"""Which providers exist, and where their keys come from.

A provider is three facts: a name, a base URL, and the *names* of the
environment variables holding its keys. Key values never appear here, so this
file is safe to commit. Adding a fourth provider is a config change only.
"""
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_smith.config import ConfigError, load_json_model

DEFAULT_PROVIDERS_PATH = "configs/providers.json"


class ProviderConfig(BaseModel):
    """One provider. Ours, not the grader's, so unknown fields are an error."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    base_url: str = Field(..., min_length=1)
    key_env_vars: List[str] = Field(..., min_length=1)
    models: List[str] = Field(default_factory=list, description="Known free models, for reference only")

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("must be an http(s) URL")
        return value.rstrip("/")


class ProvidersFile(BaseModel):
    """The whole `configs/providers.json`."""

    model_config = ConfigDict(extra="forbid")

    providers: List[ProviderConfig] = Field(..., min_length=1)

    @field_validator("providers")
    @classmethod
    def _unique_names(cls, value: List[ProviderConfig]) -> List[ProviderConfig]:
        names = [p.name for p in value]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate provider names: {', '.join(duplicates)}")
        return value


def load_providers(path: str | Path = DEFAULT_PROVIDERS_PATH) -> ProvidersFile:
    return load_json_model(path, ProvidersFile)


def resolve_provider(providers: ProvidersFile, base_url: Optional[str]) -> ProviderConfig:
    """Find the provider that serves `base_url`.

    The CLI receives a URL (`--provider-url`), not a provider name, so the URL
    is the key we look up. Matching ignores a trailing slash and is
    case-insensitive on scheme and host, which is where copy-pasted URLs differ.
    No URL at all means "the first provider in the file", so a bare
    `scripts/llm_ping.py` still does something useful.
    """
    if not base_url:
        return providers.providers[0]
    wanted = _normalise(base_url)
    for provider in providers.providers:
        if _normalise(provider.base_url) == wanted:
            return provider
    known = ", ".join(p.base_url for p in providers.providers)
    raise ConfigError(f"Unknown provider URL {base_url}. Known URLs: {known}")


def _normalise(url: str) -> str:
    parts = urlsplit(url.rstrip("/"))
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}"
