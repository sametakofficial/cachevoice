"""Text normalization pipeline for cache key generation."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import NormalizeConfig

TURKISH_LOWER_MAP = str.maketrans('Iİ', 'ıi')
DIACRITIC_MAP = str.maketrans('çğıöşü', 'cgiosu')

# MiniMax TTS syntax patterns
_MINIMAX_PAUSE_RE = re.compile(r'<#[\d.]+#>')
_MINIMAX_INTERJECTION_RE = re.compile(r'\([a-z_]+\)')


def turkish_lower(text: str) -> str:
    """Turkish-aware lowercase. Python's str.lower() handles İ/I incorrectly."""
    return text.translate(TURKISH_LOWER_MAP).lower()


def _default_config() -> NormalizeConfig:
    from ..config import NormalizeConfig
    return NormalizeConfig()


def normalize(text: str, config: NormalizeConfig | None = None) -> str:
    """Full normalization pipeline for cache key generation."""
    text = text.strip()
    if not text:
        return ""

    if config is None:
        config = _default_config()

    # MiniMax TTS syntax — strip first so markers don't leak into later steps
    if config.strip_minimax:
        text = _MINIMAX_PAUSE_RE.sub('', text)
        text = _MINIMAX_INTERJECTION_RE.sub('', text)

    if config.lowercase:
        text = turkish_lower(text)
        text = text.translate(DIACRITIC_MAP)

    if config.collapse_whitespace:
        text = re.sub(r'\s+', ' ', text)

    if config.strip_punctuation:
        text = re.sub(r'[^\w\s]', '', text)

    if config.replace_numbers:
        text = re.sub(r'\d+', '#', text)

    return text.strip()
