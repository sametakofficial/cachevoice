import pytest
from cachevoice.cache.store import FuzzyCacheStorage
from cachevoice.cache.metadata import CacheMetadataDB
from cachevoice.cache.evictor import CacheEvictor
from cachevoice.cache.normalizer import normalize
from cachevoice.cache.hot import HotCache
from cachevoice.config import FuzzyConfig
from pathlib import Path


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


def test_eviction_syncs_hot_cache(tmp_path):
    audio_dir = tmp_path / "audio"
    db_path = str(tmp_path / "cache.db")

    store = FuzzyCacheStorage(str(audio_dir))
    db = CacheMetadataDB(db_path)

    text = "merhaba dünya"
    voice = "Decent_Boy"
    normalized = normalize(text)
    audio_path = store.store(text, voice, b"fake_audio_data")
    db.add_entry(
        text_original=text, text_normalized=normalized, voice_id=voice,
        audio_path=audio_path, model="tts-1", audio_format="mp3",
        file_size=len(b"fake_audio_data"),
    )

    assert store.lookup(text, voice) is not None
    assert Path(audio_path).exists()

    evictor = CacheEvictor(db, max_entries=0, hot_cache=store.hot_cache)
    removed = evictor.run()
    assert removed == 1

    assert not Path(audio_path).exists()
    assert store.lookup(text, voice) is None


def test_fuzzy_disabled_by_default(tmp_path):
    store = FuzzyCacheStorage(str(tmp_path / "audio"))
    store.store("merhaba dünya", "v1", b"audio")
    result = store.lookup("merhaba dunya guzel", "v1")
    assert result is None


def test_fuzzy_enabled_via_config(tmp_path):
    cfg = FuzzyConfig(enabled=True, threshold=60)
    store = FuzzyCacheStorage(str(tmp_path / "audio"), fuzzy_config=cfg)
    store.store("merhaba dünya", "v1", b"audio")
    result = store.lookup("merhaba dunya guzel", "v1")
    assert result is not None
    assert result["match_type"] == "fuzzy"


def test_voice_bucketing():
    hc = HotCache()
    hc.add("hello", "voice_a", "/a/hello.mp3")
    hc.add("hello", "voice_b", "/b/hello.mp3")

    assert hc.exact_lookup("hello", "voice_a") == "/a/hello.mp3"
    assert hc.exact_lookup("hello", "voice_b") == "/b/hello.mp3"
    assert hc.exact_lookup("hello", "voice_c") is None


def test_voice_bucketing_variety_depth():
    hc = HotCache(variety_depth=4)
    hc.add("hello", "v1", "/v1/hello_1.mp3")
    hc.add("hello", "v1", "/v1/hello_2.mp3")
    hc.add("hello", "v1", "/v1/hello_1.mp3")

    paths = hc.get_paths("hello", "v1")
    assert paths == ["/v1/hello_1.mp3", "/v1/hello_2.mp3"]
    assert hc.size == 1


def test_voice_bucketing_variety_depth_one_keeps_single_path():
    hc = HotCache(variety_depth=1)
    hc.add("hello", "v1", "/v1/hello_1.mp3")
    hc.add("hello", "v1", "/v1/hello_2.mp3")

    assert hc.get_paths("hello", "v1") == ["/v1/hello_1.mp3"]


def test_exact_lookup_random_choice(monkeypatch):
    hc = HotCache(variety_depth=4)
    hc.add("hello", "v1", "/v1/hello_1.mp3")
    hc.add("hello", "v1", "/v1/hello_2.mp3")

    monkeypatch.setattr("cachevoice.cache.hot.random.choice", lambda paths: paths[-1])
    assert hc.exact_lookup("hello", "v1") == "/v1/hello_2.mp3"


def test_store_uses_versioning_with_variety_depth(tmp_path):
    audio_dir = tmp_path / "audio"
    db = CacheMetadataDB(str(tmp_path / "cache.db"))
    store = FuzzyCacheStorage(str(audio_dir), metadata_db=db, variety_depth=4)
    normalized = normalize("merhaba dünya")

    first = store.store("merhaba dünya", "v1", b"a")
    second = store.store("merhaba dünya", "v1", b"b")

    assert first != second
    assert db.get_version_count(normalized, "v1") == 2
    assert len(store.hot_cache.get_paths(normalized, "v1")) == 2


def test_voice_bucketing_size():
    hc = HotCache()
    hc.add("a", "v1", "/1.mp3")
    hc.add("b", "v1", "/2.mp3")
    hc.add("a", "v2", "/3.mp3")
    assert hc.size == 3
