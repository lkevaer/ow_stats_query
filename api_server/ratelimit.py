from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger("ow_api_server.ratelimit")


class SlidingWindowLimiter:
    """滑动窗口限流（令牌桶的轻量替代）。

    - window_seconds 窗口长度
    - max_requests 窗口内允许的最大请求数
    """

    def __init__(self, max_requests: int, window_seconds: float, *, clock=None) -> None:
        self._max = max(1, int(max_requests))
        self._window = max(0.1, float(window_seconds))
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_cleanup = 0.0
        self._clock = clock or time.monotonic

    def allow(self, key: str) -> bool:
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            q = self._buckets[key]
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self._max:
                return False
            q.append(now)
            return True

    def try_with_retry_after(self, key: str, *, consume: bool = True) -> tuple[bool, float]:
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            if now - self._last_cleanup >= self._window or len(self._buckets) >= 10_000:
                for stored in list(self._buckets):
                    bucket = self._buckets[stored]
                    while bucket and bucket[0] <= cutoff:
                        bucket.popleft()
                    if not bucket:
                        del self._buckets[stored]
                self._last_cleanup = now
            if key not in self._buckets and len(self._buckets) >= 10_000:
                return False, self._window
            q = self._buckets[key]
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self._max:
                # 估算多久后有空闲位置
                retry_after = max(0.1, (q[0] + self._window) - now)
                return False, retry_after
            if consume:
                q.append(now)
            return True, 0.0

    def restart_window(self, key: str) -> None:
        """Restart an admitted request's cooldown when its work finishes."""
        with self._lock:
            self._buckets[key] = deque([self._clock()])

    def cleanup(self) -> None:
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            remove_keys = []
            for k, q in self._buckets.items():
                while q and q[0] <= cutoff:
                    q.popleft()
                if not q:
                    remove_keys.append(k)
            for k in remove_keys:
                del self._buckets[k]


class RateLimiters:
    """聚合多套规则（per-ip / per-api-key / per-user-token）。"""

    def __init__(self) -> None:
        self._by_ip: Optional[SlidingWindowLimiter] = None
        self._by_api_key: Optional[SlidingWindowLimiter] = None
        self._by_user_token: Optional[SlidingWindowLimiter] = None
        self._lock = threading.Lock()
        self._configuration = None

    def configure(self, *, ip_max: Optional[int] = None, api_key_max: Optional[int] = None,
                  user_token_max: Optional[int] = None, window_seconds: float = 60.0) -> None:
        with self._lock:
            configuration = (ip_max or 0, api_key_max or 0, user_token_max or 0, window_seconds)
            if configuration == self._configuration:
                return
            self._by_ip = SlidingWindowLimiter(ip_max, window_seconds) if ip_max and ip_max > 0 else None
            self._by_api_key = SlidingWindowLimiter(api_key_max, window_seconds) if api_key_max and api_key_max > 0 else None
            self._by_user_token = SlidingWindowLimiter(user_token_max, window_seconds) if user_token_max and user_token_max > 0 else None
            self._configuration = configuration

    def check(self, *, ip: str, api_key: str = "", user_token: str = "") -> tuple[bool, str, float]:
        """依次检查三层限流。返回 (ok, 被触发的规则名, retry_after 秒数)。

        - 顺序：user_token > api_key > ip
        - 只要任意一层被触发，立即返回 False
        """
        if self._by_user_token is not None and user_token:
            ok, retry_after = self._by_user_token.try_with_retry_after(user_token)
            if not ok:
                return False, "user_token", retry_after
        if self._by_api_key is not None and api_key:
            ok, retry_after = self._by_api_key.try_with_retry_after(api_key)
            if not ok:
                return False, "api_key", retry_after
        if self._by_ip is not None:
            ok, retry_after = self._by_ip.try_with_retry_after(ip)
            if not ok:
                return False, "ip", retry_after
        return True, "", 0.0


# 全局单例
LIMITERS = RateLimiters()
