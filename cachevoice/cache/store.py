"""FuzzyCacheStorage — main cache interface combining hot cache + DB."""
from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from .hot import HotCache
from .matcher import FuzzyMatcher
from .normalizer import normalize

if TYPE_CHECKING:
    from ..config import NormalizeConfig


class FuzzyCacheStorage:
    def __init__(self, audio_dir: str, fuzzy_threshold: int = 90,
                 normalize_config: NormalizeConfig | None = None):
        self._audio_dir = Path(audio_dir)
        self._audio_dir.mkdir(parents=True, exist_ok=True)
        self._hot = HotCache()
        self._matcher = FuzzyMatcher(self._hot, fuzzy_threshold)
        self._normalize_config = normalize_config

    @property
    def hot_cache(self) -> HotCache:
        return self._hot

    @property
    def matcher(self) -> FuzzyMatcher:
        return self._matcher

    def lookup(self, text: str, voice_id: str) -> Optional[dict]:
        return self._matcher.find(text, voice_id)

    def store(self, text: str, voice_id: str, audio_data: bytes, audio_format: str = "mp3") -> str:
        normalized = normalize(text, self._normalize_config)
        filename = self._make_filename(normalized, voice_id, audio_format)
        filepath = self._audio_dir / filename
        filepath.write_bytes(audio_data)
        self._hot.add(normalized, voice_id, str(filepath))
        return str(filepath)

    def _make_filename(self, normalized_text: str, voice_id: str, fmt: str) -> str:
        h = hashlib.md5(f"{normalized_text}:{voice_id}:{fmt}".encode()).hexdigest()[:16]
        return f"{h}.{fmt}"

    def clear(self):
        self._hot.clear()

    @property
    def size(self) -> int:
        return self._hot.size
