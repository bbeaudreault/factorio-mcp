"""Configuration helpers for the Factorio MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field


class FactorioConfig(BaseModel):
    """Runtime configuration for connecting to a Factorio server via RCON."""

    host: str = Field(default="127.0.0.1", description="Factorio RCON host.")
    port: int = Field(default=27015, description="Factorio RCON port.")
    password: str = Field(default="", description="Factorio RCON password.")
    timeout: float = Field(default=5.0, description="Socket timeout in seconds.")

    @classmethod
    def load(cls, env_file: Optional[Path] = None) -> "FactorioConfig":
        """Load configuration from environment variables (optionally an env file)."""

        if env_file:
            load_dotenv(env_file)

        return cls(
            host=_env("FACTORIO_RCON_HOST", "127.0.0.1"),
            port=int(_env("FACTORIO_RCON_PORT", "27015")),
            password=_env("FACTORIO_RCON_PASSWORD", ""),
            timeout=float(_env("FACTORIO_RCON_TIMEOUT", "5.0")),
        )


def _env(key: str, default: str) -> str:
    import os

    return os.getenv(key, default)
