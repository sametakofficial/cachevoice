"""Voice and model mapping between OpenAI and provider-specific names."""

from typing import Any


class VoiceMapper:
    """Maps OpenAI voice names to provider-specific voice names."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize VoiceMapper with voice_mapping from config.

        Expected structure:
        voice_mapping:
          alloy:
            minimax: "Decent_Boy"
            elevenlabs: "voice-id-123"
          echo:
            minimax: "Deep_Voice_Man"
        """
        self._mappings: dict[str, dict[str, str]] = config.get("voice_mapping", {})

    def map(self, voice: str, provider: str) -> str:
        """
        Map voice name for a specific provider.

        Args:
            voice: OpenAI voice name (e.g., "alloy")
            provider: Target provider (e.g., "minimax")

        Returns:
            Mapped voice name, or original if no mapping exists
        """
        if voice in self._mappings:
            provider_map = self._mappings[voice]
            if provider in provider_map:
                return provider_map[provider]
        return voice


class ModelMapper:
    """Maps OpenAI model names to provider-specific model names."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize ModelMapper with model_mapping from config.

        Expected structure:
        model_mapping:
          tts-1:
            minimax: "speech-01-turbo"
            openai: "tts-1"
          tts-1-hd:
            minimax: "speech-01-hd"
        """
        self._mappings: dict[str, dict[str, str]] = config.get("model_mapping", {})

    def map(self, model: str, provider: str) -> str:
        """
        Map model name for a specific provider.

        Args:
            model: OpenAI model name (e.g., "tts-1")
            provider: Target provider (e.g., "minimax")

        Returns:
            Mapped model name, or original if no mapping exists
        """
        if model in self._mappings:
            provider_map = self._mappings[model]
            if provider in provider_map:
                return provider_map[provider]
        return model
