"""Integration tests — full cache flow."""
import pytest
from fastapi.testclient import TestClient
from cachevoice.server import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_cache_stats(client):
    resp = client.get("/v1/cache/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_entries" in data


def test_speech_empty_input(client):
    resp = client.post("/v1/audio/speech", json={"input": "", "voice": "Decent_Boy"})
    assert resp.status_code == 400


def test_speech_no_gateway(client):
    resp = client.post("/v1/audio/speech", json={"input": "test", "voice": "Decent_Boy"})
    # No API key configured, LiteLLM returns 502 for connection errors
    assert resp.status_code == 502


def test_cache_clear(client):
    resp = client.delete("/v1/cache")
    assert resp.status_code == 200


def test_list_fillers(client):
    resp = client.get("/v1/cache/fillers?voice_id=Decent_Boy")
    assert resp.status_code == 200
    data = resp.json()
    assert "fillers" in data
    assert len(data["fillers"]) > 0
