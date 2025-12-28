import struct

import pytest

from factorio_mcp.config import FactorioConfig
from factorio_mcp.rcon import RconAuthError, RconClient, RconProtocolError


class FakeSocket:
    """A minimal socket-like object that replays predefined bytes."""

    def __init__(self, response_bytes: bytes):
        self._response = response_bytes
        self._cursor = 0
        self.sent = b""

    def settimeout(self, _timeout):
        return None

    def connect(self, _addr):
        return None

    def shutdown(self, _how):
        return None

    def close(self):
        return None

    def sendall(self, data: bytes):
        self.sent += data

    def recv(self, n: int) -> bytes:
        if self._cursor >= len(self._response):
            return b""
        chunk = self._response[self._cursor : self._cursor + n]
        self._cursor += len(chunk)
        return chunk


def make_response(request_id: int, packet_type: int, body: str) -> bytes:
    payload = struct.pack("<ii", request_id, packet_type) + body.encode() + b"\x00\x00"
    length = struct.pack("<i", len(payload))
    return length + payload


def test_execute_json_success(monkeypatch):
    cfg = FactorioConfig()
    client = RconClient(cfg)

    # Deterministic request id
    monkeypatch.setattr("factorio_mcp.rcon.random.randint", lambda *_args, **_kwargs: 123)
    body = '{"ok":true}'
    client._sock = FakeSocket(make_response(123, RconClient.SERVERDATA_RESPONSE_VALUE, body))
    client._authed = True

    result = client.execute_json("mcp-query {}")
    assert result == {"ok": True}

    # Verify the packet header was sent with matching request id and type
    sent = client._sock.sent
    length, req_id, packet_type = struct.unpack("<iii", sent[:12])
    assert length == len(sent) - 4
    assert req_id == 123
    assert packet_type == RconClient.SERVERDATA_EXECCOMMAND


def test_execute_json_protocol_error(monkeypatch):
    cfg = FactorioConfig()
    client = RconClient(cfg)
    monkeypatch.setattr("factorio_mcp.rcon.random.randint", lambda *_args, **_kwargs: 10)
    client._sock = FakeSocket(make_response(10, RconClient.SERVERDATA_RESPONSE_VALUE, "not-json"))
    client._authed = True

    with pytest.raises(RconProtocolError):
        client.execute_json("mcp-query {}")


def test_auth_failure(monkeypatch):
    cfg = FactorioConfig()
    client = RconClient(cfg)

    monkeypatch.setattr("factorio_mcp.rcon.random.randint", lambda *_args, **_kwargs: 5)
    client._sock = FakeSocket(make_response(-1, RconClient.SERVERDATA_AUTH_RESPONSE, ""))
    # Call _authenticate directly to isolate auth path
    with pytest.raises(RconAuthError):
        client._authenticate()
