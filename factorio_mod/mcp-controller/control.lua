-- Factorio MCP Controller mod
-- Exposes a small command surface for orchestration via RCON.

local function respond(payload)
    rcon.print(game.table_to_json(payload))
end

local function parse_parameter(parameter)
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
    if value == nil or value.x == nil or value.y == nil then
        error("Position with x/y is required.")
    end
end

local function list_players()
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
            local allow_build = true

            if consume_items then
                local removed = player.remove_item { name = name, count = 1 }
                if removed == 0 then
                    allow_build = false
                    table.insert(errors, "Missing item: " .. name)
                end
            end

            if allow_build then
                local created = surface.create_entity {
                    name = name,
                    position = position,
                    direction = direction,
                    force = player.force,
                    player = player,
                    raise_built = true,
                    fast_replace = true,
                }

                table.insert(results, {
                    name = name,
                    position = position,
                    direction = direction,
                    built = created ~= nil and created.valid,
                })

                if created == nil then
                    table.insert(errors, "Failed to create entity: " .. name)
                end
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
    if payload.type == "build_entities" then
        return build_entities(payload)
    elseif payload.type == "teleport_player" then
        return teleport_player(payload)
    else
        return { ok = false, error = "Unknown action type: " .. tostring(payload.type) }
    end
end

local function register_command(name, handler)
    commands.add_command(name, "MCP bridge command", function(command)
        local payload, err = parse_parameter(command.parameter)
        if payload == nil then
            respond({ ok = false, error = err })
            return
        end

        local ok, result = pcall(handler, payload)
        if not ok then
            respond({ ok = false, error = result })
        else
            respond(result)
        end
    end)
end

register_command("mcp-query", handle_query)
register_command("mcp-action", handle_action)
