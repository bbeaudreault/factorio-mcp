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

# Human-like limitation constants
BUILD_DISTANCE = 6  # Factorio's default build distance in tiles
VIEWPORT_RADIUS = 100  # Max zoom-out limit (~200 tiles across)
MOVE_DISTANCE = 50  # Max teleport distance per move
CHUNK_SIZE = 32  # Factorio chunk size


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
        self, player: str, resource_name: str, offset: Position, radius: float
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "player": player,
            "resource": resource_name,
            "offset": offset.model_dump(),
            "radius": radius,
        }

        lua_body = """
        if payload.player == nil then
            return { ok = false, error = "player is required" }
        end

        if payload.resource == nil then
            return { ok = false, error = "resource is required" }
        end

        local player = game.players[payload.player]
        if player == nil then
            return { ok = false, error = "player not found" }
        end

        local player_pos = player.position
        local offset = payload.offset or { x = 0, y = 0 }
        local viewport_radius = payload.viewport_radius or 100

        local offset_dist = math.sqrt((offset.x or 0) * (offset.x or 0) + (offset.y or 0) * (offset.y or 0))
        if offset_dist > viewport_radius then
            return {
                ok = false,
                error = "Search offset (" .. string.format("%.1f", offset_dist) .. " tiles) is beyond visible viewport (" .. viewport_radius .. " tiles). Use view_map for distant areas.",
                player_position = player_pos,
            }
        end

        local center = {
            x = player_pos.x + (offset.x or 0),
            y = player_pos.y + (offset.y or 0),
        }

        local radius = math.min(payload.radius or 32, viewport_radius)
        local surface = player.surface

        local left_top = { x = center.x - radius, y = center.y - radius }
        local right_bottom = { x = center.x + radius, y = center.y + radius }

        local resources = surface.find_entities_filtered {
            type = "resource",
            name = payload.resource,
            area = { left_top, right_bottom },
        }

        local results = {}
        for _, resource in pairs(resources) do
            local rel_pos = {
                x = resource.position.x - player_pos.x,
                y = resource.position.y - player_pos.y,
            }
            results[#results + 1] = {
                name = resource.name,
                position = rel_pos,
                absolute_position = resource.position,
                amount = resource.amount,
            }
        end

        table.sort(results, function(a, b)
            local dx1 = a.position.x - offset.x
            local dy1 = a.position.y - offset.y
            local dx2 = b.position.x - offset.x
            local dy2 = b.position.y - offset.y
            return (dx1 * dx1 + dy1 * dy1) < (dx2 * dx2 + dy2 * dy2)
        end)

        if #results > 128 then
            while #results > 128 do
                table.remove(results)
            end
        end

        return {
            ok = true,
            results = results,
            player_position = player_pos,
            search_center = center,
            radius = radius,
        }
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
        local player_pos = player.position

        local to_build = {}
        local validation_errors = {}

        local build_distance = payload.build_distance or 6

        for i, definition in pairs(payload.entities) do
            local name = definition.name
            local rel_position = definition.position

            if name == nil or rel_position == nil then
                validation_errors[#validation_errors + 1] = "Entity " .. i .. ": missing name or position."
            else
                local dist = math.sqrt(rel_position.x * rel_position.x + rel_position.y * rel_position.y)
                if dist > build_distance then
                    validation_errors[#validation_errors + 1] = "Entity " .. name .. " at offset (" .. rel_position.x .. ", " .. rel_position.y .. ") is " .. string.format("%.1f", dist) .. " tiles away, max build distance is " .. build_distance .. " tiles"
                else
                    local direction = definition.direction or 0
                    local abs_position = {
                        x = player_pos.x + rel_position.x,
                        y = player_pos.y + rel_position.y,
                    }

                    local can_place = surface.can_place_entity {
                        name = name,
                        position = abs_position,
                        direction = direction,
                        force = player.force,
                    }

                    if not can_place then
                        validation_errors[#validation_errors + 1] = "Cannot place " .. name .. " at offset (" .. rel_position.x .. ", " .. rel_position.y .. ") - invalid terrain or collision"
                    else
                        if consume_items then
                            local count = player.get_item_count(name)
                            local needed = 0
                            for _, queued in pairs(to_build) do
                                if queued.name == name then
                                    needed = needed + 1
                                end
                            end
                            if count <= needed then
                                validation_errors[#validation_errors + 1] = "Not enough " .. name .. " in inventory (have " .. count .. ", need " .. (needed + 1) .. ")"
                            end
                        end
                    end

                    to_build[#to_build + 1] = {
                        name = name,
                        rel_position = rel_position,
                        abs_position = abs_position,
                        direction = direction,
                    }
                end
            end
        end

        if #validation_errors > 0 then
            return {
                ok = false,
                error = "Validation failed - no entities placed",
                validation_errors = validation_errors,
                player_position = player_pos,
            }
        end

        local results = {}
        local build_errors = {}

        for _, item in pairs(to_build) do
            local created = surface.create_entity {
                name = item.name,
                position = item.abs_position,
                direction = item.direction,
                force = player.force,
                player = player,
                raise_built = true,
            }

            local built = created ~= nil and created.valid

            if built and consume_items then
                player.remove_item { name = item.name, count = 1 }
            end

            results[#results + 1] = {
                name = item.name,
                relative_position = item.rel_position,
                absolute_position = item.abs_position,
                direction = item.direction,
                built = built,
            }

            if not built then
                build_errors[#build_errors + 1] = "Failed to create entity: " .. item.name
            end
        end

        return {
            ok = #build_errors == 0,
            built = results,
            player_position = player_pos,
            errors = build_errors,
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

    def find_entities(
        self,
        player: str,
        offset: Position,
        radius: float,
        entity_type: Optional[str] = None,
        entity_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "player": player,
            "offset": offset.model_dump(),
            "radius": radius,
        }
        if entity_type:
            payload["entity_type"] = entity_type
        if entity_name:
            payload["entity_name"] = entity_name

        lua_body = """
        if payload.player == nil then
            return { ok = false, error = "player is required" }
        end

        local player = game.players[payload.player]
        if player == nil then
            return { ok = false, error = "player not found" }
        end

        local player_pos = player.position
        local offset = payload.offset or { x = 0, y = 0 }
        local viewport_radius = payload.viewport_radius or 100

        local offset_dist = math.sqrt((offset.x or 0) * (offset.x or 0) + (offset.y or 0) * (offset.y or 0))
        if offset_dist > viewport_radius then
            return {
                ok = false,
                error = "Search offset (" .. string.format("%.1f", offset_dist) .. " tiles) is beyond visible viewport (" .. viewport_radius .. " tiles). Use view_map for distant areas.",
                player_position = player_pos,
            }
        end

        local center = {
            x = player_pos.x + (offset.x or 0),
            y = player_pos.y + (offset.y or 0),
        }

        local radius = math.min(payload.radius or 32, viewport_radius)
        local surface = player.surface

        local left_top = { x = center.x - radius, y = center.y - radius }
        local right_bottom = { x = center.x + radius, y = center.y + radius }

        local filter = {
            area = { left_top, right_bottom },
        }
        if payload.entity_type then
            filter.type = payload.entity_type
        end
        if payload.entity_name then
            filter.name = payload.entity_name
        end

        local entities = surface.find_entities_filtered(filter)

        local results = {}
        for _, entity in pairs(entities) do
            if entity.valid and entity.name ~= "character" then
                local rel_pos = {
                    x = entity.position.x - player_pos.x,
                    y = entity.position.y - player_pos.y,
                }
                results[#results + 1] = {
                    name = entity.name,
                    type = entity.type,
                    position = rel_pos,
                    absolute_position = entity.position,
                    direction = entity.direction,
                    health = entity.health,
                    unit_number = entity.unit_number,
                }
            end
        end

        table.sort(results, function(a, b)
            local dx1 = a.position.x - offset.x
            local dy1 = a.position.y - offset.y
            local dx2 = b.position.x - offset.x
            local dy2 = b.position.y - offset.y
            return (dx1 * dx1 + dy1 * dy1) < (dx2 * dx2 + dy2 * dy2)
        end)

        if #results > 200 then
            while #results > 200 do
                table.remove(results)
            end
        end

        return {
            ok = true,
            entities = results,
            player_position = player_pos,
            search_center = center,
            radius = radius,
        }
        """

        return self._execute_lua(lua_body, payload)

    def deconstruct_area(
        self,
        player: str,
        offset: Position,
        radius: float,
        give_items: bool = True,
        entity_type: Optional[str] = None,
        entity_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "player": player,
            "offset": offset.model_dump(),
            "radius": radius,
            "give_items": give_items,
        }
        if entity_type:
            payload["entity_type"] = entity_type
        if entity_name:
            payload["entity_name"] = entity_name

        lua_body = """
        if payload.player == nil then
            return { ok = false, error = "player is required" }
        end

        local player = game.players[payload.player]
        if player == nil then
            return { ok = false, error = "player not found" }
        end

        local player_pos = player.position
        local offset = payload.offset or { x = 0, y = 0 }
        local center = {
            x = player_pos.x + (offset.x or 0),
            y = player_pos.y + (offset.y or 0),
        }

        local radius = payload.radius or 32
        local surface = player.surface

        local left_top = { x = center.x - radius, y = center.y - radius }
        local right_bottom = { x = center.x + radius, y = center.y + radius }

        local filter = {
            area = { left_top, right_bottom },
            force = player.force,
        }
        if payload.entity_type then
            filter.type = payload.entity_type
        end
        if payload.entity_name then
            filter.name = payload.entity_name
        end

        local entities = surface.find_entities_filtered(filter)

        local results = {}
        local out_of_reach = {}
        local errors = {}
        local give_items = payload.give_items ~= false
        local build_distance = payload.build_distance or 6

        for _, entity in pairs(entities) do
            if entity.valid and entity.name ~= "character" and entity.minable then
                local name = entity.name
                local abs_pos = entity.position
                local rel_pos = {
                    x = abs_pos.x - player_pos.x,
                    y = abs_pos.y - player_pos.y,
                }

                local entity_dist = math.sqrt(rel_pos.x * rel_pos.x + rel_pos.y * rel_pos.y)
                if entity_dist > build_distance then
                    out_of_reach[#out_of_reach + 1] = {
                        name = name,
                        position = rel_pos,
                        absolute_position = abs_pos,
                        distance = entity_dist,
                    }
                else
                    local products = entity.prototype.mineable_properties.products
                    if give_items and products then
                        for _, product in pairs(products) do
                            if product.type == "item" then
                                local count = product.amount or 1
                                player.insert { name = product.name, count = count }
                            end
                        end
                    end

                    entity.destroy { raise_destroy = true }

                    results[#results + 1] = {
                        name = name,
                        position = rel_pos,
                        absolute_position = abs_pos,
                        deconstructed = true,
                    }
                end
            end
        end

        return {
            ok = true,
            deconstructed = results,
            count = #results,
            out_of_reach = out_of_reach,
            out_of_reach_count = #out_of_reach,
            player_position = player_pos,
            errors = errors,
        }
        """
        return self._execute_lua(lua_body, payload)

    def move_player(
        self,
        player: str,
        offset: Position,
        max_distance: float = 50.0,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "player": player,
            "offset": offset.model_dump(),
            "max_distance": max_distance,
        }
        lua_body = """
        if payload.player == nil then
            return { ok = false, error = "player is required" }
        end

        local player = game.players[payload.player]
        if player == nil then
            return { ok = false, error = "player not found" }
        end

        local offset = payload.offset or { x = 0, y = 0 }
        local max_distance = payload.max_distance or 50

        local dist = math.sqrt((offset.x or 0) * (offset.x or 0) + (offset.y or 0) * (offset.y or 0))
        if dist > max_distance then
            return {
                ok = false,
                error = "Move distance " .. string.format("%.1f", dist) .. " tiles exceeds maximum of " .. max_distance .. " tiles",
                player_position = player.position,
            }
        end

        local player_pos = player.position
        local target = {
            x = player_pos.x + (offset.x or 0),
            y = player_pos.y + (offset.y or 0),
        }

        local surface = player.surface
        local non_colliding = surface.find_non_colliding_position(
            "character",
            target,
            2,
            0.5
        )

        if non_colliding == nil then
            return {
                ok = false,
                error = "Cannot move to position - blocked by terrain or entities",
                player_position = player_pos,
                attempted_target = target,
            }
        end

        player.teleport(non_colliding, surface)

        local final_offset = {
            x = non_colliding.x - player_pos.x,
            y = non_colliding.y - player_pos.y,
        }

        return {
            ok = true,
            player = player.name,
            previous_position = player_pos,
            new_position = non_colliding,
            actual_offset = final_offset,
        }
        """
        return self._execute_lua(lua_body, payload)

    def view_map(
        self,
        player: str,
        center: Position,
        radius: float = 64.0,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "player": player,
            "center": center.model_dump(),
            "radius": radius,
        }
        lua_body = """
        if payload.player == nil then
            return { ok = false, error = "player is required" }
        end

        local player = game.players[payload.player]
        if player == nil then
            return { ok = false, error = "player not found" }
        end

        local center = payload.center
        if center == nil or center.x == nil or center.y == nil then
            return { ok = false, error = "center position with x/y is required" }
        end

        local radius = payload.radius or 64
        local surface = player.surface
        local chunk_size = 32

        local min_chunk_x = math.floor((center.x - radius) / chunk_size)
        local max_chunk_x = math.floor((center.x + radius) / chunk_size)
        local min_chunk_y = math.floor((center.y - radius) / chunk_size)
        local max_chunk_y = math.floor((center.y + radius) / chunk_size)

        local chunks = {}

        for cx = min_chunk_x, max_chunk_x do
            for cy = min_chunk_y, max_chunk_y do
                local chunk_area = {
                    left_top = { x = cx * chunk_size, y = cy * chunk_size },
                    right_bottom = { x = (cx + 1) * chunk_size, y = (cy + 1) * chunk_size },
                }

                local resources = surface.find_entities_filtered {
                    type = "resource",
                    area = { chunk_area.left_top, chunk_area.right_bottom },
                }
                local resource_counts = {}
                for _, res in pairs(resources) do
                    resource_counts[res.name] = (resource_counts[res.name] or 0) + 1
                end

                local buildings = surface.find_entities_filtered {
                    area = { chunk_area.left_top, chunk_area.right_bottom },
                    force = player.force,
                }
                local building_counts = {
                    total = 0,
                    assemblers = 0,
                    inserters = 0,
                    belts = 0,
                    power = 0,
                    mining = 0,
                    other = 0,
                }
                for _, entity in pairs(buildings) do
                    if entity.valid and entity.name ~= "character" then
                        building_counts.total = building_counts.total + 1
                        local etype = entity.type
                        if etype == "assembling-machine" or etype == "furnace" then
                            building_counts.assemblers = building_counts.assemblers + 1
                        elseif etype == "inserter" then
                            building_counts.inserters = building_counts.inserters + 1
                        elseif etype == "transport-belt" or etype == "splitter" or etype == "underground-belt" then
                            building_counts.belts = building_counts.belts + 1
                        elseif etype == "electric-pole" or etype == "generator" or etype == "solar-panel" or etype == "accumulator" then
                            building_counts.power = building_counts.power + 1
                        elseif etype == "mining-drill" then
                            building_counts.mining = building_counts.mining + 1
                        else
                            building_counts.other = building_counts.other + 1
                        end
                    end
                end

                if next(resource_counts) ~= nil or building_counts.total > 0 then
                    chunks[#chunks + 1] = {
                        chunk = { x = cx, y = cy },
                        area = chunk_area,
                        resources = resource_counts,
                        buildings = building_counts,
                    }
                end
            end
        end

        return {
            ok = true,
            center = center,
            radius = radius,
            player_position = player.position,
            chunk_count = #chunks,
            chunks = chunks,
        }
        """
        return self._execute_lua(lua_body, payload)

    def _build_lua_command(self, lua_body: str, payload: Optional[Dict[str, Any]] = None) -> str:
        payload_json = json.dumps(payload or {}, separators=(",", ":"))
        lua = f"""
        local json_to_table = helpers and helpers.json_to_table or game.json_to_table
        local table_to_json = helpers and helpers.table_to_json or game.table_to_json
        local payload = json_to_table([[{payload_json}]])
        local function __mcp_main(payload)
            {lua_body}
        end
        local __mcp_ok, __mcp_result = pcall(__mcp_main, payload)
        if not __mcp_ok then
            rcon.print(table_to_json({{ ok = false, error = tostring(__mcp_result) }}))
        else
            rcon.print(table_to_json(__mcp_result))
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
        player: str,
        resource_name: str,
        offset_x: float = 0,
        offset_y: float = 0,
        radius: float = 64,
    ) -> Dict[str, Any]:  # noqa: ARG001
        """Find resources near player within visible viewport. Offset is relative to player position.

        LIMITATION: Search is limited to visible viewport (100 tiles from player).
        Use view_map for distant areas.
        Returns up to 128 entries."""

        return bridge.find_resources(player, resource_name, Position(x=offset_x, y=offset_y), radius)

    @server.tool()
    async def build_entities(  # noqa: ARG001
        context: Context,
        player: str,
        entities: List[Dict[str, Any]],
        consume_items: bool = True,
    ) -> Dict[str, Any]:
        """
        Place entities for a player at positions RELATIVE to the player's current location.

        LIMITATION: Entities must be within 6 tiles of the player (build distance).

        Each entity dictionary should include:
        - 'name': entity prototype name (e.g., 'assembling-machine-3')
        - 'position': {x, y} offset from player (e.g., {x: 5, y: 0} = 5 tiles east)
        - 'direction': optional, 0-15 (0=north, 4=east, 8=south, 12=west)

        Coordinate system: +x is east, +y is south.
        """

        parsed_entities = [EntityPlacement.model_validate(entity) for entity in entities]
        return bridge.build_entities(player, parsed_entities, consume_items=consume_items)

    @server.tool()
    async def teleport_player(  # noqa: ARG001
        context: Context, player: str, x: float, y: float, surface: Optional[str] = None
    ) -> Dict[str, Any]:
        """Teleport a player to coordinates (useful for setup/testing)."""

        return bridge.teleport_player(player, Position(x=x, y=y), surface)

    @server.tool()
    async def find_entities(  # noqa: ARG001
        context: Context,
        player: str,
        offset_x: float = 0,
        offset_y: float = 0,
        radius: float = 32,
        entity_type: Optional[str] = None,
        entity_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Find all entities near player within visible viewport. Offset is relative to player.

        LIMITATION: Search is limited to visible viewport (100 tiles from player).
        Use view_map for distant areas.

        Can filter by entity_type (e.g., 'assembling-machine', 'inserter', 'transport-belt')
        or entity_name (e.g., 'assembling-machine-3', 'fast-inserter').
        Returns up to 200 entries.
        """

        return bridge.find_entities(
            player, Position(x=offset_x, y=offset_y), radius, entity_type, entity_name
        )

    @server.tool()
    async def deconstruct_area(  # noqa: ARG001
        context: Context,
        player: str,
        offset_x: float = 0,
        offset_y: float = 0,
        radius: float = 32,
        give_items: bool = True,
        entity_type: Optional[str] = None,
        entity_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Deconstruct (pick up) player-owned entities in an area relative to player.

        LIMITATION: Only entities within 6 tiles of the player can be deconstructed.
        Entities beyond build distance are returned in 'out_of_reach' list.

        Items are returned to the player's inventory by default (give_items=True).
        Can filter by entity_type or entity_name to only deconstruct specific entities.
        """

        return bridge.deconstruct_area(
            player, Position(x=offset_x, y=offset_y), radius, give_items, entity_type, entity_name
        )

    @server.tool()
    async def move_player(  # noqa: ARG001
        context: Context,
        player: str,
        offset_x: float,
        offset_y: float,
    ) -> Dict[str, Any]:
        """Move player to a nearby position (limited to 50 tiles).

        Offset is relative to current player position.
        Coordinate system: +x is east, +y is south.

        Will find a non-colliding position near the target if the exact spot is blocked.
        For longer distances, call multiple times.
        """
        return bridge.move_player(player, Position(x=offset_x, y=offset_y))

    @server.tool()
    async def view_map(  # noqa: ARG001
        context: Context,
        player: str,
        center_x: float,
        center_y: float,
        radius: float = 64,
    ) -> Dict[str, Any]:
        """View a strategic overview of any map area (read-only).

        Returns chunk-based summaries (32x32 tile chunks) with:
        - Resource counts and types per chunk
        - Building counts by category (assemblers, inserters, belts, power, mining)

        This is a map view only - you cannot build or interact through this view.
        The position is absolute (not relative to player).
        Use move_player to get within build range of an area before interacting.
        """
        return bridge.view_map(player, Position(x=center_x, y=center_y), radius)

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
