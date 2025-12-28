from __future__ import annotations

import os

from factorio_mcp.config import FactorioConfig


def test_env_file_overrides_previous_values(tmp_path, monkeypatch):
    monkeypatch.delenv("FACTORIO_RCON_HOST", raising=False)
    monkeypatch.delenv("FACTORIO_RCON_PASSWORD", raising=False)

    env_first = tmp_path / ".env.first"
    env_first.write_text(
        "\n".join(
            [
                "FACTORIO_RCON_HOST=192.0.2.10",
                "FACTORIO_RCON_PORT=12345",
                "FACTORIO_RCON_PASSWORD=first",
                "FACTORIO_RCON_TIMEOUT=1.5",
            ]
        )
    )

    env_second = tmp_path / ".env.second"
    env_second.write_text(
        "\n".join(
            [
                "FACTORIO_RCON_HOST=198.51.100.20",
                "FACTORIO_RCON_PORT=45678",
                "FACTORIO_RCON_PASSWORD=second",
                "FACTORIO_RCON_TIMEOUT=2.5",
            ]
        )
    )

    config_first = FactorioConfig.load(env_first)
    assert config_first.host == "192.0.2.10"
    assert config_first.port == 12345
    assert config_first.password == "first"
    assert config_first.timeout == 1.5

    config_second = FactorioConfig.load(env_second)
    assert config_second.host == "198.51.100.20"
    assert config_second.port == 45678
    assert config_second.password == "second"
    assert config_second.timeout == 2.5

    # Ensure os.environ was not mutated by FactorioConfig.load
    assert os.getenv("FACTORIO_RCON_HOST") is None
    assert os.getenv("FACTORIO_RCON_PASSWORD") is None


def test_env_file_missing_values_do_not_leak(tmp_path, monkeypatch):
    monkeypatch.delenv("FACTORIO_RCON_HOST", raising=False)
    monkeypatch.delenv("FACTORIO_RCON_PASSWORD", raising=False)

    env_first = tmp_path / ".env.first"
    env_first.write_text(
        "\n".join(
            [
                "FACTORIO_RCON_HOST=192.0.2.10",
                "FACTORIO_RCON_PASSWORD=hunter2",
            ]
        )
    )

    env_second = tmp_path / ".env.second"
    env_second.write_text("FACTORIO_RCON_HOST=198.51.100.20")

    _ = FactorioConfig.load(env_first)
    config_second = FactorioConfig.load(env_second)

    assert config_second.host == "198.51.100.20"
    # Password should fall back to default because it is missing in env_second.
    assert config_second.password == ""
