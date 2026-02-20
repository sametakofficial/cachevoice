import httpx
import pytest
from fastapi import HTTPException

from cachevoice.gateway.fallback import FallbackOrchestrator


def _http_status_error(status_code: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.invalid/v1/audio/speech")
    response = httpx.Response(status_code=status_code, request=request, text=message)
    return httpx.HTTPStatusError(message, request=request, response=response)


class StubLiteLLMRouter:
    def __init__(self, outcomes: list[object]):
        self._outcomes: list[object] = outcomes
        self.calls: int = 0

    async def synthesize(self, text: str, voice: str, model: str, response_format: str) -> bytes:
        _ = (text, voice, model, response_format)
        self.calls += 1
        outcome = self._outcomes[self.calls - 1]
        if isinstance(outcome, Exception):
            raise outcome
        if not isinstance(outcome, bytes):
            raise AssertionError("stub outcome must be bytes or Exception")
        return outcome


class StubEdgeProvider:
    def __init__(self, outcome: object):
        self._outcome: object = outcome
        self.calls: int = 0

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        _ = (text, voice)
        self.calls += 1
        if isinstance(self._outcome, Exception):
            raise self._outcome
        if not isinstance(self._outcome, bytes):
            raise AssertionError("stub outcome must be bytes or Exception")
        return self._outcome


class StepClock:
    def __init__(self, start: float = 1000.0):
        self.now: float = start

    def __call__(self) -> float:
        current = self.now
        self.now += 1.0
        return current


@pytest.mark.anyio
async def test_timeout_falls_back_to_edge(caplog: pytest.LogCaptureFixture):
    lite = StubLiteLLMRouter([httpx.TimeoutException("upstream timeout")])
    edge = StubEdgeProvider(b"edge-audio")
    orchestrator = FallbackOrchestrator(["litellm", "edge_tts"], lite, edge)

    caplog.set_level("INFO", logger="cachevoice.gateway")
    audio = await orchestrator.synthesize("hello", "alloy")

    assert audio == b"edge-audio"
    assert lite.calls == 1
    assert edge.calls == 1
    assert "fallback.fail provider=litellm" in caplog.text
    assert "fallback.success provider=edge_tts" in caplog.text


@pytest.mark.anyio
async def test_429_opens_circuit_breaker_and_skips_provider(caplog: pytest.LogCaptureFixture):
    too_many = _http_status_error(429, "rate limited")
    lite = StubLiteLLMRouter([too_many, too_many, too_many])
    edge = StubEdgeProvider(b"edge-audio")
    clock = StepClock()
    orchestrator = FallbackOrchestrator(["litellm", "edge_tts"], lite, edge, now_fn=clock)

    caplog.set_level("INFO", logger="cachevoice.gateway")
    _ = await orchestrator.synthesize("t1", "alloy")
    _ = await orchestrator.synthesize("t2", "alloy")
    _ = await orchestrator.synthesize("t3", "alloy")
    _ = await orchestrator.synthesize("t4", "alloy")

    assert lite.calls == 3
    assert edge.calls == 4
    assert "fallback.circuit-open provider=litellm" in caplog.text
    assert "fallback.skip provider=litellm reason=circuit-open" in caplog.text


@pytest.mark.anyio
async def test_all_providers_down_returns_503():
    lite = StubLiteLLMRouter([httpx.TimeoutException("litellm timeout")])
    edge = StubEdgeProvider(httpx.ConnectError("edge unavailable"))
    orchestrator = FallbackOrchestrator(["litellm", "edge_tts"], lite, edge)

    with pytest.raises(HTTPException) as exc_info:
        _ = await orchestrator.synthesize("hello", "alloy")

    assert exc_info.value.status_code == 503


@pytest.mark.anyio
async def test_http_400_does_not_fallback_to_edge():
    lite = StubLiteLLMRouter([_http_status_error(400, "bad request")])
    edge = StubEdgeProvider(b"should-not-be-used")
    orchestrator = FallbackOrchestrator(["litellm", "edge_tts"], lite, edge)

    with pytest.raises(HTTPException) as exc_info:
        _ = await orchestrator.synthesize("hello", "alloy")

    assert exc_info.value.status_code == 400
    assert lite.calls == 1
    assert edge.calls == 0
