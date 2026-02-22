"""In-memory hot cache (dict) — loaded from SQLite at startup."""
from __future__ import annotations
from collections import defaultdict
from typing import Optional, Callable, Any
from rapidfuzz import process, fuzz

SCORERS: dict[str, Callable[..., Any]] = {
    "token_sort_ratio": fuzz.token_sort_ratio,
    "ratio": fuzz.ratio,
    "partial_ratio": fuzz.partial_ratio,
    "WRatio": fuzz.WRatio,
}


class HotCache:
    def __init__(self):
        # Voice bucketing: voice_id -> normalized_text -> [audio_paths]
        self._buckets: dict[str, dict[str, list[str]]] = defaultdict(lambda: dict[str, list[str]]())

    def load_entries(self, entries: list[dict[str, str]]):
        for e in entries:
            vid = e['voice_id']
            norm = e['text_normalized']
            path = e['audio_path']
            bucket = self._buckets[vid]
            if norm not in bucket:
                bucket[norm] = []
            if path not in bucket[norm]:
                bucket[norm].append(path)

    def exact_lookup(self, normalized_text: str, voice_id: str) -> Optional[str]:
        bucket = self._buckets.get(voice_id)
        if not bucket:
            return None
        paths = bucket.get(normalized_text)
        return paths[0] if paths else None

    def fuzzy_lookup(
        self, normalized_text: str, voice_id: str,
        threshold: int = 90, scorer: str = "token_sort_ratio",
    ) -> Optional[tuple[str, str, float]]:
        bucket = self._buckets.get(voice_id)
        if not bucket:
            return None
        candidates = list(bucket.keys())
        if not candidates:
            return None
        scorer_fn = SCORERS.get(scorer, fuzz.token_sort_ratio)
        match = process.extractOne(
            normalized_text, candidates,
            scorer=scorer_fn, score_cutoff=threshold,
        )
        if match:
            matched_text, score, _ = match
            paths = bucket[matched_text]
            return (matched_text, paths[0], score)
        return None

    def get_paths(self, normalized_text: str, voice_id: str) -> list[str]:
        bucket = self._buckets.get(voice_id)
        if not bucket:
            return []
        return list(bucket.get(normalized_text, []))

    def add(self, normalized_text: str, voice_id: str, audio_path: str):
        bucket = self._buckets[voice_id]
        if normalized_text not in bucket:
            bucket[normalized_text] = []
        paths = bucket[normalized_text]
        if audio_path not in paths:
            paths.append(audio_path)

    def remove(self, normalized_text: str, voice_id: str):
        bucket = self._buckets.get(voice_id)
        if bucket:
            bucket.pop(normalized_text, None)

    def clear(self):
        self._buckets.clear()

    @property
    def size(self) -> int:
        return sum(len(b) for b in self._buckets.values())
