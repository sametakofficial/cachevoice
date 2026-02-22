"""FuzzyCacheStorage — main cache interface combining hot cache + DB."""
from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from .hot import HotCache
from .matcher import FuzzyMatcher
from .normalizer import normalize

if TYPE_CHECKING:
    from ..config import FuzzyConfig, NormalizeConfig
    from .metadata import CacheMetadataDB


class FuzzyCacheStorage:
    def __init__(self, audio_dir: str, fuzzy_config: FuzzyConfig | None = None,
                 normalize_config: NormalizeConfig | None = None,
                 metadata_db: CacheMetadataDB | None = None,
                 variety_depth: int = 1):
        self._audio_dir = Path(audio_dir)
        self._audio_dir.mkdir(parents=True, exist_ok=True)
        self._variety_depth = max(1, variety_depth)
        self._db = metadata_db
        self._hot = HotCache(variety_depth=self._variety_depth)
        self._matcher = FuzzyMatcher(self._hot, fuzzy_config)
        self._normalize_config = normalize_config

    @property
    def hot_cache(self) -> HotCache:
        return self._hot

    @property
    def matcher(self) -> FuzzyMatcher:
        return self._matcher

    def lookup(self, text: str, voice_id: str) -> Optional[dict[str, object]]:
        return self._matcher.find(text, voice_id)

    def store(
        self,
        text: str,
        voice_id: str,
        audio_data: bytes,
        audio_format: str = "mp3",
        version_num: int | None = None,
    ) -> str:
        normalized = normalize(text, self._normalize_config)
        if version_num is None and self._db is not None:
            existing_versions = self._db.get_version_count(normalized, voice_id)
            version_num = min(existing_versions + 1, self._variety_depth)
        if version_num is None:
            version_num = 1

        filename = self._make_filename(normalized, voice_id, audio_format, version_num)
        filepath = self._audio_dir / filename
        filepath.write_bytes(audio_data)
        self._hot.add(normalized, voice_id, str(filepath))

        if self._db is not None:
            self._db.add_entry(
                text_original=text,
                text_normalized=normalized,
                voice_id=voice_id,
                audio_path=str(filepath),
                audio_format=audio_format,
                file_size=len(audio_data),
                version_num=version_num,
            )
        return str(filepath)

    def _make_filename(self, normalized_text: str, voice_id: str, fmt: str, version_num: int = 1) -> str:
        if version_num <= 1:
            key = f"{normalized_text}:{voice_id}:{fmt}"
        else:
            key = f"{normalized_text}:{voice_id}:{fmt}:{version_num}"
        h = hashlib.md5(key.encode()).hexdigest()[:16]
        return f"{h}.{fmt}"

    def clear(self):
        self._hot.clear()

    @property
    def size(self) -> int:
        return self._hot.size
