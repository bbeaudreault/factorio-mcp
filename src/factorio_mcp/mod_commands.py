"""Helpers for building commands understood by the Factorio MCP mod."""

from __future__ import annotations

import json
from typing import Any, Dict


QUERY_COMMAND = "mcp-query"
ACTION_COMMAND = "mcp-action"


def build_query(payload: Dict[str, Any]) -> str:
    """Return an RCON command string that invokes the MCP query command."""

    return f"{QUERY_COMMAND} {json.dumps(payload, separators=(',', ':'))}"


def build_action(payload: Dict[str, Any]) -> str:
    """Return an RCON command string that invokes the MCP action command."""

    return f"{ACTION_COMMAND} {json.dumps(payload, separators=(',', ':'))}"
