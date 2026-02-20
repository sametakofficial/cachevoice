from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import asyncio
import logging
import time
from typing import Callable, Protocol

import httpx
from fastapi import HTTPException

logger = logging.getLogger("cachevoice.gateway")


class _LiteLLMRouterProtocol(Protocol):
    async def synthesize(self, text: str, voice: str, model: str, response_format: str) -> bytes: ...


class _EdgeProviderProtocol(Protocol):
    async def synthesize(self, text: str, voice: str | None = None) -> bytes: ...


@dataclass
class _CircuitState:
    failures: deque[float]
    open_until: float = 0.0


class FallbackOrchestrator:
    def __init__(
        self,
        fallback_chain: list[str],
        litellm_router: _LiteLLMRouterProtocol,
        edge_provider: _EdgeProviderProtocol,
        *,
        failure_threshold: int = 3,
        failure_window_seconds: int = 300,
        cooldown_seconds: int = 300,
        now_fn: Callable[[], float] | None = None,
    ):
        self._fallback_chain: list[str] = fallback_chain
        self._litellm_router: _LiteLLMRouterProtocol = litellm_router
        self._edge_provider: _EdgeProviderProtocol = edge_provider
        self._failure_threshold: int = failure_threshold
        self._failure_window_seconds: int = failure_window_seconds
        self._cooldown_seconds: int = cooldown_seconds
        self._now_fn: Callable[[], float] = now_fn or time.monotonic
        self._circuit: dict[str, _CircuitState] = defaultdict(
            lambda: _CircuitState(failures=deque())
        )

    async def synthesize(
        self,
        text: str,
        voice: str,
        model: str = "tts-1",
        response_format: str = "mp3",
    ) -> bytes:
        errors: list[str] = []

        for provider_name in self._fallback_chain:
            if self._is_circuit_open(provider_name):
                logger.info(
                    "fallback.skip provider=%s reason=circuit-open",
                    provider_name,
                )
                continue

            logger.info("fallback.try provider=%s", provider_name)

            try:
                audio = await self._call_provider(
                    provider_name,
                    text=text,
                    voice=voice,
                    model=model,
                    response_format=response_format,
                )
                self._clear_failures(provider_name)
                logger.info("fallback.success provider=%s", provider_name)
                return audio
            except Exception as exc:
                status_code = self._extract_status_code(exc)
                should_fallback = self._should_fallback(status_code, exc)
                error_text = str(exc)

                logger.warning(
                    "fallback.fail provider=%s status=%s error=%s",
                    provider_name,
                    status_code,
                    error_text,
                )
                errors.append(f"{provider_name}: {error_text}")

                if self._count_failure(status_code, exc):
                    self._record_failure(provider_name)

                if not should_fallback:
                    raise self._to_http_exception(exc, status_code)

        raise HTTPException(
            status_code=503,
            detail="TTS unavailable: all fallback providers failed"
            + (f" ({'; '.join(errors)})" if errors else ""),
        )

    async def _call_provider(
        self,
        provider_name: str,
        *,
        text: str,
        voice: str,
        model: str,
        response_format: str,
    ) -> bytes:
        provider_key = provider_name.lower()
        if provider_key == "litellm":
            return await self._litellm_router.synthesize(
                text,
                voice,
                model,
                response_format,
            )
        if provider_key in {"edge", "edge_tts", "edgetts"}:
            return await self._edge_provider.synthesize(text, voice)
        raise RuntimeError(f"Unknown fallback provider '{provider_name}'")

    def _extract_status_code(self, exc: Exception) -> int | None:
        if isinstance(exc, HTTPException):
            return exc.status_code
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code
        status_code = getattr(exc, "status_code", None)
        return status_code if isinstance(status_code, int) else None

    def _should_fallback(self, status_code: int | None, exc: Exception) -> bool:
        if status_code is not None:
            if status_code == 400:
                return False
            if status_code == 429:
                return True
            return status_code >= 500
        return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, asyncio.TimeoutError))

    def _count_failure(self, status_code: int | None, exc: Exception) -> bool:
        if status_code is not None:
            return status_code == 429 or status_code >= 500
        return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, asyncio.TimeoutError))

    def _record_failure(self, provider_name: str) -> None:
        now = self._now_fn()
        state = self._circuit[provider_name]
        self._prune_old_failures(state, now)
        state.failures.append(now)
        if len(state.failures) >= self._failure_threshold:
            state.open_until = now + self._cooldown_seconds
            logger.warning(
                "fallback.circuit-open provider=%s failures=%d window=%ds cooldown=%ds",
                provider_name,
                len(state.failures),
                self._failure_window_seconds,
                self._cooldown_seconds,
            )

    def _clear_failures(self, provider_name: str) -> None:
        state = self._circuit[provider_name]
        state.failures.clear()
        state.open_until = 0.0

    def _is_circuit_open(self, provider_name: str) -> bool:
        state = self._circuit[provider_name]
        now = self._now_fn()
        self._prune_old_failures(state, now)
        if state.open_until > now:
            return True
        if state.open_until:
            state.open_until = 0.0
        return False

    def _prune_old_failures(self, state: _CircuitState, now: float) -> None:
        cutoff = now - self._failure_window_seconds
        while state.failures and state.failures[0] < cutoff:
            _ = state.failures.popleft()

    def _to_http_exception(self, exc: Exception, status_code: int | None) -> HTTPException:
        if isinstance(exc, HTTPException):
            return exc
        if status_code is not None:
            return HTTPException(status_code=status_code, detail=str(exc))
        return HTTPException(status_code=503, detail=str(exc))
