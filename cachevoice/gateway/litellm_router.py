from __future__ import annotations

import logging
from typing import Protocol, cast

from litellm import provider_list
from litellm.router import Router

from ..config import ProviderConfig, Settings
from .mapping import ModelMapper, VoiceMapper

logger = logging.getLogger("cachevoice.gateway")


class LiteLLMRouter:
    def __init__(self, settings: Settings):
        self._settings: Settings = settings
        self._voice_mapper: VoiceMapper
        self._model_mapper: ModelMapper
        self._provider_order: list[str]
        self._route_index: dict[tuple[str, str], str]
        self._router: Router | None

        self._settings = settings
        self._voice_mapper = VoiceMapper({"voice_mapping": settings.voice_mapping})
        self._model_mapper = ModelMapper({"model_mapping": settings.model_mapping})
        self._provider_order = self._build_provider_order()
        self._route_index = {}

        model_list = self._build_model_list()
        self._router = Router(model_list=model_list) if model_list else None

        if self._router:
            logger.info("LiteLLM router initialized with %d deployment(s)", len(model_list))
        else:
            logger.warning("No LiteLLM deployments configured; TTS will fail on cache miss")

    @property
    def available(self) -> bool:
        return self._router is not None

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        model: str | None = None,
        response_format: str = "mp3",
    ) -> bytes:
        if not self._router:
            raise RuntimeError("No TTS gateway configured")

        requested_model = model or "tts-1"
        last_error: Exception | None = None

        for provider in self._provider_order:
            route_name = self._route_index.get((provider, requested_model), provider)
            provider_cfg = self._settings.providers.configs.get(provider)
            if not provider_cfg:
                continue

            provider_voice = self._map_voice(voice or provider_cfg.default_voice or "alloy", provider)

            try:
                response = await self._router.aspeech(
                    model=route_name,
                    input=text,
                    voice=provider_voice,
                    response_format=response_format,
                )
                return self._as_bytes(response)
            except Exception as exc:
                last_error = exc
                logger.error(
                    "LiteLLM aspeech failed for provider '%s' (route=%s): %s",
                    provider,
                    route_name,
                    exc,
                )

        if last_error:
            raise last_error
        raise RuntimeError("No TTS providers configured")

    def _build_provider_order(self) -> list[str]:
        providers = [self._settings.providers.default, *self._settings.providers.fallback_chain]
        ordered: list[str] = []
        seen: set[str] = set()

        for provider in providers:
            if not provider or provider in seen:
                continue
            if provider not in self._settings.providers.configs:
                logger.warning("Provider '%s' referenced but missing from providers.configs", provider)
                continue
            ordered.append(provider)
            seen.add(provider)

        for provider in self._settings.providers.configs:
            if provider not in seen:
                ordered.append(provider)

        return ordered

    def _build_model_list(self) -> list[dict[str, object]]:
        model_list: list[dict[str, object]] = []

        generic_models: set[str] = {"tts-1"}
        generic_models.update(self._extract_generic_models())

        for provider in self._provider_order:
            provider_cfg = self._settings.providers.configs.get(provider)
            if not provider_cfg:
                continue

            default_deployment = self._deployment_for(provider, provider_cfg.litellm_model)
            if default_deployment:
                model_list.append(default_deployment)

            for generic_model in sorted(generic_models):
                mapped_model = self._map_model(generic_model, provider)
                route_name = f"{provider}:{generic_model}"
                deployment_model = self._compose_provider_model(provider_cfg.litellm_model, mapped_model)
                deployment = self._deployment_for(route_name, deployment_model, provider_cfg)
                if deployment:
                    model_list.append(deployment)
                    self._route_index[(provider, generic_model)] = route_name

        return model_list

    def _deployment_for(
        self,
        model_name: str,
        deployment_model: str,
        provider_cfg: ProviderConfig | None = None,
    ) -> dict[str, object] | None:
        if not deployment_model:
            return None

        cfg = provider_cfg
        if cfg is None:
            cfg = self._settings.providers.configs.get(model_name)
        if cfg is None:
            provider_key = model_name.split(":", maxsplit=1)[0]
            cfg = self._settings.providers.configs.get(provider_key)
        if cfg is None:
            return None

        provider_prefix = deployment_model.split("/", maxsplit=1)[0] if "/" in deployment_model else ""
        if provider_prefix and provider_prefix not in provider_list:
            logger.warning("Skipping model '%s': unsupported LiteLLM provider", deployment_model)
            return None

        if not self._has_api_key(cfg.api_key) and not deployment_model.startswith("edge/"):
            logger.warning("Skipping provider '%s' because api_key is empty", model_name)
            return None

        litellm_params: dict[str, object] = {
            "model": deployment_model,
            "timeout": cfg.timeout or 15,
        }
        if cfg.base_url:
            litellm_params["api_base"] = cfg.base_url
        if cfg.api_key:
            litellm_params["api_key"] = cfg.api_key

        return {
            "model_name": model_name,
            "litellm_params": litellm_params,
        }

    def _extract_generic_models(self) -> set[str]:
        raw_mapping = self._settings.model_mapping
        if not isinstance(raw_mapping, dict):
            return set()

        generic_models: set[str] = set()
        if raw_mapping and all(isinstance(v, dict) for v in raw_mapping.values()):
            for key, value in raw_mapping.items():
                if not isinstance(key, str):
                    continue
                if key in self._settings.providers.configs:
                    for provider_model in value:
                        if isinstance(provider_model, str):
                            generic_models.add(provider_model)
                else:
                    generic_models.add(key)
        return generic_models

    def _map_voice(self, voice: str, provider: str) -> str:
        mapped = self._voice_mapper.map(voice, provider)
        if mapped != voice:
            return mapped
        provider_map = self._settings.voice_mapping.get(provider)
        if isinstance(provider_map, dict):
            mapped_provider = provider_map.get(voice)
            if isinstance(mapped_provider, str):
                return mapped_provider
        return mapped

    def _map_model(self, model: str, provider: str) -> str:
        mapped = self._model_mapper.map(model, provider)
        if mapped != model:
            return mapped
        provider_map = self._settings.model_mapping.get(provider)
        if isinstance(provider_map, dict):
            mapped_provider = provider_map.get(model)
            if isinstance(mapped_provider, str):
                return mapped_provider
        return mapped

    @staticmethod
    def _compose_provider_model(base_model: str, mapped_model: str) -> str:
        if not mapped_model:
            return base_model
        if "/" in mapped_model:
            return mapped_model
        if "/" in base_model:
            prefix = base_model.split("/", maxsplit=1)[0]
            return f"{prefix}/{mapped_model}"
        return mapped_model

    @staticmethod
    def _has_api_key(api_key: str | None) -> bool:
        if api_key is None:
            return False
        stripped = api_key.strip()
        if not stripped:
            return False  # Empty string means no key provided
        return not (stripped.startswith("${") and stripped.endswith("}"))

    @staticmethod
    def _as_bytes(response: object) -> bytes:
        if isinstance(response, bytes):
            return response
        if isinstance(response, bytearray):
            return bytes(response)
        if isinstance(response, memoryview):
            return response.tobytes()
        if hasattr(response, "read"):
            data = cast(_Readable, response).read()
            return data
        content = getattr(response, "content", None)
        if isinstance(content, bytes):
            return content
        raise TypeError(f"Unexpected LiteLLM response type: {type(response)!r}")


class _Readable(Protocol):
    def read(self) -> bytes: ...
