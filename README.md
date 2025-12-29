# Factorio MCP

Model Context Protocol server that allows an LLM to control gameplay through RCON.

## Overview

This project is a **Python MCP server** (`factorio-mcp`) that exposes tools the LLM can call (list players, inspect state, place entities, etc.). The server talks directly to the Factorio server over RCON using `/c` Lua commands—no Factorio-side mod is required.

The intended use-case is multiplayer: an LLM plays alongside humans by issuing MCP tool calls that translate into in-game actions.

## Getting started

### Prerequisites

- Python 3.10+
- A running Factorio headless server with RCON enabled.
- Console commands allowed for RCON (`allow-commands=admins-only` or broader).

### Configure RCON access

Set the following environment variables (or place them in a `.env` file):

```
FACTORIO_RCON_HOST=127.0.0.1
FACTORIO_RCON_PORT=27015
FACTORIO_RCON_PASSWORD=secret
# Optional
FACTORIO_RCON_TIMEOUT=5.0
```

### Install the MCP server

```bash
pip install .
```

Run the server:

```bash
factorio-mcp --env-file .env
```

The process starts a Model Context Protocol server exposing the Factorio tools.

When launching the server via the MCP CLI (`mcp run` / `mcp dev`), set `FACTORIO_MCP_ENV_FILE`
to point at your `.env` so the module-level FastMCP app can load credentials:

```bash
export FACTORIO_MCP_ENV_FILE=/path/to/.env
mcp dev src/factorio_mcp/server.py:app --with-editable .
```

### Server command permissions

Because the MCP server sends `/c` commands over RCON, your Factorio server must allow console commands for RCON (the default `allow-commands=admins-only` works, since RCON is treated as admin). If your server disables `/c`, enable it on a development server before use.

## Available tools

The MCP server exposes these tools to the LLM:

- `ping` – verifies connectivity with the Factorio server.
- `list_players` – returns connected players and locations.
- `player_state(player)` – reports position, surface, health, and main inventory counts for a player.
- `find_resources(resource_name, x, y, radius, surface?)` – finds resource entities near a point.
- `build_entities(player, entities, consume_items?)` – asks the server to place entities on behalf of a player (optionally consuming items from their inventory).
- `teleport_player(player, x, y, surface?)` – teleports a player (useful for setup/testing).

## How it works

The MCP server translates tool calls into Lua snippets sent via `/c` over RCON. Each snippet validates inputs, runs the relevant game-side logic, and emits structured JSON back through `rcon.print` for the MCP client to parse.

## Testing

Install dev dependencies and run the Python test suite:

```bash
pip install .[dev]
pytest
```

These tests cover the RCON packet encoding/decoding logic and the JSON command shaping utilities to catch regressions before integrating with a Factorio server.
