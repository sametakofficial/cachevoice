"""ElevenLabs TTS gateway."""
from __future__ import annotations
import httpx
import logging
import warnings
from typing import Optional

logger = logging.getLogger("cachevoice.gateway")


class ElevenLabsGateway:
    def __init__(self, base_url: str, api_key: str, model: str = "eleven_multilingual_v2",
                 default_voice: str = "some-voice-id", timeout: int = 15):
        warnings.warn(
            "ElevenLabsGateway is deprecated; use LiteLLMRouter instead.",
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
        voice_id = voice or self._default_voice
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/v1/text-to-speech/{voice_id}",
                headers={
                    "xi-api-key": self._api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": model or self._model,
                },
            )
            resp.raise_for_status()
            return resp.content

    @property
    def default_voice(self) -> str:
        return self._default_voice
