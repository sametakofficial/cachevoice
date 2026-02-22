import pytest
import sqlite3
from cachevoice.cache.metadata import CacheMetadataDB, CURRENT_SCHEMA_VERSION


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


def test_schema_version_tracked(db):
    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION


def test_unique_constraint_same_version_rejects_duplicate(db):
    db.add_entry("hello", "hello", "voice1", "/tmp/a.mp3", version_num=1)
    with pytest.raises(sqlite3.IntegrityError):
        db.add_entry("hello", "hello", "voice1", "/tmp/b.mp3", version_num=1)


def test_unique_constraint_different_version_allowed(db):
    db.add_entry("hello", "hello", "voice1", "/tmp/a.mp3", version_num=1)
    db.add_entry("hello", "hello", "voice1", "/tmp/b.mp3", version_num=2)
    assert db.get_version_count("hello", "voice1") == 2


def test_unique_constraint_different_voice_allowed(db):
    db.add_entry("hello", "hello", "voice1", "/tmp/a.mp3", version_num=1)
    db.add_entry("hello", "hello", "voice2", "/tmp/b.mp3", version_num=1)
    entries = db.get_all_entries()
    assert len(entries) == 2


def test_get_version_count(db):
    assert db.get_version_count("hello", "voice1") == 0
    db.add_entry("hello", "hello", "voice1", "/tmp/a.mp3", version_num=1)
    assert db.get_version_count("hello", "voice1") == 1
    db.add_entry("hello", "hello", "voice1", "/tmp/b.mp3", version_num=2)
    db.add_entry("hello", "hello", "voice1", "/tmp/c.mp3", version_num=3)
    assert db.get_version_count("hello", "voice1") == 3


def test_record_hit_specific_version(db):
    db.add_entry("test", "test", "v", "/tmp/a.mp3", version_num=1)
    db.add_entry("test", "test", "v", "/tmp/b.mp3", version_num=2)
    db.record_hit("test", "v", version_num=1)
    conn = sqlite3.connect(db._db_path)
    conn.row_factory = sqlite3.Row
    r1 = conn.execute(
        "SELECT hit_count FROM cache_entries WHERE version_num = 1"
    ).fetchone()
    r2 = conn.execute(
        "SELECT hit_count FROM cache_entries WHERE version_num = 2"
    ).fetchone()
    conn.close()
    assert r1["hit_count"] == 1
    assert r2["hit_count"] == 0


def test_version_num_defaults_to_1(db):
    db.add_entry("hello", "hello", "voice1", "/tmp/a.mp3")
    entries = db.get_all_entries()
    assert entries[0]["version_num"] == 1


def _create_v1_db(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE cache_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text_original TEXT NOT NULL,
            text_normalized TEXT NOT NULL,
            voice_id TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            audio_path TEXT NOT NULL,
            audio_format TEXT DEFAULT 'mp3',
            file_size INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            hit_count INTEGER DEFAULT 0,
            is_filler BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_hit_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def test_migration_adds_version_num(tmp_path):
    db_path = str(tmp_path / "migrate.db")
    _create_v1_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO cache_entries (text_original, text_normalized, voice_id, audio_path) VALUES (?, ?, ?, ?)",
        ("hello", "hello", "v1", "/tmp/a.mp3")
    )
    conn.commit()
    conn.close()

    db = CacheMetadataDB(db_path)
    entries = db.get_all_entries()
    assert len(entries) == 1
    assert entries[0]["version_num"] == 1
    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION


def test_migration_deduplicates_keeps_highest_hit_count(tmp_path):
    db_path = str(tmp_path / "dedup.db")
    _create_v1_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO cache_entries (text_original, text_normalized, voice_id, audio_path, hit_count) VALUES (?, ?, ?, ?, ?)",
        ("hello", "hello", "v1", "/tmp/low.mp3", 2)
    )
    conn.execute(
        "INSERT INTO cache_entries (text_original, text_normalized, voice_id, audio_path, hit_count) VALUES (?, ?, ?, ?, ?)",
        ("hello", "hello", "v1", "/tmp/high.mp3", 10)
    )
    conn.execute(
        "INSERT INTO cache_entries (text_original, text_normalized, voice_id, audio_path, hit_count) VALUES (?, ?, ?, ?, ?)",
        ("hello", "hello", "v1", "/tmp/mid.mp3", 5)
    )
    conn.commit()
    conn.close()

    db = CacheMetadataDB(db_path)
    entries = db.get_all_entries()
    assert len(entries) == 1
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT audio_path, hit_count FROM cache_entries").fetchone()
    conn.close()
    assert row["hit_count"] == 10
    assert row["audio_path"] == "/tmp/high.mp3"
