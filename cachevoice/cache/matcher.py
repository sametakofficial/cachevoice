"""FuzzyMatcher — rapidfuzz-based cache matching."""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING
from .normalizer import normalize
from .hot import HotCache

if TYPE_CHECKING:
    from ..config import FuzzyConfig


class FuzzyMatcher:
    def __init__(self, hot_cache: HotCache, fuzzy_config: FuzzyConfig | None = None):
        self._hot = hot_cache
        if fuzzy_config is None:
            from ..config import FuzzyConfig
            fuzzy_config = FuzzyConfig()
        self._fuzzy_enabled = fuzzy_config.enabled
        self._threshold = fuzzy_config.threshold
        self._scorer = fuzzy_config.scorer

    def find(self, text: str, voice_id: str) -> Optional[dict[str, object]]:
        normalized = normalize(text)
        if not normalized:
            return None
        path = self._hot.exact_lookup(normalized, voice_id)
        if path:
            return {"audio_path": path, "match_type": "exact", "score": 100, "normalized": normalized}
        if not self._fuzzy_enabled:
            return None
        result = self._hot.fuzzy_lookup(normalized, voice_id, self._threshold, self._scorer)
        if result:
            matched_text, path, score = result
            return {"audio_path": path, "match_type": "fuzzy", "score": score, "normalized": normalized, "matched": matched_text}
        return None
