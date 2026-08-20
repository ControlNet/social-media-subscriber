"""Secret-backed runtime settings."""

from typing import ClassVar

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Frozen multiline account and provider credential settings."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        case_sensitive=False,
        env_prefix="",
        extra="forbid",
        frozen=True,
    )

    accounts: SecretStr
    bright_data_api_keys: SecretStr
