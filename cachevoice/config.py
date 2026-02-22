from pydantic_settings import BaseSettings
from pydantic import BaseModel, model_validator
from typing import Any
import os
import re
import yaml

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(
            lambda m: os.environ.get(m.group(1), m.group(0)), value
        )
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


class ProviderConfig(BaseModel):
    litellm_model: str = ""
    base_url: str = ""
    api_key: str = ""
    default_voice: str = ""
    timeout: int = 15

    # Legacy compat
    model: str = ""

    @model_validator(mode="after")
    def _migrate_legacy_model(self) -> "ProviderConfig":
        if self.model and not self.litellm_model:
            self.litellm_model = self.model
        return self


class ProvidersConfig(BaseModel):
    default: str = ""
    fallback_chain: list[str] = []
    configs: dict[str, ProviderConfig] = {}

    @model_validator(mode="before")
    @classmethod
    def _extract_provider_configs(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        configs: dict[str, Any] = {}
        reserved = {"default", "fallback_chain"}
        leftover: dict[str, Any] = {}
        for k, v in data.items():
            if k in reserved:
                leftover[k] = v
            elif isinstance(v, dict):
                configs[k] = v
            else:
                leftover[k] = v
        leftover["configs"] = configs
        return leftover


class FuzzyConfig(BaseModel):
    enabled: bool = False
    threshold: int = 90
    scorer: str = "token_sort_ratio"

class NormalizeConfig(BaseModel):
    lowercase: bool = True
    strip_punctuation: bool = True
    collapse_whitespace: bool = True
    replace_numbers: bool = True
    strip_minimax: bool = True

class EvictionConfig(BaseModel):
    max_size_mb: int = 500
    max_entries: int = 50000
    max_text_length: int = 500
    cleanup_interval_hours: int = 1
    min_age_days: int = 7

class CacheConfig(BaseModel):
    audio_dir: str = "./data/audio"
    db_path: str = "./data/cache.db"
    enabled: bool = True
    fuzzy: FuzzyConfig = FuzzyConfig()
    normalize: NormalizeConfig = NormalizeConfig()
    eviction: EvictionConfig = EvictionConfig()

class FillerTemplate(BaseModel):
    id: str
    text: str

class FillerConfig(BaseModel):
    auto_generate_on_startup: bool = False
    voice_id: str = ""
    templates: list[FillerTemplate] = []

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8844
    log_level: str = "info"

class Settings(BaseSettings):
    model_config = {"extra": "allow"}
    
    server: ServerConfig = ServerConfig()
    providers: ProvidersConfig = ProvidersConfig()
    cache: CacheConfig = CacheConfig()
    fillers: FillerConfig = FillerConfig()
    voice_mapping: dict[str, dict[str, str]] = {}
    model_mapping: dict[str, dict[str, str]] = {}

    @classmethod
    def from_yaml(cls, path: str = "cachevoice.yaml") -> "Settings":
        with open(path) as f:
            data = yaml.safe_load(f)
        data = _resolve_env_vars(data)
        return cls(**data)

    def get_provider(self, name: str | None = None) -> ProviderConfig:
        key = name or self.providers.default
        return self.providers.configs[key]

    def map_voice(self, voice: str, provider: str) -> str:
        return self.voice_mapping.get(provider, {}).get(voice, voice)

    def map_model(self, model: str, provider: str) -> str:
        return self.model_mapping.get(provider, {}).get(model, model)
