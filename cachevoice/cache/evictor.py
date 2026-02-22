"""Cache eviction (LRU, max size) — protects fillers."""
from __future__ import annotations
import os
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .hot import HotCache

from .metadata import CacheMetadataDB

logger = logging.getLogger("cachevoice.evictor")


class CacheEvictor:
    def __init__(self, db: CacheMetadataDB, max_entries: int = 50000,
                 max_size_mb: int = 500, min_age_days: int = 7,
                 hot_cache: HotCache | None = None):
        self._db = db
        self._max_entries = max_entries
        self._max_size_mb = max_size_mb
        self._min_age_days = min_age_days
        self._hot_cache = hot_cache

    def run(self) -> int:
        candidates = self._db.get_eviction_candidates(self._max_entries, self._min_age_days)
        removed = 0
        for entry in candidates:
            audio_path = self._db.delete_entry(entry["id"])  # pyright: ignore[reportArgumentType]
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
            if self._hot_cache:
                self._hot_cache.remove(str(entry["text_normalized"]), str(entry["voice_id"]))
            removed += 1
        if removed:
            logger.info("Evicted %d cache entries", removed)
        return removed
