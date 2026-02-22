"""Integration tests — full cache flow."""
import pytest
import tempfile
import os
from pathlib import Path
from fastapi.testclient import TestClient
from cachevoice.server import app, _startup_integrity_check
from cachevoice.cache.metadata import CacheMetadataDB
from cachevoice.cache.store import FuzzyCacheStorage


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def integrity_env(tmp_path):
    db_path = str(tmp_path / "test.db")
    audio_dir = str(tmp_path / "audio")
    os.makedirs(audio_dir, exist_ok=True)
    fillers_dir = tmp_path / "audio" / "fillers"
    fillers_dir.mkdir()
    db = CacheMetadataDB(db_path)
    store = FuzzyCacheStorage(audio_dir=audio_dir)
    return db, store, audio_dir, tmp_path


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "provider_status" in data
    assert data["provider_status"] in ["available", "unavailable", "unknown"]


def test_cache_stats(client):
    resp = client.get("/v1/cache/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_entries" in data
    assert "total_hits" in data
    assert "total_misses" in data
    assert "hit_rate" in data
    assert "cache_age_seconds" in data
    assert "per_voice" in data
    assert isinstance(data["hit_rate"], float)
    assert 0.0 <= data["hit_rate"] <= 1.0
    assert isinstance(data["per_voice"], dict)


def test_speech_empty_input(client):
    resp = client.post("/v1/audio/speech", json={"input": "", "voice": "Decent_Boy"})
    assert resp.status_code == 400


def test_speech_no_gateway(client):
    resp = client.post("/v1/audio/speech", json={"input": "test", "voice": "Decent_Boy"})
    # No API key configured, all providers skipped, returns 503
    assert resp.status_code == 503


def test_cache_clear(client):
    resp = client.delete("/v1/cache")
    assert resp.status_code == 200


def test_list_fillers(client):
    resp = client.get("/v1/cache/fillers?voice_id=Decent_Boy")
    assert resp.status_code == 200
    data = resp.json()
    assert "fillers" in data
    assert len(data["fillers"]) > 0


def test_integrity_removes_orphan_db_entries(integrity_env):
    db, store, audio_dir, tmp_path = integrity_env

    real_file = Path(audio_dir) / "real.mp3"
    real_file.write_bytes(b"audio-data")
    db.add_entry(
        text_original="real", text_normalized="real", voice_id="v1",
        audio_path=str(real_file), audio_format="mp3", file_size=10,
    )

    db.add_entry(
        text_original="ghost", text_normalized="ghost", voice_id="v1",
        audio_path=str(Path(audio_dir) / "nonexistent.mp3"), audio_format="mp3",
        file_size=10,
    )

    store.hot_cache.load_entries(db.get_all_entries())
    assert store.hot_cache.size == 2

    _startup_integrity_check(db, store, audio_dir)

    assert len(db.get_all_entries()) == 1
    assert store.hot_cache.exact_lookup("ghost", "v1") is None
    assert store.hot_cache.exact_lookup("real", "v1") is not None
    assert real_file.exists()


def test_integrity_removes_orphan_audio_files(integrity_env):
    db, store, audio_dir, tmp_path = integrity_env

    real_file = Path(audio_dir) / "real.mp3"
    real_file.write_bytes(b"audio-data")
    db.add_entry(
        text_original="real", text_normalized="real", voice_id="v1",
        audio_path=str(real_file), audio_format="mp3", file_size=10,
    )

    orphan_file = Path(audio_dir) / "orphan.mp3"
    orphan_file.write_bytes(b"orphan-data")

    store.hot_cache.load_entries(db.get_all_entries())

    _startup_integrity_check(db, store, audio_dir)

    assert real_file.exists()
    assert not orphan_file.exists()
    assert len(db.get_all_entries()) == 1


def test_integrity_preserves_filler_dir(integrity_env):
    db, store, audio_dir, tmp_path = integrity_env

    fillers_dir = Path(audio_dir) / "fillers"
    filler_file = fillers_dir / "hmm.mp3"
    filler_file.write_bytes(b"filler-audio")

    _startup_integrity_check(db, store, audio_dir)

    assert filler_file.exists()


def test_auto_generate_fillers_on_startup(tmp_path, monkeypatch, capfd):
    import yaml
    from cachevoice.config import Settings
    
    config_path = tmp_path / "test_config.yaml"
    audio_dir = tmp_path / "audio"
    db_path = tmp_path / "test.db"
    audio_dir.mkdir()
    
    config_data = {
        "cache": {
            "audio_dir": str(audio_dir),
            "db_path": str(db_path),
        },
        "fillers": {
            "auto_generate_on_startup": True,
            "voice_id": "TestVoice",
        },
        "providers": {
            "default": "edge",
            "edge": {
                "default_voice": "tr-TR-AhmetNeural",
            },
        },
    }
    
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)
    
    monkeypatch.setattr("cachevoice.server._load_settings", lambda: Settings.from_yaml(str(config_path)))
    
    with TestClient(app) as client:
        pass
    
    captured = capfd.readouterr()
    assert "Auto-generating fillers for voice 'TestVoice'" in captured.err
    assert "Fillers: generated" in captured.err


def test_auto_generate_disabled_by_default(tmp_path, monkeypatch):
    import yaml
    from cachevoice.config import Settings
    
    config_path = tmp_path / "test_config.yaml"
    audio_dir = tmp_path / "audio"
    db_path = tmp_path / "test.db"
    audio_dir.mkdir()
    
    config_data = {
        "cache": {
            "audio_dir": str(audio_dir),
            "db_path": str(db_path),
        },
        "fillers": {
            "auto_generate_on_startup": False,
        },
        "providers": {
            "default": "edge",
            "edge": {
                "default_voice": "tr-TR-AhmetNeural",
            },
        },
    }
    
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)
    
    monkeypatch.setattr("cachevoice.server._load_settings", lambda: Settings.from_yaml(str(config_path)))
    
    with TestClient(app) as client:
        resp = client.get("/v1/cache/fillers?voice_id=Decent_Boy")
        assert resp.status_code == 200
