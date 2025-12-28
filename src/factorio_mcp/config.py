"""Configuration helpers for the Factorio MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from dotenv import dotenv_values
from pydantic import BaseModel, Field


class FactorioConfig(BaseModel):
    """Runtime configuration for connecting to a Factorio server via RCON."""

    host: str = Field(default="127.0.0.1", description="Factorio RCON host.")
    port: int = Field(default=27015, description="Factorio RCON port.")
    password: str = Field(default="", description="Factorio RCON password.")
    timeout: float = Field(default=5.0, description="Socket timeout in seconds.")

    @classmethod
    def load(cls, env_file: Optional[Path] = None) -> "FactorioConfig":
        """Load configuration from environment variables (optionally an env file).

        The env file is loaded with override semantics so later calls can replace
        variables that were seeded during earlier imports.
        """

        if env_file:
            load_dotenv(env_file, override=True)

        env_values = dotenv_values(env_file) if env_file else {}

        def lookup(key: str, default: str) -> str:
            if (value := env_values.get(key)) is not None:
                return value

            import os

            return os.getenv(key, default)

        return cls(
            host=lookup("FACTORIO_RCON_HOST", "127.0.0.1"),
            port=int(lookup("FACTORIO_RCON_PORT", "27015")),
            password=lookup("FACTORIO_RCON_PASSWORD", ""),
            timeout=float(lookup("FACTORIO_RCON_TIMEOUT", "5.0")),
        )
