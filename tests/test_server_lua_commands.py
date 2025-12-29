from factorio_mcp.config import FactorioConfig
from factorio_mcp.server import FactorioBridge, Position


class RecordingClient:
    def __init__(self, response):
        self.commands: list[str] = []
        self.response = response

    def execute_json(self, command: str):
        self.commands.append(command)
        return self.response


def _bridge_with_response(response):
    bridge = FactorioBridge(FactorioConfig())
    bridge._client = RecordingClient(response)
    return bridge


def test_ping_sends_lua_command():
    bridge = _bridge_with_response({"ok": True})

    result = bridge.ping()

    assert result == {"ok": True}
    assert bridge._client.commands[0].startswith("/c ")  # type: ignore[attr-defined]
    assert "type='ping'" in bridge._client.commands[0]  # type: ignore[attr-defined]


def test_find_resources_embeds_payload_json():
    bridge = _bridge_with_response({"ok": True})

    bridge.find_resources("iron-ore", Position(x=1.5, y=-2.5), 10, surface="nauvis")

    command = bridge._client.commands[0]  # type: ignore[attr-defined]
    assert '"resource":"iron-ore"' in command
    assert '"position":{"x":1.5,"y":-2.5}' in command
    assert '"radius":10' in command
    assert '"surface":"nauvis"' in command
