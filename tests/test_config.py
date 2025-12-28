from __future__ import annotations

from factorio_mcp.config import FactorioConfig


def test_env_file_overrides_previous_values(tmp_path):
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
