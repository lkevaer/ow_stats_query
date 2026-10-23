from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("ow_api_server.metrics")

# ---------------------------------------------------------------------------
# 轻量级 Prometheus 指标（零依赖）
# 兼容 exposition formats 0.0.4：在 /metrics 暴露纯文本格式
# ---------------------------------------------------------------------------


class _Counter:
    __slots__ = ("_value", "_labels")

    def __init__(self, labels: Dict[str, str]) -> None:
        self._value = 0.0
        self._labels = labels

    def inc(self, delta: float = 1.0) -> None:
        if delta >= 0:
            self._value += delta

    def value(self) -> float:
        return self._value

    def labels(self) -> Dict[str, str]:
        return self._labels


class _Gauge:
    __slots__ = ("_value", "_labels")

    def __init__(self, labels: Dict[str, str]) -> None:
        self._value = 0.0
        self._labels = labels

    def set(self, v: float) -> None:
        self._value = float(v)

    def inc(self, delta: float = 1.0) -> None:
        self._value += delta

    def dec(self, delta: float = 1.0) -> None:
        self._value -= delta

    def value(self) -> float:
        return self._value

    def labels(self) -> Dict[str, str]:
        return self._labels


_DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)


class _Histogram:
    __slots__ = ("_buckets", "_bucket_counts", "_sum", "_count", "_labels")

    def __init__(self, labels: Dict[str, str]) -> None:
        self._buckets = _DEFAULT_BUCKETS
        self._bucket_counts = [0] * (len(self._buckets) + 1)
        self._sum = 0.0
        self._count = 0
        self._labels = labels

    def observe(self, value: float) -> None:
        v = max(0.0, float(value))
        self._sum += v
        self._count += 1
        for i, upper in enumerate(self._buckets):
            if v <= upper:
                self._bucket_counts[i] += 1
                return
        self._bucket_counts[-1] += 1

    def labels(self) -> Dict[str, str]:
        return self._labels

    def cumulative_counts(self):
        cum = 0
        out = []
        for i, upper in enumerate(self._buckets):
            cum += self._bucket_counts[i]
            out.append((upper, cum))
        cum += self._bucket_counts[-1]
        out.append((float("inf"), cum))
        return out

    def sum(self) -> float:
        return self._sum

    def count(self) -> int:
        return self._count


class MetricsRegistry:
    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._counters = {}
        self._gauges = {}
        self._histograms = {}
        self._meta = {}

    def register_counter(self, name: str, help_text: str) -> None:
        with self._lock:
            self._meta.setdefault(name, {"help": help_text, "type": "counter"})

    def register_gauge(self, name: str, help_text: str) -> None:
        with self._lock:
            self._meta.setdefault(name, {"help": help_text, "type": "gauge"})

    def register_histogram(self, name: str, help_text: str) -> None:
        with self._lock:
            self._meta.setdefault(name, {"help": help_text, "type": "histogram"})

    def counter(self, name: str, labels: Optional[Dict[str, str]] = None) -> _Counter:
        key = (name, self._label_key(labels or {}))
        with self._lock:
            c = self._counters.get(key)
            if c is None:
                c = _Counter(labels or {})
                self._counters[key] = c
            return c

    def gauge(self, name: str, labels: Optional[Dict[str, str]] = None) -> _Gauge:
        key = (name, self._label_key(labels or {}))
        with self._lock:
            g = self._gauges.get(key)
            if g is None:
                g = _Gauge(labels or {})
                self._gauges[key] = g
            return g

    def histogram(self, name: str, labels: Optional[Dict[str, str]] = None) -> _Histogram:
        key = (name, self._label_key(labels or {}))
        with self._lock:
            h = self._histograms.get(key)
            if h is None:
                h = _Histogram(labels or {})
                self._histograms[key] = h
            return h

    def render_text(self) -> str:
        lines = []
        with self._lock:
            for metric_name in sorted(self._meta.keys()):
                meta = self._meta[metric_name]
                lines.append("# HELP {0} {1}".format(metric_name, meta["help"]))
                lines.append("# TYPE {0} {1}".format(metric_name, meta["type"]))
                if meta["type"] == "counter":
                    for key in sorted(self._counters.keys()):
                        if key[0] != metric_name:
                            continue
                        c = self._counters[key]
                        lines.append(self._render_sample(metric_name, c.labels(), c.value()))
                elif meta["type"] == "gauge":
                    for key in sorted(self._gauges.keys()):
                        if key[0] != metric_name:
                            continue
                        g = self._gauges[key]
                        lines.append(self._render_sample(metric_name, g.labels(), g.value()))
                elif meta["type"] == "histogram":
                    for key in sorted(self._histograms.keys()):
                        if key[0] != metric_name:
                            continue
                        h = self._histograms[key]
                        for upper, cum in h.cumulative_counts():
                            bucket_labels = dict(h.labels())
                            bucket_labels["le"] = "+Inf" if upper == float("inf") else str(upper)
                            lines.append(self._render_sample(metric_name + "_bucket", bucket_labels, cum))
                        lines.append(self._render_sample(metric_name + "_sum", h.labels(), h.sum()))
                        lines.append(self._render_sample(metric_name + "_count", h.labels(), h.count()))
        return "\n".join(lines) + ("\n" if lines else "")

    @staticmethod
    def _label_key(labels: Dict[str, str]) -> str:
        return ",".join("{0}={1}".format(k, labels[k]) for k in sorted(labels.keys()))

    @staticmethod
    def _render_sample(name: str, labels: Dict[str, str], value: float) -> str:
        v = "{0}".format(value)
        if not labels:
            return "{0} {1}".format(name, v)
        parts = ",".join('{0}="{1}"'.format(k, MetricsRegistry._escape(v)) for k, v in sorted(labels.items()))
        return "{0}{{{1}}} {2}".format(name, parts, v)

    @staticmethod
    def _escape(s: Any) -> str:
        return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------
REGISTRY = MetricsRegistry()
REGISTRY.register_counter("http_requests_total", "Total HTTP requests processed by the API server")
REGISTRY.register_histogram("http_request_duration_seconds", "HTTP request latency in seconds")
REGISTRY.register_gauge("http_requests_in_flight", "Current HTTP requests being processed")
REGISTRY.register_counter("http_errors_total", "Total HTTP error responses (4xx/5xx)")
REGISTRY.register_counter("cache_lookup_total", "Total cache lookups (hits + misses) by cache name")
REGISTRY.register_counter("cache_hit_total", "Total cache hits by cache name")
REGISTRY.register_gauge("cache_entries", "Current number of entries per cache")
REGISTRY.register_counter("rate_limit_throttled_total", "Total requests throttled by per-IP / per-key rate limit")


def render_metrics_text() -> str:
    """刷新 cache_entries/gauge 后生成 Prometheus 文本。"""
    try:
        from .cache import list_cache_stats as _list_stats

        stats = _list_stats()
        for cache_name, data in stats.items():
            hits = int(data.get("hits", 0))
            misses = int(data.get("misses", 0))
            REGISTRY.gauge("cache_entries", {"cache": cache_name}).set(int(data.get("entries", 0)))
            REGISTRY.counter("cache_hit_total", {"cache": cache_name}).inc(hits)
            REGISTRY.counter("cache_lookup_total", {"cache": cache_name}).inc(hits + misses)
    except Exception:
        pass

    return REGISTRY.render_text()


__all__ = ["REGISTRY", "render_metrics_text"]
