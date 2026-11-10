#!/usr/bin/env python3
"""Stateless Streamable HTTP MCP endpoint for the fixed T001 QA controls."""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from qa_mcp_server import handle as handle_stdio_message


SUPPORTED_PROTOCOLS = {
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
    "2026-07-28",
}
MAX_BODY_BYTES = 16 * 1024


class AuthenticationError(Exception):
    pass


def verify_access_token(token: str) -> dict[str, Any]:
    """Validate the Cloudflare Access JWT and restrict it to the configured user/app."""
    team_domain = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip()
    audience = os.environ.get("CF_ACCESS_AUD", "").strip()
    allowed_email = os.environ.get("CF_ACCESS_ALLOWED_EMAIL", "karl@prismlink.tech").strip().lower()
    if not team_domain or not audience:
        raise AuthenticationError("Cloudflare Access verification is not configured.")

    try:
        import jwt
        from jwt import PyJWKClient

        jwks_url = f"https://{team_domain}/cdn-cgi/access/certs"
        key = PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=f"https://{team_domain}",
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except Exception as exc:
        raise AuthenticationError("Cloudflare Access token validation failed.") from exc

    if str(claims.get("email", "")).strip().lower() != allowed_email:
        raise AuthenticationError("This Cloudflare identity is not allowed to use voice QA.")
    return claims


def dispatch(message: Any, protocol_version: str | None = None) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": -32600, "message": "Invalid JSON-RPC request"}}
    response = handle_stdio_message(message)
    if response and message.get("method") == "initialize":
        params = message.get("params") or {}
        requested = params.get("protocolVersion")
        response["result"]["protocolVersion"] = requested if requested in SUPPORTED_PROTOCOLS else "2025-11-25"
    return response


class McpHandler(BaseHTTPRequestHandler):
    server_version = "PrismLinkQAMCP/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Avoid request headers, tokens, and tool payloads in service logs.
        sys.stderr.write(f"qa-mcp {self.command} {self.path.split('?')[0]}\n")

    def do_GET(self) -> None:
        if urlsplit(self.path).path != "/mcp":
            self.send_error(404)
            return
        # This endpoint is stateless and does not offer server-initiated SSE.
        self.send_error(405, "GET streaming is not supported")

    def do_DELETE(self) -> None:
        self.send_error(405, "Stateless MCP sessions are not supported")

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/mcp":
            self.send_error(404)
            return

        token = self.headers.get("Cf-Access-Jwt-Assertion", "")
        try:
            verify_access_token(token)
        except AuthenticationError as exc:
            self.send_error(401, str(exc))
            return

        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            self.send_error(415, "Expected application/json")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_error(413, "Request body size is invalid")
            return

        try:
            message = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_error(400, "Invalid JSON")
            return

        method = message.get("method") if isinstance(message, dict) else None
        if method not in {"initialize", "notifications/initialized", "ping", "tools/list", "tools/call"}:
            response = dispatch(message)
            if response is None:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send_json(response)
            return

        if method != "initialize":
            version = self.headers.get("MCP-Protocol-Version")
            if version and version not in SUPPORTED_PROTOCOLS:
                self.send_error(400, "Unsupported MCP-Protocol-Version")
                return

        response = dispatch(message, self.headers.get("MCP-Protocol-Version"))
        if response is None:  # JSON-RPC notification
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_json(response)

    def _send_json(self, response: dict[str, Any]) -> None:
        body = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        result = response.get("result", {})
        protocol_version = result.get("protocolVersion") if isinstance(result, dict) else None
        protocol_version = protocol_version or self.headers.get("MCP-Protocol-Version")
        if protocol_version in SUPPORTED_PROTOCOLS:
            self.send_header("MCP-Protocol-Version", protocol_version)
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    bind = os.environ.get("QA_MCP_BIND", "127.0.0.1")
    port = int(os.environ.get("QA_MCP_PORT", "8765"))
    if bind not in {"127.0.0.1", "::1"}:
        raise SystemExit("QA_MCP_BIND must remain loopback-only behind Cloudflare Tunnel.")
    server = ThreadingHTTPServer((bind, port), McpHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
