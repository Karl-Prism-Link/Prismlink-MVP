#!/usr/bin/env python3
"""Minimal stdio MCP bridge for the Prism Link voice QA systemd unit.

This intentionally exposes no shell, target, environment, or filesystem inputs.
Only the fixed T001 unit and its result directory can be inspected or started.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

UNIT = "prism-link-voice-qa.service"
RESULT_DIR = Path("/var/lib/prism-link-voice-qa/results")
PROTOCOL_VERSION = "2024-11-05"


def systemctl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def latest_result() -> dict[str, Any] | None:
    files = sorted(RESULT_DIR.glob("T001-*-result.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def tool_result(value: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
        "isError": is_error,
    }


def call_tool(name: str, arguments: Any) -> dict[str, Any]:
    if arguments not in ({}, None):
        return tool_result({"error": "This tool accepts no arguments."}, is_error=True)

    if name == "qa_status":
        proc = systemctl("show", UNIT, "--property=ActiveState")
        if proc.returncode != 0:
            return tool_result({"error": proc.stderr.strip() or "Could not read QA unit state."}, is_error=True)
        values = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
        return tool_result({"unit": UNIT, "state": values, "latest_result": latest_result()})

    if name == "qa_run_t001":
        proc = systemctl("start", UNIT, timeout=180)
        if proc.returncode != 0:
            return tool_result(
                {"error": "The fixed T001 unit failed to start.", "detail": proc.stderr.strip()},
                is_error=True,
            )
        result = latest_result()
        if result is None:
            return tool_result({"error": "The unit finished but did not write a T001 result."}, is_error=True)
        return tool_result(result, is_error=result.get("status") == "FAIL")

    return tool_result({"error": f"Unknown tool: {name}"}, is_error=True)


TOOLS = [
    {
        "name": "qa_status",
        "description": "Read the fixed Prism Link T001 QA unit state and its latest result. Does not start calls.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "qa_run_t001",
        "description": "Start the fixed T001 Linphone call to Luxe Hair & Beauty via Aurora's direct SIP route, then return its result. No target or shell arguments are accepted.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "prism-link-voice-qa", "version": "1.0.0"},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        result = call_tool(str(params.get("name", "")), params.get("arguments", {}))
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    if request_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"},
        }
    return None


def main() -> None:
    for line in sys.stdin:
        request: dict[str, Any] = {}
        try:
            request = json.loads(line)
            response = handle(request)
        except Exception as exc:  # Keep stdout protocol-only; details go to stderr.
            print(f"MCP request failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            request_id = locals().get("request", {}).get("id")
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": "Internal error"},
            }
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
