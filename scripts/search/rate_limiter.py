#!/usr/bin/env python3
"""全局限流器 — 按数据源维护请求间隔。"""
from __future__ import annotations

import threading
import time
from typing import Dict


class RateLimiter:
    """简单的令牌桶限流器。"""

    def __init__(self):
        self._locks: Dict[str, threading.Lock] = {}
        self._last_request: Dict[str, float] = {}
        self._intervals: Dict[str, float] = {
            "openalex": 0.1,
            "arxiv": 3.0,
            "s2": 1.1,
            "dblp": 0.5,
            "pmc": 0.4,
            "crossref": 0.05,
            "core": 1.0,
        }

    def wait(self, source: str):
        """阻塞直到可以发送下一个请求。"""
        if source not in self._locks:
            self._locks[source] = threading.Lock()
        with self._locks[source]:
            interval = self._intervals.get(source, 1.0)
            last = self._last_request.get(source, 0)
            elapsed = time.time() - last
            if elapsed < interval:
                time.sleep(interval - elapsed)
            self._last_request[source] = time.time()

    def set_interval(self, source: str, interval: float):
        self._intervals[source] = interval


# 全局单例
_limiter = RateLimiter()


def rate_limit(source: str):
    """便捷函数: 阻塞等待限流。"""
    _limiter.wait(source)


if __name__ == "__main__":
    print("限流器测试:")
    for i in range(3):
        rate_limit("s2")
        print(f"  S2 请求 {i+1}: {time.strftime('%H:%M:%S')}")
