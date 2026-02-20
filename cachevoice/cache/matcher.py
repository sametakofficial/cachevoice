"""FuzzyMatcher — rapidfuzz-based cache matching."""
from __future__ import annotations
from typing import Optional
from .normalizer import normalize
from .hot import HotCache


class FuzzyMatcher:
    def __init__(self, hot_cache: HotCache, threshold: int = 90):
        self._hot = hot_cache
        self._threshold = threshold

    def find(self, text: str, voice_id: str) -> Optional[dict]:
        normalized = normalize(text)
        if not normalized:
            return None
        path = self._hot.exact_lookup(normalized, voice_id)
        if path:
            return {"audio_path": path, "match_type": "exact", "score": 100, "normalized": normalized}
        result = self._hot.fuzzy_lookup(normalized, voice_id, self._threshold)
        if result:
            matched_text, path, score = result
            return {"audio_path": path, "match_type": "fuzzy", "score": score, "normalized": normalized, "matched": matched_text}
        return None
