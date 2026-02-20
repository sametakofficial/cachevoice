"""Filler audio pool management."""
from __future__ import annotations
import logging
from typing import Optional, Protocol
from ..cache.metadata import CacheMetadataDB
from ..cache.normalizer import normalize
from ..cache.store import FuzzyCacheStorage

logger = logging.getLogger("cachevoice.fillers")

FILLER_TEMPLATES = [
    {"id": "ack_listening", "text": "Evet, dinliyorum"},
    {"id": "ack_thinking", "text": "Hmm, bir saniye"},
    {"id": "ack_searching", "text": "Bakıyorum"},
    {"id": "ack_found", "text": "Buldum, bir saniye"},
    {"id": "ack_analyzing", "text": "Analiz ediyorum"},
    {"id": "ack_summarizing", "text": "Özetliyorum"},
    {"id": "ack_started", "text": "Hemen bakıyorum"},
    {"id": "ack_wait", "text": "Bir dakika"},
]


class FillerManager:
    def __init__(self, db: CacheMetadataDB, store: FuzzyCacheStorage,
                 gateway: Optional[_SynthesizerGateway] = None,
                 templates: Optional[list[dict[str, str]]] = None):
        self._db = db
        self._store = store
        self._gateway = gateway
        self._templates = templates or FILLER_TEMPLATES

    async def generate_fillers(self, voice_id: str) -> list[dict[str, object]]:
        """Generate filler audio for all templates. Returns list of generated entries."""
        if not self._gateway:
            raise RuntimeError("No TTS gateway configured")

        generated = []
        for tmpl in self._templates:
            text = tmpl["text"]
            normalized = normalize(text)

            # Skip if already cached
            existing = self._store.lookup(text, voice_id)
            if existing:
                logger.info("Filler already cached: %s", tmpl["id"])
                generated.append({"id": tmpl["id"], "text": text, "status": "exists"})
                continue

            try:
                audio_data = await self._gateway.synthesize(text, voice_id)
                audio_path = self._store.store(text, voice_id, audio_data)
                self._db.add_entry(
                    text_original=text, text_normalized=normalized,
                    voice_id=voice_id, audio_path=audio_path,
                    file_size=len(audio_data), is_filler=True,
                )
                generated.append({"id": tmpl["id"], "text": text, "status": "generated"})
                logger.info("Generated filler: %s", tmpl["id"])
            except Exception as e:
                logger.error("Failed to generate filler %s: %s", tmpl["id"], e)
                generated.append({"id": tmpl["id"], "text": text, "status": "error", "error": str(e)})

        return generated

    def list_fillers(self, voice_id: str) -> list[dict[str, object]]:
        """List available filler audio for a voice."""
        results = []
        for tmpl in self._templates:
            cached = self._store.lookup(tmpl["text"], voice_id)
            results.append({
                "id": tmpl["id"],
                "text": tmpl["text"],
                "cached": cached is not None,
                "audio_path": cached["audio_path"] if cached else None,
            })
        return results


class _SynthesizerGateway(Protocol):
    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        model: str | None = None,
        response_format: str = "mp3",
    ) -> bytes: ...
