from factorio_mcp import mod_commands


def test_build_query_compact_json():
    payload = {"type": "ping", "nested": {"a": 1}}
    command = mod_commands.build_query(payload)
    assert command.startswith(mod_commands.QUERY_COMMAND + " ")
    # Compact JSON: no spaces
    assert " " not in command[len(mod_commands.QUERY_COMMAND) + 1 :]
    assert command.endswith('{"type":"ping","nested":{"a":1}}')


def test_build_action_compact_json():
    payload = {"type": "build_entities", "player": "alice"}
    command = mod_commands.build_action(payload)
    assert command == 'mcp-action {"type":"build_entities","player":"alice"}'
