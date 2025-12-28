-- Factorio MCP Controller mod
-- Exposes a small command surface for orchestration via RCON.
-- APIs referenced were checked against the Factorio 2.0 Lua API:
-- commands.add_command, game.json_to_table, rcon.print, surface.create_entity,
-- player.remove_item, player.teleport, and LuaEntity.destroy (raise_destroy).

-- Send a JSON payload back over RCON.
local function respond(payload)
    rcon.print(game.table_to_json(payload))
end

local function mod_log(message)
    -- Write namespaced entries to the Factorio log for easier debugging.
    log("[mcp-controller] " .. message)
end

local function parse_parameter(parameter)
    -- RCON commands arrive as JSON strings; parse with error handling.
    if parameter == nil or parameter == "" then
        return nil, "Missing JSON payload."
    end

    local ok, payload = pcall(game.json_to_table, parameter)
    if not ok then
        return nil, "Invalid JSON payload."
    end

    return payload, nil
end

local function ensure_position(value)
    -- Basic validation helper for positional tables.
    if value == nil or value.x == nil or value.y == nil then
        error("Position with x/y is required.")
    end
end

local function list_players()
    -- Enumerate current players with minimal state so the MCP server can decide targets.
    local players = {}
    for _, player in pairs(game.players) do
        table.insert(players, {
            name = player.name,
            index = player.index,
            connected = player.connected,
            afk_time = player.afk_time,
            online_time = player.online_time,
            surface = player.surface and player.surface.name or nil,
            position = player.position,
        })
    end

    return { ok = true, players = players }
end

local function player_state(payload)
    -- Return surface/position/health and inventory counts for a specific player.
    if payload.player == nil then
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
end

local function find_resources(payload)
    -- Locate resource entities near a coordinate (used for planning placements).
    if payload.resource == nil then
        return { ok = false, error = "resource is required" }
    end

    if payload.position == nil then
        return { ok = false, error = "position is required" }
    end

    ensure_position(payload.position)

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
        table.insert(results, {
            name = resource.name,
            position = resource.position,
            amount = resource.amount,
            surface = resource.surface.name,
        })
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
end

local function build_entities(payload)
    -- Place entities on behalf of a player. Items are only consumed after successful placement.
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
            table.insert(errors, "Entity definition missing name or position.")
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
                -- Only consume items after a successful placement; if removal fails, undo the build.
                local removed = player.remove_item { name = name, count = 1 }
                if removed == 0 then
                    created.destroy { raise_destroy = true }
                    built = false
                    table.insert(errors, "Missing item: " .. name)
                end
            end

            table.insert(results, {
                name = name,
                position = position,
                direction = direction,
                built = built,
            })

            if not built then
                table.insert(errors, "Failed to create entity: " .. name)
            end
        end
    end

    return {
        ok = #errors == 0,
        built = results,
        errors = errors,
    }
end

local function teleport_player(payload)
    -- Teleport a player to coordinates (handy for setup and testing).
    if payload.player == nil then
        return { ok = false, error = "player is required" }
    end

    if payload.position == nil then
        return { ok = false, error = "position is required" }
    end

    ensure_position(payload.position)

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
end

local function handle_query(payload)
    -- Read-only commands.
    if payload.type == "ping" then
        return { ok = true, type = "ping", tick = game.tick }
    elseif payload.type == "players" then
        return list_players()
    elseif payload.type == "player_state" then
        return player_state(payload)
    elseif payload.type == "find_resources" then
        return find_resources(payload)
    else
        return { ok = false, error = "Unknown query type: " .. tostring(payload.type) }
    end
end

local function handle_action(payload)
    -- Commands that change the world or player state.
    if payload.type == "build_entities" then
        return build_entities(payload)
    elseif payload.type == "teleport_player" then
        return teleport_player(payload)
    else
        return { ok = false, error = "Unknown action type: " .. tostring(payload.type) }
    end
end

local function register_command(name, handler)
    -- Wire RCON-facing commands to handlers with robust error responses.
    commands.add_command(name, "MCP bridge command", function(command)
        mod_log(name .. " invoked by " .. (command.player_index and ("player " .. command.player_index) or "server"))
        if command.parameter and command.parameter ~= "" then
            mod_log(name .. " payload: " .. command.parameter)
        end

        local payload, err = parse_parameter(command.parameter)
        if payload == nil then
            mod_log(name .. " parse error: " .. err)
            respond({ ok = false, error = err })
            return
        end

        local ok, result = pcall(handler, payload)
        if not ok then
            mod_log(name .. " handler error: " .. tostring(result))
            respond({ ok = false, error = result })
        else
            mod_log(name .. " result: " .. game.table_to_json(result))
            respond(result)
        end
    end)
end

register_command("mcp-query", handle_query)
register_command("mcp-action", handle_action)
