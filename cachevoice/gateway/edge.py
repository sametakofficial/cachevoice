"""Microsoft Edge TTS gateway — free, no API key required."""
from __future__ import annotations
import logging
import os
import tempfile
from typing import Optional

logger = logging.getLogger("cachevoice.gateway")


class EdgeTTSProvider:
    """Microsoft Edge TTS provider with Turkish voice support."""

    def __init__(self, default_voice: str = "tr-TR-AhmetNeural"):
        self._default_voice = default_voice

    async def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        """Synthesize text to MP3 audio bytes.
        
        Args:
            text: Text to synthesize
            voice: Voice ID (defaults to tr-TR-AhmetNeural)
            
        Returns:
            MP3 audio bytes
            
        Raises:
            Exception: If synthesis fails
        """
        import edge_tts

        voice_id = voice or self._default_voice
        fd, mp3_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)

        try:
            comm = edge_tts.Communicate(text, voice_id)
            await comm.save(mp3_path)
            
            with open(mp3_path, "rb") as f:
                audio_bytes = f.read()
            
            return audio_bytes
        except Exception as e:
            logger.error(f"Edge TTS synthesis failed: {e}")
            raise
        finally:
            # Cleanup temp file
            try:
                os.unlink(mp3_path)
            except Exception:
                pass

    @property
    def default_voice(self) -> str:
        return self._default_voice
