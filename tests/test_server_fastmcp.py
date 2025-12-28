from __future__ import annotations

from importlib import reload
from pathlib import Path

import factorio_mcp.server as server_module
from mcp.server.fastmcp import FastMCP


def _reload_server(monkeypatch, env_file: Path | None = None):
    if env_file:
        monkeypatch.setenv("FACTORIO_MCP_ENV_FILE", str(env_file))
    else:
        monkeypatch.delenv("FACTORIO_MCP_ENV_FILE", raising=False)
    return reload(server_module)


def test_app_is_fastmcp(monkeypatch):
    server = _reload_server(monkeypatch)

    assert isinstance(server.app, FastMCP)
    assert server.app.name == "factorio-mcp"
    assert "factorio-mcp" in server.app.dependencies


def test_env_file_env_var_used(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "FACTORIO_RCON_HOST=192.0.2.10",
                "FACTORIO_RCON_PORT=12345",
                "FACTORIO_RCON_PASSWORD=hunter2",
                "FACTORIO_RCON_TIMEOUT=1.5",
            ]
        )
    )

    server = _reload_server(monkeypatch, env_file)

    config = server.app.factorio_config  # type: ignore[attr-defined]
    assert config.host == "192.0.2.10"
    assert config.port == 12345
    assert config.password == "hunter2"
    assert config.timeout == 1.5

    _reload_server(monkeypatch)
