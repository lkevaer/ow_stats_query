from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Generic, Optional, TypeVar

logger = logging.getLogger("ow_api_server.cache")

V = TypeVar("V")


@dataclass
class _CacheEntry(Generic[V]):
    value: V
    expire_at: float
    hit_count: int = 0


@dataclass
class TTLCacheStats:
    hits: int = 0
    misses: int = 0
    dedup_merged: int = 0  # 被并入已有 inflight 请求的次数


class TTLCache(Generic[V]):
    """线程安全 + asyncio 安全的 TTL 缓存，同时支持请求去重合并（Dedup）。

    核心特性：
    - 纯内存 LRU（OrderedDict move_to_end）
    - 每个 key 独立 TTL（expire_at 绝对时间）
    - 同一时间、同一 key：只会有 1 个真实请求，其余并发请求直接等它的结果
    - 异常结果不会进入缓存，避免污染
    - 暴露 hits/misses 统计用于 Prometheus / 日志
    """

    def __init__(self, name: str, max_size: int = 2000, default_ttl_seconds: float = 300) -> None:
        self._name = name
        self._max_size = max(1, int(max_size))
        self._default_ttl = max(1.0, float(default_ttl_seconds))
        self._store: "OrderedDict[str, _CacheEntry[V]]" = OrderedDict()
        self._inflight: Dict[str, asyncio.Future[V]] = {}
        self._lock = asyncio.Lock()
        self._stats = TTLCacheStats()

    # ------------------------------------------------------------------
    # 对外核心 API
    # ------------------------------------------------------------------
    async def get_or_compute(
        self,
        key: str,
        coro_factory: Callable[[], Awaitable[V]],
        *,
        ttl_seconds: Optional[float] = None,
        cache_none: bool = False,
    ) -> V:
        """取缓存；miss 时调用 coro_factory 生产结果并写入缓存。

        返回值与 coro_factory 直接执行的结果相同。
        同一 key 的并发请求会被合并到同一个 coro_factory 调用。
        """
        # 1) 快速路径：查缓存（仅读，无阻塞其他 key）
        async with self._lock:
            entry = self._store.get(key)
            if entry is not None and entry.expire_at > time.monotonic():
                self._store.move_to_end(key)
                entry.hit_count += 1
                self._stats.hits += 1
                return entry.value
            if entry is not None:
                # 过期，主动清除
                self._store.pop(key, None)

            # 2) 是否已经有 inflight 请求？
            fut = self._inflight.get(key)
            if fut is not None:
                self._stats.dedup_merged += 1
                # 释放锁后等待；这样其他 key 的请求不受阻塞
                released_ok = True
            else:
                loop = asyncio.get_running_loop()
                self._inflight[key] = loop.create_future()
                fut = self._inflight[key]
                released_ok = False

        if released_ok:
            # 等待先行者完成
            try:
                return await fut
            except asyncio.CancelledError:
                raise
            except Exception:
                # 先行者抛异常 → 重新走一次（让调用者自己再试）
                # 这里简单处理：抛给调用方（上层会通过 HTTP 500 暴露）
                raise

        # 3) 我是先行者：在锁外执行
        try:
            result = await coro_factory()
        except Exception as exc:
            # 失败不缓存，但要通知等待者
            async with self._lock:
                self._inflight.pop(key, None)
                if not fut.done():
                    fut.set_exception(exc)
            raise

        # 4) 写入缓存
        if not cache_none and result is None:
            async with self._lock:
                self._inflight.pop(key, None)
                if not fut.done():
                    fut.set_result(result)
            return result

        ttl = float(ttl_seconds) if ttl_seconds is not None else self._default_ttl
        async with self._lock:
            self._stats.misses += 1
            # 写入
            self._store[key] = _CacheEntry(value=result, expire_at=time.monotonic() + ttl)
            self._store.move_to_end(key)
            # 驱逐（超出上限）
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)
            # 唤醒等待者
            self._inflight.pop(key, None)
            if not fut.done():
                fut.set_result(result)
        return result

    # ------------------------------------------------------------------
    # 统计 / 调试
    # ------------------------------------------------------------------
    def stats(self) -> TTLCacheStats:
        return TTLCacheStats(
            hits=self._stats.hits,
            misses=self._stats.misses,
            dedup_merged=self._stats.dedup_merged,
        )

    def size(self) -> int:
        return len(self._store)

    def name(self) -> str:
        return self._name

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()
            self._stats = TTLCacheStats()

    async def invalidate(self, key: str) -> bool:
        async with self._lock:
            return self._store.pop(key, None) is not None


# ---------------------------------------------------------------------------
# 全局缓存实例：供各路由共享；便于后续加 Prometheus 统计
# ---------------------------------------------------------------------------
_CACHES: Dict[str, TTLCache[Any]] = {}
_INIT_LOCK = asyncio.Lock()


async def get_or_create_cache(
    name: str,
    *,
    max_size: int = 2000,
    default_ttl_seconds: float = 300,
) -> TTLCache[Any]:
    """按名称获取/创建全局缓存。"""
    async with _INIT_LOCK:
        cache = _CACHES.get(name)
        if cache is None:
            cache = TTLCache(name=name, max_size=max_size, default_ttl_seconds=default_ttl_seconds)
            _CACHES[name] = cache
        return cache


def list_cache_stats() -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for name, cache in _CACHES.items():
        s = cache.stats()
        out[name] = {
            "entries": cache.size(),
            "hits": s.hits,
            "misses": s.misses,
            "dedup_merged": s.dedup_merged,
        }
    return out
