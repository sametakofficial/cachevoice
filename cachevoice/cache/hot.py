"""In-memory hot cache (dict) — loaded from SQLite at startup."""
from __future__ import annotations
from typing import Optional
from rapidfuzz import process, fuzz


class HotCache:
    def __init__(self):
        self._exact: dict[str, str] = {}
        self._texts: list[str] = []
        self._text_to_path: dict[str, str] = {}

    def load_entries(self, entries: list[dict]):
        for e in entries:
            key = f"{e['text_normalized']}:{e['voice_id']}"
            self._exact[key] = e['audio_path']
            if e['text_normalized'] not in self._text_to_path:
                self._texts.append(e['text_normalized'])
            self._text_to_path[e['text_normalized']] = e['audio_path']

    def exact_lookup(self, normalized_text: str, voice_id: str) -> Optional[str]:
        return self._exact.get(f"{normalized_text}:{voice_id}")

    def fuzzy_lookup(self, normalized_text: str, voice_id: str, threshold: int = 90) -> Optional[tuple[str, str, float]]:
        # Filter candidates to only texts that exist for this voice_id
        candidates = [text for text in self._texts if f"{text}:{voice_id}" in self._exact]
        if not candidates:
            return None
        match = process.extractOne(
            normalized_text, candidates,
            scorer=fuzz.token_sort_ratio, score_cutoff=threshold,
        )
        if match:
            matched_text, score, _ = match
            return (matched_text, self._exact[f"{matched_text}:{voice_id}"], score)
        return None

    def add(self, normalized_text: str, voice_id: str, audio_path: str):
        key = f"{normalized_text}:{voice_id}"
        self._exact[key] = audio_path
        if normalized_text not in self._text_to_path:
            self._texts.append(normalized_text)
        self._text_to_path[normalized_text] = audio_path

    def remove(self, normalized_text: str, voice_id: str):
        self._exact.pop(f"{normalized_text}:{voice_id}", None)

    def clear(self):
        self._exact.clear()
        self._texts.clear()
        self._text_to_path.clear()

    @property
    def size(self) -> int:
        return len(self._exact)
