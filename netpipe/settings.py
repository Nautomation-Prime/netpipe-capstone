#!/usr/bin/env python3
"""
Runtime configuration and credentials.

Values come from the environment or a .env file. Nothing here is ever
committed; .env is in .gitignore.
"""

from typing import Annotated, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NETAUTO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Credentials — no defaults, deliberately
    username: Annotated[str, Field(min_length=1)]
    password: SecretStr
    enable_secret: SecretStr | None = None

    # Execution controls
    max_workers: Annotated[int, Field(ge=1, le=50)] = 10
    batch_size: Annotated[int, Field(ge=1, le=100)] = 5
    connect_timeout: Annotated[int, Field(ge=5, le=120)] = 20

    # Safety controls
    require_approval: bool = True
    min_free_flash_mb: Annotated[int, Field(ge=0)] = 20
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


def load_settings() -> Settings:
    """
    Load settings, failing immediately and clearly if anything is missing.
    """
    from pydantic import ValidationError

    try:
        return Settings()
    except ValidationError as exc:
        missing = [str(err["loc"][0]).upper() for err in exc.errors()
                   if err["type"] == "missing"]
        if missing:
            names = ", ".join(f"NETAUTO_{name}" for name in missing)
            raise SystemExit(
                f"✗ Missing required configuration: {names}\n"
                f"  Set them in your environment or in a .env file."
            ) from exc
        raise SystemExit(f"✗ Invalid configuration:\n{exc}") from exc
