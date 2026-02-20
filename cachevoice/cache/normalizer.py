"""Text normalization pipeline for cache key generation."""
import re

TURKISH_LOWER_MAP = str.maketrans('Iİ', 'ıi')
DIACRITIC_MAP = str.maketrans('çğıöşü', 'cgiosu')


def turkish_lower(text: str) -> str:
    """Turkish-aware lowercase. Python's str.lower() handles İ/I incorrectly."""
    return text.translate(TURKISH_LOWER_MAP).lower()


def normalize(text: str) -> str:
    """Full normalization pipeline for cache key generation."""
    text = text.strip()
    if not text:
        return ""
    text = turkish_lower(text)
    text = text.translate(DIACRITIC_MAP)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\d+', '#', text)
    return text.strip()
