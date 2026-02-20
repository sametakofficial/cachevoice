from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
from typing import cast
import wave

import pytest
from starlette.requests import Request
from starlette.responses import Response

import cachevoice.server as server
from cachevoice.cache.metadata import CacheMetadataDB
from cachevoice.cache.store import FuzzyCacheStorage
from cachevoice.config import Settings
from cachevoice.gateway.litellm_router import LiteLLMRouter


class RouterStub:
    model_list: list[dict[str, object]]

    def __init__(self, model_list: list[dict[str, object]]):
        self.model_list = model_list
        self.calls: list[dict[str, str]] = []
        self.response_bytes: bytes = b"stub-mp3-audio"

    async def aspeech(self, model: str, input: str, voice: str, response_format: str) -> bytes:
        self.calls.append(
            {
                "model": model,
                "input": input,
                "voice": voice,
                "response_format": response_format,
            }
        )
        return self.response_bytes


@dataclass
class LiteLLMEnv:
    settings: Settings
    store: FuzzyCacheStorage
    db: CacheMetadataDB
    gateway: LiteLLMRouter
    router_stub: RouterStub


def _make_settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "providers": {
                "default": "minimax",
                "fallback_chain": [],
                "minimax": {
                    "litellm_model": "minimax/speech-01-turbo",
                    "api_key": "test-key",
                    "default_voice": "Decent_Boy",
                    "timeout": 15,
                },
            },
            "cache": {
                "audio_dir": str(tmp_path / "audio"),
                "db_path": str(tmp_path / "cache.db"),
                "enabled": True,
                "eviction": {"max_text_length": 20},
            },
            "voice_mapping": {
                "minimax": {
                    "alloy": "Decent_Boy",
                }
            },
            "model_mapping": {
                "minimax": {
                    "tts-1": "speech-01-turbo",
                }
            },
        }
    )


async def _call_audio_speech(payload: dict[str, str]) -> Response:
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


def _valid_wav_bytes() -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 160)
    return buffer.getvalue()


@pytest.fixture
def litellm_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[LiteLLMEnv, None, None]:
    created_stubs: list[RouterStub] = []

    def fake_router_ctor(model_list: list[dict[str, object]]) -> RouterStub:
        stub = RouterStub(model_list)
        created_stubs.append(stub)
        return stub

    monkeypatch.setattr("cachevoice.gateway.litellm_router.provider_list", ["minimax", "openai"])
    monkeypatch.setattr("cachevoice.gateway.litellm_router.Router", fake_router_ctor)

    settings = _make_settings(tmp_path)
    gateway = LiteLLMRouter(settings)
    store = FuzzyCacheStorage(settings.cache.audio_dir, fuzzy_threshold=settings.cache.fuzzy.threshold)
    db = CacheMetadataDB(settings.cache.db_path)

    monkeypatch.setattr(server, "_settings", settings)
    monkeypatch.setattr(server, "_gateway", gateway)
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_db", db)
    monkeypatch.setattr(server, "_write_counter", 0)
    monkeypatch.setattr(server, "_evictor", None)

    yield LiteLLMEnv(
        settings=settings,
        store=store,
        db=db,
        gateway=gateway,
        router_stub=created_stubs[0],
    )


def test_router_initialization_from_config(litellm_env: LiteLLMEnv):
    assert litellm_env.gateway.available is True
    model_names = {cast(str, entry["model_name"]) for entry in litellm_env.router_stub.model_list}
    assert "minimax" in model_names
    assert "minimax:tts-1" in model_names


@pytest.mark.anyio
async def test_cache_miss_goes_request_to_litellm_to_audio_to_cache(litellm_env: LiteLLMEnv):
    litellm_env.router_stub.response_bytes = b"generated-audio"

    response = await _call_audio_speech(
        {"input": "hello from litellm", "voice": "alloy", "model": "tts-1", "response_format": "mp3"}
    )

    assert response.status_code == 200
    assert response.body == b"generated-audio"
    assert response.headers["content-type"] == "audio/mpeg"
    assert len(litellm_env.router_stub.calls) == 1
    assert litellm_env.store.size == 1
    assert len(litellm_env.db.get_all_entries()) == 1


@pytest.mark.anyio
async def test_cache_hit_returns_same_audio_without_provider_call(litellm_env: LiteLLMEnv):
    litellm_env.router_stub.response_bytes = b"stable-audio"
    payload = {"input": "repeat me", "voice": "alloy", "model": "tts-1", "response_format": "mp3"}

    first = await _call_audio_speech(payload)
    second = await _call_audio_speech(payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.body == b"stable-audio"
    assert second.body == b"stable-audio"
    assert len(litellm_env.router_stub.calls) == 1


@pytest.mark.anyio
async def test_voice_mapping_alloy_to_decent_boy_applied(litellm_env: LiteLLMEnv):
    _ = await _call_audio_speech(
        {"input": "voice mapping", "voice": "alloy", "model": "tts-1", "response_format": "mp3"}
    )

    assert litellm_env.router_stub.calls[-1]["voice"] == "Decent_Boy"


@pytest.mark.anyio
async def test_format_conversion_returns_valid_audio(litellm_env: LiteLLMEnv, monkeypatch: pytest.MonkeyPatch):
    expected_wav = _valid_wav_bytes()

    def fake_convert(audio_data: bytes, target_format: str) -> bytes | None:
        _ = audio_data
        if target_format == "wav":
            return expected_wav
        return None

    monkeypatch.setattr(server, "_convert_audio_format", fake_convert)

    response = await _call_audio_speech(
        {"input": "convert me", "voice": "alloy", "model": "tts-1", "response_format": "wav"}
    )

    assert response.status_code == 200
    assert len(litellm_env.router_stub.calls) == 1
    assert response.headers["content-type"] == "audio/wav"
    body = bytes(response.body)
    assert body.startswith(b"RIFF")
    assert body[8:12] == b"WAVE"


@pytest.mark.anyio
async def test_max_text_length_enforcement_skips_cache_store(litellm_env: LiteLLMEnv):
    long_text = "x" * (litellm_env.settings.cache.eviction.max_text_length + 1)

    response = await _call_audio_speech(
        {"input": long_text, "voice": "alloy", "model": "tts-1", "response_format": "mp3"}
    )

    assert response.status_code == 200
    assert litellm_env.store.size == 0
    assert len(litellm_env.db.get_all_entries()) == 0
