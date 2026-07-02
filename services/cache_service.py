"""Local disk caching for PubMed and other API responses."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Optional

import diskcache

_DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
_DEFAULT_TTL = 7 * 24 * 3600  # 7 days


class CacheService:
    """Wraps diskcache for keyed storage with TTL."""

    def __init__(self, cache_dir: Optional[Path] = None, ttl: int = _DEFAULT_TTL):
        self.cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl
        self._cache = diskcache.Cache(str(self.cache_dir))

    @staticmethod
    def _make_key(namespace: str, value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        return f"{namespace}:{digest}"

    def get(self, namespace: str, key: str) -> Optional[Any]:
        """Retrieve a cached value or None."""
        return self._cache.get(self._make_key(namespace, key))

    def set(self, namespace: str, key: str, value: Any) -> None:
        """Store a value with TTL."""
        self._cache.set(self._make_key(namespace, key), value, expire=self.ttl)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()


_cache_instance: Optional[CacheService] = None


def get_cache() -> CacheService:
    """Return singleton cache instance."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = CacheService()
    return _cache_instance
