"""Integration tests — full cache flow."""
import pytest
import os
import asyncio
import json
from pathlib import Path
from typing import cast
from fastapi.testclient import TestClient
from starlette.requests import Request
from cachevoice.server import app, _startup_integrity_check
import cachevoice.server as server
from cachevoice.cache.metadata import CacheMetadataDB
from cachevoice.cache.store import FuzzyCacheStorage
from cachevoice.cache.evictor import CacheEvictor
from cachevoice.cache.normalizer import normalize
from cachevoice.config import Settings
from cachevoice.gateway.fallback import FallbackOrchestrator
from cachevoice.fillers.manager import FillerManager


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


class _GatewayStub:
    def __init__(self, delay_seconds: float = 0.0):
        self.delay_seconds = delay_seconds
        self.calls: list[dict[str, str]] = []

    async def synthesize(self, text: str, voice: str, model: str, response_format: str) -> bytes:
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        self.calls.append(
            {
                "text": text,
                "voice": voice,
                "model": model,
                "response_format": response_format,
            }
        )
        return f"audio-{len(self.calls)}".encode()


class _LiteFailingStub:
    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls = 0

    async def synthesize(self, text: str, voice: str, model: str, response_format: str) -> bytes:
        _ = (text, voice, model, response_format)
        self.calls += 1
        raise self.exc


class _EdgeStub:
    def __init__(self, audio: bytes):
        self.audio = audio
        self.calls = 0

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        _ = (text, voice)
        self.calls += 1
        return self.audio


class _FillerGatewayStub:
    def __init__(self):
        self.calls: list[tuple[str, str | None]] = []

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        model: str | None = None,
        response_format: str = "mp3",
    ) -> bytes:
        _ = (model, response_format)
        self.calls.append((text, voice))
        return f"filler:{text}".encode()


def _make_settings(tmp_path: Path, variety_depth: int) -> Settings:
    return Settings.model_validate(
        {
            "cache": {
                "audio_dir": str(tmp_path / "audio"),
                "db_path": str(tmp_path / "cache.db"),
                "enabled": True,
                "variety_depth": variety_depth,
            },
            "providers": {
                "default": "edge",
                "edge": {
                    "default_voice": "Decent_Boy",
                },
            },
        }
    )


def _setup_variety_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variety_depth: int, delay_seconds: float = 0.0):
    settings = _make_settings(tmp_path, variety_depth)
    store = FuzzyCacheStorage(
        settings.cache.audio_dir,
        fuzzy_config=settings.cache.fuzzy,
        variety_depth=settings.cache.variety_depth,
    )
    db = CacheMetadataDB(settings.cache.db_path)
    gateway = _GatewayStub(delay_seconds=delay_seconds)

    monkeypatch.setattr(server, "_settings", settings)
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_db", db)
    monkeypatch.setattr(server, "_gateway", gateway)
    monkeypatch.setattr(server, "_variety_in_flight", set())
    monkeypatch.setattr(server, "_evictor", None)
    monkeypatch.setattr(server, "_write_counter", 0)

    return settings, store, db, gateway


async def _call_audio_speech(payload: dict[str, str]):
    body = json.dumps(payload).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/audio/speech",
            "raw_path": b"/v1/audio/speech",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        },
        receive=receive,  # type: ignore[arg-type]
    )
    return await server.audio_speech(request)


async def _call_generate_fillers(payload: dict[str, str]):
    body = json.dumps(payload).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/cache/fillers/generate",
            "raw_path": b"/v1/cache/fillers/generate",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        },
        receive=receive,  # type: ignore[arg-type]
    )
    return await server.generate_fillers(request)


async def _wait_for_version_count(db: CacheMetadataDB, normalized_text: str, voice: str, expected: int):
    for _ in range(100):
        if db.get_version_count(normalized_text, voice) == expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Expected version_count={expected} for {normalized_text}/{voice}")


@pytest.mark.anyio
async def test_variety_background_generation_on_cache_miss(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _, _, db, gateway = _setup_variety_env(monkeypatch, tmp_path, variety_depth=2)
    payload = {"input": "miss first", "voice": "Decent_Boy", "model": "tts-1", "response_format": "mp3"}
    normalized = normalize(payload["input"])

    response = await _call_audio_speech(payload)
    assert response.status_code == 200

    await _wait_for_version_count(db, normalized, payload["voice"], expected=2)
    assert len(gateway.calls) == 2
    assert server._variety_in_flight == set()


@pytest.mark.anyio
async def test_variety_background_generation_on_cache_hit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _, _, db, gateway = _setup_variety_env(monkeypatch, tmp_path, variety_depth=3)
    payload = {"input": "repeat this", "voice": "Decent_Boy", "model": "tts-1", "response_format": "mp3"}
    normalized = normalize(payload["input"])

    first = await _call_audio_speech(payload)
    assert first.status_code == 200
    await _wait_for_version_count(db, normalized, payload["voice"], expected=2)

    second = await _call_audio_speech(payload)
    assert second.status_code == 200
    await _wait_for_version_count(db, normalized, payload["voice"], expected=3)

    assert len(gateway.calls) == 3
    assert server._variety_in_flight == set()


@pytest.mark.anyio
async def test_variety_no_background_when_depth_reached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _, _, db, gateway = _setup_variety_env(monkeypatch, tmp_path, variety_depth=2)
    payload = {"input": "maxed text", "voice": "Decent_Boy", "model": "tts-1", "response_format": "mp3"}
    normalized = normalize(payload["input"])

    first = await _call_audio_speech(payload)
    assert first.status_code == 200
    await _wait_for_version_count(db, normalized, payload["voice"], expected=2)

    second = await _call_audio_speech(payload)
    assert second.status_code == 200
    await asyncio.sleep(0.05)

    assert db.get_version_count(normalized, payload["voice"]) == 2
    assert len(gateway.calls) == 2


@pytest.mark.anyio
async def test_variety_in_flight_deduplicates_background_generation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _, store, db, gateway = _setup_variety_env(monkeypatch, tmp_path, variety_depth=2, delay_seconds=0.05)
    text = "dedup me"
    voice = "Decent_Boy"
    normalized = normalize(text)
    seed_path = store.store(text, voice, b"seed-audio", "mp3", version_num=1)
    db.add_entry(
        text_original=text,
        text_normalized=normalized,
        voice_id=voice,
        audio_path=seed_path,
        model="tts-1",
        audio_format="mp3",
        file_size=len(b"seed-audio"),
        version_num=1,
    )

    payload = {"input": text, "voice": voice, "model": "tts-1", "response_format": "mp3"}
    first, second = await asyncio.gather(_call_audio_speech(payload), _call_audio_speech(payload))
    assert first.status_code == 200
    assert second.status_code == 200

    await _wait_for_version_count(db, normalized, voice, expected=2)

    assert len(gateway.calls) == 1
    assert server._variety_in_flight == set()


@pytest.mark.anyio
async def test_end_to_end_cache_flow_store_hit_evict_miss_restore(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _, store, db, gateway = _setup_variety_env(monkeypatch, tmp_path, variety_depth=1)
    payload = {"input": "cache flow", "voice": "Decent_Boy", "model": "tts-1", "response_format": "mp3"}
    normalized = normalize(payload["input"])

    first = await _call_audio_speech(payload)
    assert first.status_code == 200
    assert first.body == b"audio-1"
    assert len(gateway.calls) == 1
    assert db.get_version_count(normalized, payload["voice"]) == 1

    second = await _call_audio_speech(payload)
    assert second.status_code == 200
    assert second.body == b"audio-1"
    assert len(gateway.calls) == 1

    evictor = CacheEvictor(db, max_entries=0, hot_cache=store.hot_cache)
    assert evictor.run() == 1
    assert store.lookup(payload["input"], payload["voice"]) is None

    third = await _call_audio_speech(payload)
    assert third.status_code == 200
    assert third.body == b"audio-2"
    assert len(gateway.calls) == 2
    assert db.get_version_count(normalized, payload["voice"]) == 1


@pytest.mark.anyio
async def test_variety_depth_random_selection_uses_existing_versions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _, store, db, gateway = _setup_variety_env(monkeypatch, tmp_path, variety_depth=3)
    payload = {"input": "random variety", "voice": "Decent_Boy", "model": "tts-1", "response_format": "mp3"}
    normalized = normalize(payload["input"])

    first = await _call_audio_speech(payload)
    assert first.status_code == 200
    await _wait_for_version_count(db, normalized, payload["voice"], expected=2)

    second = await _call_audio_speech(payload)
    assert second.status_code == 200
    await _wait_for_version_count(db, normalized, payload["voice"], expected=3)

    monkeypatch.setattr("cachevoice.cache.hot.random.choice", lambda paths: paths[-1])
    third = await _call_audio_speech(payload)
    assert third.status_code == 200
    assert third.body == b"audio-3"
    assert len(gateway.calls) == 3
    assert len(store.hot_cache.get_paths(normalized, payload["voice"])) == 3


@pytest.mark.anyio
async def test_minimax_marked_and_plain_text_share_cache_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _, _, db, gateway = _setup_variety_env(monkeypatch, tmp_path, variety_depth=1)
    first_payload = {
        "input": "(laughs) Merhaba<#1.5#> nasilsin?",
        "voice": "Decent_Boy",
        "model": "tts-1",
        "response_format": "mp3",
    }
    second_payload = {
        "input": "Merhaba nasılsın",
        "voice": "Decent_Boy",
        "model": "tts-1",
        "response_format": "mp3",
    }
    normalized = normalize(second_payload["input"])

    first = await _call_audio_speech(first_payload)
    assert first.status_code == 200
    assert len(gateway.calls) == 1

    second = await _call_audio_speech(second_payload)
    assert second.status_code == 200
    assert second.body == first.body
    assert len(gateway.calls) == 1
    assert db.get_version_count(normalized, second_payload["voice"]) == 1


@pytest.mark.anyio
async def test_concurrent_requests_create_single_cache_entry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _, store, db, gateway = _setup_variety_env(monkeypatch, tmp_path, variety_depth=1, delay_seconds=0.03)
    payload = {"input": "parallel dedup", "voice": "Decent_Boy", "model": "tts-1", "response_format": "mp3"}
    normalized = normalize(payload["input"])

    responses = await asyncio.gather(*[_call_audio_speech(payload) for _ in range(8)])
    assert all(resp.status_code == 200 for resp in responses)
    assert db.get_version_count(normalized, payload["voice"]) == 1
    assert len(store.hot_cache.get_paths(normalized, payload["voice"])) == 1
    assert len(gateway.calls) >= 1


@pytest.mark.anyio
async def test_provider_fallback_integration_uses_edge_on_primary_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    settings = _make_settings(tmp_path, variety_depth=1)
    store = FuzzyCacheStorage(settings.cache.audio_dir, fuzzy_config=settings.cache.fuzzy, variety_depth=1)
    db = CacheMetadataDB(settings.cache.db_path)
    lite = _LiteFailingStub(asyncio.TimeoutError("litellm timeout"))
    edge = _EdgeStub(b"edge-fallback-audio")
    gateway = FallbackOrchestrator(["litellm", "edge_tts"], lite, edge)

    monkeypatch.setattr(server, "_settings", settings)
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_db", db)
    monkeypatch.setattr(server, "_gateway", gateway)
    monkeypatch.setattr(server, "_variety_in_flight", set())
    monkeypatch.setattr(server, "_evictor", None)
    monkeypatch.setattr(server, "_write_counter", 0)

    payload = {"input": "fallback test", "voice": "Decent_Boy", "model": "tts-1", "response_format": "mp3"}
    response = await _call_audio_speech(payload)

    assert response.status_code == 200
    assert response.body == b"edge-fallback-audio"
    assert lite.calls == 1
    assert edge.calls == 1
    assert db.get_version_count(normalize(payload["input"]), payload["voice"]) == 1


@pytest.mark.anyio
async def test_format_conversion_mp3_to_opus_cached_and_served(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _, store, _, gateway = _setup_variety_env(monkeypatch, tmp_path, variety_depth=1)

    def fake_convert(audio_data: bytes, target_format: str) -> bytes | None:
        if target_format == "opus":
            return b"converted-opus"
        return None

    monkeypatch.setattr(server, "_convert_audio_format", fake_convert)
    payload = {"input": "needs opus", "voice": "Decent_Boy", "model": "tts-1", "response_format": "opus"}

    first = await _call_audio_speech(payload)
    assert first.status_code == 200
    assert first.body == b"converted-opus"
    assert first.media_type == "audio/ogg"
    assert gateway.calls[0]["response_format"] == "mp3"

    lookup = store.lookup(payload["input"], payload["voice"])
    assert lookup is not None
    assert str(lookup["audio_path"]).endswith(".opus")

    second = await _call_audio_speech(payload)
    assert second.status_code == 200
    assert second.body == b"converted-opus"
    assert len(gateway.calls) == 1


@pytest.mark.anyio
async def test_filler_generation_and_lookup_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    settings = _make_settings(tmp_path, variety_depth=1)
    store = FuzzyCacheStorage(settings.cache.audio_dir, fuzzy_config=settings.cache.fuzzy, variety_depth=1)
    db = CacheMetadataDB(settings.cache.db_path)
    filler_gateway = _FillerGatewayStub()
    filler_mgr = FillerManager(db, store, filler_gateway)

    monkeypatch.setattr(server, "_settings", settings)
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_db", db)
    monkeypatch.setattr(server, "_gateway", filler_gateway)
    monkeypatch.setattr(server, "_filler_mgr", filler_mgr)
    monkeypatch.setattr(server, "_variety_in_flight", set())
    monkeypatch.setattr(server, "_evictor", None)
    monkeypatch.setattr(server, "_write_counter", 0)

    generated = cast(dict[str, object], await _call_generate_fillers({"voice_id": "Decent_Boy"}))
    results = cast(list[dict[str, object]], generated["results"])
    assert results
    assert any(item["status"] == "generated" for item in results)

    listed = cast(dict[str, object], await server.list_fillers("Decent_Boy"))
    fillers = cast(list[dict[str, object]], listed["fillers"])
    assert fillers
    assert all(bool(item["cached"]) for item in fillers)

    first_filler = fillers[0]
    assert store.lookup(str(first_filler["text"]), "Decent_Boy") is not None


def test_integrity_cleans_mixed_orphans_and_preserves_non_audio(integrity_env):
    db, store, audio_dir, _tmp_path = integrity_env
    audio_dir_path = Path(audio_dir)

    real_file = audio_dir_path / "real.mp3"
    real_file.write_bytes(b"real")
    db.add_entry(
        text_original="real",
        text_normalized="real",
        voice_id="v1",
        audio_path=str(real_file),
        audio_format="mp3",
        file_size=4,
    )

    db.add_entry(
        text_original="ghost",
        text_normalized="ghost",
        voice_id="v1",
        audio_path=str(audio_dir_path / "ghost.mp3"),
        audio_format="mp3",
        file_size=4,
    )

    orphan_audio = audio_dir_path / "orphan.ogg"
    orphan_audio.write_bytes(b"orphan")
    non_audio = audio_dir_path / "notes.txt"
    non_audio.write_text("keep me")
    filler_file = audio_dir_path / "fillers" / "uhh.mp3"
    filler_file.write_bytes(b"filler")

    store.hot_cache.load_entries(db.get_all_entries())

    _startup_integrity_check(db, store, audio_dir)

    entries = db.get_all_entries()
    assert len(entries) == 1
    assert entries[0]["text_normalized"] == "real"
    assert real_file.exists()
    assert not orphan_audio.exists()
    assert non_audio.exists()
    assert filler_file.exists()
