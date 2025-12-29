"""Model Context Protocol server that bridges Factorio via RCON."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from factorio_mcp.config import FactorioConfig
from factorio_mcp.rcon import RconAuthError, RconClient, RconProtocolError


class Position(BaseModel):
    x: float
    y: float


class EntityPlacement(BaseModel):
    name: str = Field(description="Prototype name, e.g. 'electric-mining-drill'.")
    position: Position = Field(description="Entity placement position.")
    direction: Optional[int] = Field(
        default=None,
        description="Optional Factorio direction integer (0-7).",
    )


class FactorioBridge:
    """High-level commands that speak to Factorio via RCON /c Lua snippets."""

    def __init__(self, config: FactorioConfig):
        self.config = config
        self._client = RconClient(config)

    def ping(self) -> Dict[str, Any]:
        lua_body = "return {ok=true,type='ping',tick=game.tick}"
        return self._execute_lua(lua_body)

    def list_players(self) -> Dict[str, Any]:
        lua_body = """
        local players = {}
        for _, player in pairs(game.players) do
            players[#players + 1] = {
                name = player.name,
                index = player.index,
                connected = player.connected,
                afk_time = player.afk_time,
                online_time = player.online_time,
                surface = player.surface and player.surface.name or nil,
                position = player.position,
            }
        end
        return { ok = true, players = players }
        """
        return self._execute_lua(lua_body)

    def player_state(self, player: str) -> Dict[str, Any]:
        lua_body = """
        if payload == nil or payload.player == nil then
            return { ok = false, error = "player is required" }
        end

        local player = game.players[payload.player]
        if player == nil then
            return { ok = false, error = "player not found" }
        end

        local inventory = {}
        local main_inventory = player.get_main_inventory()
        if main_inventory then
            for name, count in pairs(main_inventory.get_contents()) do
                inventory[name] = count
            end
        end

        return {
            ok = true,
            player = {
                name = player.name,
                index = player.index,
                connected = player.connected,
                surface = player.surface and player.surface.name or nil,
                position = player.position,
                health = player.character and player.character.health or nil,
                crafting_queue = player.crafting_queue,
                inventory = inventory,
            },
        }
        """
        return self._execute_lua(lua_body, {"player": player})

    def find_resources(
        self, resource_name: str, position: Position, radius: float, surface: Optional[str]
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "resource": resource_name,
            "position": position.model_dump(),
            "radius": radius,
        }
        if surface:
            payload["surface"] = surface

        lua_body = """
        if payload.resource == nil then
            return { ok = false, error = "resource is required" }
        end

        if payload.position == nil then
            return { ok = false, error = "position is required" }
        end

        if payload.position.x == nil or payload.position.y == nil then
            return { ok = false, error = "Position with x/y is required." }
        end

        local radius = payload.radius or 32
        local surface = game.surfaces[payload.surface or 1]
        if surface == nil then
            return { ok = false, error = "surface not found" }
        end

        local left_top = { x = payload.position.x - radius, y = payload.position.y - radius }
        local right_bottom = { x = payload.position.x + radius, y = payload.position.y + radius }

        local resources = surface.find_entities_filtered {
            type = "resource",
            name = payload.resource,
            area = { left_top, right_bottom },
        }

        local results = {}
        for _, resource in pairs(resources) do
            results[#results + 1] = {
                name = resource.name,
                position = resource.position,
                amount = resource.amount,
                surface = resource.surface.name,
            }
        end

        table.sort(results, function(a, b)
            local dx1 = a.position.x - payload.position.x
            local dy1 = a.position.y - payload.position.y
            local dx2 = b.position.x - payload.position.x
            local dy2 = b.position.y - payload.position.y
            return (dx1 * dx1 + dy1 * dy1) < (dx2 * dx2 + dy2 * dy2)
        end)

        if #results > 128 then
            while #results > 128 do
                table.remove(results)
            end
        end

        return { ok = true, results = results, center = payload.position, radius = radius }
        """

        return self._execute_lua(lua_body, payload)

    def build_entities(
        self,
        player: str,
        entities: List[EntityPlacement],
        consume_items: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "player": player,
            "entities": [entity.model_dump() for entity in entities],
            "consume_items": consume_items,
        }
        lua_body = """
        if payload.player == nil then
            return { ok = false, error = "player is required" }
        end

        if payload.entities == nil then
            return { ok = false, error = "entities is required" }
        end

        local player = game.players[payload.player]
        if player == nil then
            return { ok = false, error = "player not found" }
        end

        local consume_items = payload.consume_items ~= false
        local surface = player.surface
        local results = {}
        local errors = {}

        for _, definition in pairs(payload.entities) do
            local name = definition.name
            local position = definition.position

            if name == nil or position == nil then
                errors[#errors + 1] = "Entity definition missing name or position."
            else
                local direction = definition.direction
                local created = surface.create_entity {
                    name = name,
                    position = position,
                    direction = direction,
                    force = player.force,
                    player = player,
                    raise_built = true,
                    fast_replace = true,
                }

                local built = created ~= nil and created.valid

                if built and consume_items then
                    local removed = player.remove_item { name = name, count = 1 }
                    if removed == 0 then
                        created.destroy { raise_destroy = true }
                        built = false
                        errors[#errors + 1] = "Missing item: " .. name
                    end
                end

                results[#results + 1] = {
                    name = name,
                    position = position,
                    direction = direction,
                    built = built,
                }

                if not built then
                    errors[#errors + 1] = "Failed to create entity: " .. name
                end
            end
        end

        return {
            ok = #errors == 0,
            built = results,
            errors = errors,
        }
        """
        return self._execute_lua(lua_body, payload)

    def teleport_player(self, player: str, position: Position, surface: Optional[str]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"player": player, "position": position.model_dump()}
        if surface:
            payload["surface"] = surface
        lua_body = """
        if payload.player == nil then
            return { ok = false, error = "player is required" }
        end

        if payload.position == nil then
            return { ok = false, error = "position is required" }
        end

        if payload.position.x == nil or payload.position.y == nil then
            return { ok = false, error = "Position with x/y is required." }
        end

        local player = game.players[payload.player]
        if player == nil then
            return { ok = false, error = "player not found" }
        end

        local surface = payload.surface and game.surfaces[payload.surface] or player.surface
        if surface == nil then
            return { ok = false, error = "surface not found" }
        end

        player.teleport(payload.position, surface)
        return { ok = true, player = player.name, position = payload.position, surface = surface.name }
        """
        return self._execute_lua(lua_body, payload)

    def _build_lua_command(self, lua_body: str, payload: Optional[Dict[str, Any]] = None) -> str:
        payload_json = json.dumps(payload or {}, separators=(",", ":"))
        lua = f"""
        local payload = game.json_to_table([[{payload_json}]])
        local function __mcp_main(payload)
            {lua_body}
        end
        local __mcp_ok, __mcp_result = pcall(__mcp_main, payload)
        if not __mcp_ok then
            rcon.print(game.table_to_json({{ ok = false, error = tostring(__mcp_result) }}))
        else
            rcon.print(game.table_to_json(__mcp_result))
        end
        """
        normalized = " ".join(lua.split())
        return f"/c {normalized}"

    def _execute_lua(self, lua_body: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        command = self._build_lua_command(lua_body, payload)
        return self._client.execute_json(command)


ENV_FILE_ENV_VAR = "FACTORIO_MCP_ENV_FILE"


def _resolve_env_file(env_file: Optional[Path] = None) -> Optional[Path]:
    """Return the env file from CLI or a fallback environment variable."""

    if env_file:
        return env_file

    env_value = os.getenv(ENV_FILE_ENV_VAR)
    if env_value:
        return Path(env_value).expanduser()
    return None


def build_server(config: FactorioConfig) -> FastMCP:
    bridge = FactorioBridge(config)
    server = FastMCP("factorio-mcp", dependencies=("factorio-mcp",))
    server.factorio_config = config  # type: ignore[attr-defined]

    @server.tool()
    async def ping(context: Context) -> Dict[str, Any]:  # noqa: ARG001
        """Verify that Factorio is reachable via RCON."""

        return bridge.ping()

    @server.tool()
    async def list_players(context: Context) -> Dict[str, Any]:  # noqa: ARG001
        """Return a list of online players and their surfaces/positions."""

        return bridge.list_players()

    @server.tool()
    async def player_state(context: Context, player: str) -> Dict[str, Any]:  # noqa: ARG001
        """Return state for the given player (position, surface, inventory counts)."""

        return bridge.player_state(player)

    @server.tool()
    async def find_resources(
        context: Context,
        resource_name: str,
        x: float,
        y: float,
        radius: float = 64,
        surface: Optional[str] = None,
    ) -> Dict[str, Any]:  # noqa: ARG001
        """Find resources near a coordinate. Returns up to 128 entries ordered by distance."""

        return bridge.find_resources(resource_name, Position(x=x, y=y), radius, surface)

    @server.tool()
    async def build_entities(  # noqa: ARG001
        context: Context,
        player: str,
        entities: List[Dict[str, Any]],
        consume_items: bool = True,
    ) -> Dict[str, Any]:
        """
        Ask Factorio to place entities for a player via RCON.

        Each entity dictionary should include 'name', 'position' with x/y, and optional 'direction'.
        """

        parsed_entities = [EntityPlacement.model_validate(entity) for entity in entities]
        return bridge.build_entities(player, parsed_entities, consume_items=consume_items)

    @server.tool()
    async def teleport_player(  # noqa: ARG001
        context: Context, player: str, x: float, y: float, surface: Optional[str] = None
    ) -> Dict[str, Any]:
        """Teleport a player to coordinates (useful for setup/testing)."""

        return bridge.teleport_player(player, Position(x=x, y=y), surface)

    return server


# Expose a module-level FastMCP instance so `mcp run`/`mcp dev` can import it directly.
app = build_server(FactorioConfig.load(_resolve_env_file()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Factorio MCP server.")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional path to a .env file containing FACTORIO_RCON_* variables.",
    )
    args = parser.parse_args()

    env_file = _resolve_env_file(args.env_file)
    config = FactorioConfig.load(env_file)

    try:
        server = build_server(config)
        server.run()
    except (RconAuthError, RconProtocolError) as exc:
        print(json.dumps({"error": str(exc)}))


if __name__ == "__main__":
    main()
