import pytest
from cachevoice.cache.metadata import CacheMetadataDB


@pytest.fixture
def db(tmp_path):
    return CacheMetadataDB(str(tmp_path / "test.db"))


def test_add_and_get(db):
    db.add_entry("Bakıyorum", "bakiyorum", "Decent_Boy", "/tmp/audio.mp3")
    entries = db.get_all_entries()
    assert len(entries) == 1
    assert entries[0]["text_normalized"] == "bakiyorum"


def test_record_hit(db):
    db.add_entry("test", "test", "v", "/tmp/a.mp3")
    db.record_hit("test", "v")
    stats = db.get_stats()
    assert stats["total_hits"] == 1


def test_stats(db):
    db.add_entry("a", "a", "v", "/tmp/1.mp3", file_size=1000)
    db.add_entry("b", "b", "v", "/tmp/2.mp3", file_size=2000, is_filler=True)
    stats = db.get_stats()
    assert stats["total_entries"] == 2
    assert stats["total_size_bytes"] == 3000
    assert stats["filler_count"] == 1


def test_delete_all(db):
    db.add_entry("a", "a", "v", "/tmp/1.mp3")
    db.add_entry("b", "b", "v", "/tmp/2.mp3")
    paths = db.delete_all()
    assert len(paths) == 2
    assert db.get_stats()["total_entries"] == 0
