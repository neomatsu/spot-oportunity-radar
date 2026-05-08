from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.models import AppAssetList, DataSourcesConfigModel


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    env: str = "local"
    db_url: str = "sqlite:///spot_opportunity_radar.db"
    log_level: str = "INFO"
    config_dir: str = "config"
    demo_mode: bool = True
    telegram_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    @property
    def root_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent

    @property
    def config_path(self) -> Path:
        return self.root_dir / self.config_dir


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    alphavantage_api_key: str | None = None
    fmp_api_key: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_provider_settings() -> ProviderSettings:
    return ProviderSettings()


def load_yaml_config(name: str) -> dict[str, Any]:
    path = get_settings().config_path / name
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def deep_merge_configs(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge override into base recursively. Override values take precedence."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge_configs(result[key], value)
        else:
            result[key] = value
    return result


def load_assets_config() -> AppAssetList:
    return AppAssetList.model_validate(load_yaml_config("assets.yaml"))


def load_data_sources_config() -> DataSourcesConfigModel:
    return DataSourcesConfigModel.model_validate(load_yaml_config("data_sources.yaml"))
