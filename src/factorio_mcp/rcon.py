"""Lightweight RCON client tailored for Factorio."""

from __future__ import annotations

import json
import random
import socket
import struct
from dataclasses import dataclass
from typing import Optional

from factorio_mcp.config import FactorioConfig


class RconProtocolError(RuntimeError):
    """Raised when an unexpected response is encountered."""


class RconAuthError(RuntimeError):
    """Raised when authentication against the RCON server fails."""


@dataclass
class RconPacket:
    request_id: int
    packet_type: int
    body: str


class RconClient:
    """Minimal RCON client suitable for communicating with Factorio."""

    SERVERDATA_AUTH = 3
    SERVERDATA_AUTH_RESPONSE = 2
    SERVERDATA_EXECCOMMAND = 2
    SERVERDATA_RESPONSE_VALUE = 0

    def __init__(self, config: FactorioConfig):
        self.config = config
        self._sock: Optional[socket.socket] = None
        self._authed = False

    def __enter__(self) -> "RconClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        if self._sock:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.config.timeout)
        sock.connect((self.config.host, self.config.port))
        self._sock = sock

        self._authenticate()

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
        self._sock = None
        self._authed = False

    def execute(self, command: str) -> str:
        """Run a command and return the raw response body."""

        if not self._authed:
            self.connect()

        packet = self._send_packet(self.SERVERDATA_EXECCOMMAND, command)
        if packet.request_id == -1:
            raise RconProtocolError("RCON command rejected by server.")
        return packet.body

    def execute_json(self, command: str) -> dict:
        """Run a command and parse a JSON response."""

        body = self.execute(command)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RconProtocolError(f"Expected JSON response from RCON, got: {body}") from exc

    def _authenticate(self) -> None:
        packet = self._send_packet(self.SERVERDATA_AUTH, self.config.password)
        if packet.request_id == -1:
            raise RconAuthError("Authentication failed; check the RCON password.")
        self._authed = True

    def _send_packet(self, packet_type: int, body: str) -> RconPacket:
        if not self._sock:
            raise RconProtocolError("Socket not connected.")

        request_id = random.randint(1, 2_147_483_647)
        payload = struct.pack("<ii", request_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
        length = struct.pack("<i", len(payload))

        self._sock.sendall(length + payload)
        return self._recv_response(request_id)

    def _recv_response(self, request_id: int) -> RconPacket:
        """
        Read one or more packets for a response.

        Factorio/Source RCON can split large responses into multiple packets; we collect
        until we encounter an empty body (terminator) or run out of packets.
        """

        packets: list[RconPacket] = []
        first = self._recv_packet(expected_request_id=request_id)
        packets.append(first)

        while True:
            try:
                packet = self._recv_packet(expected_request_id=request_id)
            except (socket.timeout, RconProtocolError):
                break

            packets.append(packet)
            if packet.body == "":
                break

        combined_body = "".join(packet.body for packet in packets)
        return RconPacket(
            request_id=first.request_id,
            packet_type=first.packet_type,
            body=combined_body,
        )

    def _recv_packet(self, expected_request_id: Optional[int] = None) -> RconPacket:
        assert self._sock is not None

        length_bytes = self._read_exact(4)
        (length,) = struct.unpack("<i", length_bytes)
        payload = self._read_exact(length)

        request_id, packet_type = struct.unpack("<ii", payload[:8])
        body = payload[8:-2].decode("utf-8", errors="replace")

        if expected_request_id is not None and request_id not in (expected_request_id, -1):
            raise RconProtocolError(
                f"Unexpected request id {request_id}, expected {expected_request_id}."
            )

        return RconPacket(request_id=request_id, packet_type=packet_type, body=body)

    def _read_exact(self, length: int) -> bytes:
        assert self._sock is not None

        chunks: list[bytes] = []
        remaining = length
        while remaining > 0:
            chunk = self._sock.recv(remaining)
            if not chunk:
                raise RconProtocolError("Socket connection closed while reading.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
