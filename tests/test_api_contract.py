from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from collections.abc import Generator
import json
from typing import TypedDict, cast

import pytest
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive
import cachevoice.server as server


class LookupResult(TypedDict):
    audio_path: str
    normalized: str
    match_type: str
    score: int


class StoreCall(TypedDict):
    text: str
    voice: str
    audio_data: bytes
    audio_format: str


class AddEntryCall(TypedDict):
    text_original: str
    text_normalized: str
    voice_id: str
    audio_path: str
    model: str
    audio_format: str
    file_size: int
    is_filler: bool


class StubStore:
    def __init__(self):
        self.lookup_result: LookupResult | None = None
        self.lookup_calls: list[tuple[str, str]] = []
        self.store_calls: list[StoreCall] = []

    def lookup(self, text: str, voice_id: str):
        self.lookup_calls.append((text, voice_id))
        return self.lookup_result

    def store(self, text: str, voice_id: str, audio_data: bytes, audio_format: str = "mp3"):
        self.store_calls.append(
            {
                "text": text,
                "voice": voice_id,
                "audio_data": audio_data,
                "audio_format": audio_format,
            }
        )
        return f"/tmp/stored-audio.{audio_format}"


class StubDB:
    def __init__(self):
        self.hit_calls: list[tuple[str, str]] = []
        self.add_entry_calls: list[AddEntryCall] = []

    async def record_hit_async(self, text_normalized: str, voice_id: str):
        self.hit_calls.append((text_normalized, voice_id))

    def add_entry(
        self,
        text_original: str,
        text_normalized: str,
        voice_id: str,
        audio_path: str,
        model: str = "",
        audio_format: str = "mp3",
        file_size: int = 0,
        is_filler: bool = False,
    ):
        self.add_entry_calls.append(
            {
                "text_original": text_original,
                "text_normalized": text_normalized,
                "voice_id": voice_id,
                "audio_path": audio_path,
                "model": model,
                "audio_format": audio_format,
                "file_size": file_size,
                "is_filler": is_filler,
            }
        )


class StubGateway:
    def __init__(self):
        self.calls: list[dict[str, str]] = []
        self.response_bytes: bytes = b"generated-audio"

    async def synthesize(self, text: str, voice: str, model: str, response_format: str) -> bytes:
        self.calls.append(
            {
                "text": text,
                "voice": voice,
                "model": model,
                "response_format": response_format,
            }
        )
        return self.response_bytes


@dataclass
class ApiEnv:
    store: StubStore
    db: StubDB
    gateway: StubGateway
    tmp_path: Path


async def call_audio_speech(payload: dict[str, str]) -> Response:
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
        cast(Receive, receive),
    )
    return await server.audio_speech(request)


@pytest.fixture(autouse=True)
def mock_audio_conversion(monkeypatch: pytest.MonkeyPatch):
    """Mock audio conversion to avoid ffmpeg dependency in tests."""
    def fake_convert(audio_data: bytes, target_format: str) -> bytes | None:
        return audio_data
    monkeypatch.setattr("cachevoice.server._convert_audio_format", fake_convert)


@pytest.fixture
def api_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[ApiEnv, None, None]:
    store = StubStore()
    db = StubDB()
    gateway = StubGateway()
    settings = SimpleNamespace(
        cache=SimpleNamespace(
            enabled=True,
            eviction=SimpleNamespace(max_text_length=500),
        )
    )

    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_db", db)
    monkeypatch.setattr(server, "_gateway", gateway)
    monkeypatch.setattr(server, "_settings", settings)
    yield ApiEnv(store=store, db=db, gateway=gateway, tmp_path=tmp_path)


@pytest.mark.anyio
async def test_valid_tts_request_returns_audio_bytes_and_mp3_headers(api_env: ApiEnv):
    api_env.gateway.response_bytes = b"mp3-audio"

    response = await call_audio_speech(
        {"input": "hello", "voice": "Decent_Boy", "model": "tts-1", "response_format": "mp3"}
    )

    assert response.status_code == 200
    assert response.body == b"mp3-audio"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["content-length"] == str(len(b"mp3-audio"))


@pytest.mark.anyio
async def test_missing_text_returns_400_with_empty_body(api_env: ApiEnv):
    assert api_env.store.lookup_calls == []
    response = await call_audio_speech({"voice": "Decent_Boy"})

    assert response.status_code == 400
    assert response.body == b""


@pytest.mark.anyio
async def test_missing_voice_uses_default_voice_and_returns_ogg(api_env: ApiEnv):
    api_env.gateway.response_bytes = b"ogg-audio"

    response = await call_audio_speech({"input": "hello without voice", "response_format": "ogg"})

    assert response.status_code == 200
    assert response.body == b"ogg-audio"
    assert response.headers["content-type"] == "audio/ogg"
    assert api_env.gateway.calls[-1]["voice"] == "Decent_Boy"


@pytest.mark.anyio
async def test_cache_hit_returns_cached_audio_without_gateway_call(api_env: ApiEnv):
    cached_audio_path = api_env.tmp_path / "cached.mp3"
    _ = cached_audio_path.write_bytes(b"cached-audio")
    api_env.store.lookup_result = {
        "audio_path": str(cached_audio_path),
        "normalized": "hello",
        "match_type": "exact",
        "score": 100,
    }

    response = await call_audio_speech({"input": "hello", "voice": "Decent_Boy", "response_format": "mp3"})

    assert response.status_code == 200
    assert response.body == b"cached-audio"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["content-length"] == str(len(b"cached-audio"))
    assert api_env.gateway.calls == []
    assert api_env.db.hit_calls == [("hello", "Decent_Boy")]


@pytest.mark.anyio
async def test_cache_miss_calls_gateway_stores_audio_and_returns_wav(api_env: ApiEnv):
    api_env.gateway.response_bytes = b"wav-audio"
    api_env.store.lookup_result = None

    response = await call_audio_speech(
        {"input": "cache miss", "voice": "voice-1", "model": "tts-1", "response_format": "wav"}
    )

    assert response.status_code == 200
    assert response.body == b"wav-audio"
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["content-length"] == str(len(b"wav-audio"))
    assert len(api_env.gateway.calls) == 1
    # After T10: gateway always receives mp3, server converts to requested format
    assert api_env.gateway.calls[0]["response_format"] == "mp3"

    assert len(api_env.store.store_calls) == 1
    assert api_env.store.store_calls[0]["audio_format"] == "wav"

    assert len(api_env.db.add_entry_calls) == 1
    assert api_env.db.add_entry_calls[0]["text_original"] == "cache miss"
    assert api_env.db.add_entry_calls[0]["voice_id"] == "voice-1"
    assert api_env.db.add_entry_calls[0]["audio_format"] == "wav"
