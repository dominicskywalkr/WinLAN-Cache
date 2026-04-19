from __future__ import annotations

import threading
from collections import Counter


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[str] = Counter()
        self._bytes_saved = 0
        self._bytes_served = 0
        self._platform_hits: Counter[str] = Counter()
        self._platform_misses: Counter[str] = Counter()

    def increment(self, key: str, value: int = 1) -> None:
        with self._lock:
            self._counters[key] += value

    def record_hit(self, platform: str, bytes_saved: int) -> None:
        with self._lock:
            self._counters["cache_hits"] += 1
            self._platform_hits[platform] += 1
            self._bytes_saved += max(bytes_saved, 0)

    def record_miss(self, platform: str) -> None:
        with self._lock:
            self._counters["cache_misses"] += 1
            self._platform_misses[platform] += 1

    def record_bytes_served(self, size: int) -> None:
        with self._lock:
            self._bytes_served += max(size, 0)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "bytes_saved": self._bytes_saved,
                "bytes_served": self._bytes_served,
                "platform_hits": dict(self._platform_hits),
                "platform_misses": dict(self._platform_misses),
            }