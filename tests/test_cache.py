import pytest
from cachevoice.cache.store import FuzzyCacheStorage


@pytest.fixture
def store(tmp_path):
    return FuzzyCacheStorage(str(tmp_path / "audio"))


def test_exact_hit(store):
    store.store("Bakıyorum", "Decent_Boy", b"fake_audio")
    result = store.lookup("Bakıyorum", "Decent_Boy")
    assert result is not None
    assert result["match_type"] == "exact"
    assert result["score"] == 100


def test_miss(store):
    result = store.lookup("tamamen farklı bir cümle", "Decent_Boy")
    assert result is None


def test_case_insensitive(store):
    store.store("ARAŞTIRIYORUM", "Decent_Boy", b"fake_audio")
    result = store.lookup("araştırıyorum", "Decent_Boy")
    assert result is not None
    assert result["match_type"] == "exact"


def test_number_normalization(store):
    store.store("3 kaynak buldum", "Decent_Boy", b"fake_audio")
    result = store.lookup("5 kaynak buldum", "Decent_Boy")
    assert result is not None
    assert result["match_type"] == "exact"


def test_clear(store):
    store.store("test", "v", b"data")
    assert store.size == 1
    store.clear()
    assert store.size == 0
