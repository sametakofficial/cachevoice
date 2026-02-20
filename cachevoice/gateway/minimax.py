"""MiniMax/GateAI TTS gateway — OpenAI-compatible API."""
from __future__ import annotations
import httpx
import logging
import warnings
from typing import Optional

logger = logging.getLogger("cachevoice.gateway")


class MiniMaxGateway:
    def __init__(self, base_url: str, api_key: str, model: str = "speech-01-turbo",
                 default_voice: str = "Decent_Boy", timeout: int = 15):
        warnings.warn(
            "MiniMaxGateway is deprecated; use LiteLLMRouter instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._default_voice = default_voice
        self._timeout = timeout

    async def synthesize(self, text: str, voice: Optional[str] = None,
                         model: Optional[str] = None,
                         response_format: str = "mp3") -> bytes:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/audio/speech",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "input": text,
                    "voice": voice or self._default_voice,
                    "model": model or self._model,
                    "response_format": response_format,
                },
            )
            resp.raise_for_status()
            return resp.content

    @property
    def default_voice(self) -> str:
        return self._default_voice
