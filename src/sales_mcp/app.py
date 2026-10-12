"""The HTTP application: FastAPI in front, the MCP server mounted at /mcp.

Two doors lead to the same server:

  /mcp            `Authorization: Bearer <MCP_TOKEN>`. For Claude Code, Claude
                  Desktop config files, scripts and the MCP Inspector.
  /mcp/t/<token>  The token travels in the path. It exists because Claude.ai
                  custom connectors cannot send custom headers. A credential in
                  a URL is a known risk, so this door has its own token, its own
                  off switch, and the token is masked in the access log.

Each door has its own rate-limit bucket: a runaway connector cannot lock out
the header door, and the other way round.

Before either door, the gate checks Host and Origin (DNS-rebinding protection).
It does this itself rather than through the SDK because the SDK's allow-list has
no subdomain wildcard, and a Cloudflare quick tunnel gets a new random
`*.trycloudflare.com` name on every start: with `*.trycloudflare.com` allowed,
a reviewer can open a tunnel without editing any configuration. That pattern
is safe to allow: those names resolve to Cloudflare, never to 127.0.0.1, so they
cannot be used to rebind a browser onto this machine.
"""

import hmac
import json
import logging
import re
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS
from starlette.types import ASGIApp, Receive, Scope, Send

from sales_mcp import __version__, db
from sales_mcp.config import Settings, get_settings
from sales_mcp.server import TOOLS, build_server

log = logging.getLogger("sales_mcp.http")

URL_DOOR = re.compile(r"^/mcp/t/([^/]+)/?$")
WINDOW_S = 60


def _split_port(value: str) -> tuple[str, str | None]:
    """'example.com:8000' -> ('example.com', '8000'); IPv6 literals keep their brackets."""
    if value.startswith("["):
        name, _, rest = value.partition("]")
        return name + "]", rest[1:] if rest.startswith(":") else None
    name, sep, port = value.rpartition(":")
    return (name, port) if sep and port.isdigit() else (value, None)


def host_allowed(host: str, patterns: list[str]) -> bool:
    """Match a Host header against an allow-list.

    `name` exact · `name:*` that name on any port · `*.domain` any subdomain of
    domain, on any port.
    """
    host = host.strip().lower()
    if not host:
        return False
    name, _ = _split_port(host)
    for pattern in (p.strip().lower() for p in patterns):
        if pattern.startswith("*."):
            if name.endswith(pattern[1:]) and len(name) > len(pattern) - 1:
                return True
        elif pattern.endswith(":*"):
            if name == pattern[:-2]:
                return True
        elif host == pattern:
            return True
    return False


def origin_allowed(origin: str | None, patterns: list[str]) -> bool:
    """An absent Origin is fine (not a browser); a present one must be listed.

    `scheme://name` exact · `scheme://name:*` that origin on any port.
    """
    if not origin:
        return True
    origin = origin.strip().lower().rstrip("/")
    for pattern in (p.strip().lower().rstrip("/") for p in patterns):
        if pattern.endswith(":*"):
            base = pattern[:-2]
            if origin == base or (
                origin.startswith(base + ":") and origin[len(base) + 1 :].isdigit()
            ):
                return True
        elif origin == pattern:
            return True
    return False


def tokens_match(given: str, expected: str) -> bool:
    """Constant-time comparison. An empty expected token authorizes NOBODY,
    and neither does a placeholder left over from `.env.example`.

    That matters more than it looks: without it, deploying before the secret is
    loaded would open the door to anyone sending an empty token, and the
    placeholders are published in this repository.
    """
    expected = (expected or "").strip()
    if not expected or expected.startswith("replace-me"):
        return False
    return hmac.compare_digest(given.strip().encode(), expected.encode())


class SlidingWindow:
    """At most `per_minute` calls per key in any 60-second window, in memory.

    One process, one counter. Running several replicas would need a shared
    store (Redis); a single small server does not.
    """

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        hits = self.hits.setdefault(key, deque())
        while hits and now - hits[0] >= WINDOW_S:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return False
        hits.append(now)
        return True


async def rpc_error(
    send: Send,
    status: int,
    code: int,
    message: str,
    hint: str,
    headers: dict[str, str] | None = None,
) -> None:
    """A JSON-RPC error. `hint` says what to DO, not only what failed."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": code, "message": message, "data": {"hint": hint}},
        }
    ).encode()
    raw_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    raw_headers += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw_headers})
    await send({"type": "http.response.body", "body": body})


class McpGate:
    """Authentication and rate limiting in front of the MCP endpoint."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.limiter = SlidingWindow(settings.mcp_rate_per_min)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/mcp" or path.startswith("/mcp/")):
            await self.app(scope, receive, send)
            return

        if not self.settings.mcp_enabled:
            await rpc_error(
                send, 404, -32001, "The MCP server is turned off.", "Set MCP_ENABLED=true."
            )
            return

        headers = dict(scope["headers"])
        host = headers.get(b"host", b"").decode(errors="ignore")
        if not host_allowed(host, self.settings.mcp_allowed_hosts):
            log.warning("Refused Host header: %s", host)
            await rpc_error(
                send, 421, -32004, "Host not allowed.", "Add this host to MCP_ALLOWED_HOSTS."
            )
            return
        origin = headers.get(b"origin", b"").decode(errors="ignore")
        if not origin_allowed(origin, self.settings.mcp_allowed_origins):
            log.warning("Refused Origin header: %s", origin)
            await rpc_error(
                send, 403, -32004, "Origin not allowed.", "Add it to MCP_ALLOWED_ORIGINS."
            )
            return

        match = URL_DOOR.match(path)
        if path in ("/mcp", "/mcp/"):
            door = "header"
            header = headers.get(b"authorization", b"").decode(errors="ignore")
            scheme, _, token = header.partition(" ")
            if scheme.lower() != "bearer" or not tokens_match(token, self.settings.mcp_token):
                await rpc_error(
                    send,
                    401,
                    -32002,
                    "Missing or invalid token.",
                    "Send `Authorization: Bearer <token>`.",
                    {"WWW-Authenticate": 'Bearer realm="sales-mcp"'},
                )
                return
        elif match:
            door = "url"
            if not self.settings.mcp_url_token_enabled:
                await rpc_error(
                    send,
                    404,
                    -32001,
                    "The URL-token door is turned off.",
                    "Set MCP_URL_TOKEN_ENABLED=true, or use the header door.",
                )
                return
            if not tokens_match(match.group(1), self.settings.mcp_url_token):
                # The hint never repeats the token: errors end up in screenshots.
                await rpc_error(
                    send, 401, -32002, "Missing or invalid token.", "Check the connector URL."
                )
                return
            scope = {**scope, "path": "/mcp", "raw_path": b"/mcp"}
        else:
            await rpc_error(
                send,
                404,
                -32601,
                "Unknown MCP path.",
                "Use /mcp (bearer header) or /mcp/t/<token>.",
            )
            return

        if not self.limiter.allow(door):
            await rpc_error(
                send,
                429,
                -32003,
                "Too many calls.",
                f"At most {self.settings.mcp_rate_per_min} per minute.",
                {"Retry-After": str(WINDOW_S)},
            )
            return
        await self.app(scope, receive, send)


class MaskUrlToken(logging.Filter):
    """Keep the URL door's token out of the access log."""

    PATTERN = re.compile(r"(/mcp/t/)[^/\s?\"]+")

    def mask(self, value: object) -> object:
        return self.PATTERN.sub(r"\1***", value) if isinstance(value, str) else value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.mask(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self.mask(a) for a in record.args)
        return True


class QuietHealthChecks(logging.Filter):
    """Drop successful `/health` lines: Docker's healthcheck calls it every 10 s,
    and the console should show the requests a person made."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn.access: (client, method, path, http_version, status)
        return not (
            isinstance(args, tuple) and len(args) >= 5 and args[2] == "/health" and args[4] == 200
        )


def install_log_masking() -> None:
    access = logging.getLogger("uvicorn.access")
    for kind in (MaskUrlToken, QuietHealthChecks):
        if not any(isinstance(f, kind) for f in access.filters):
            access.addFilter(kind())


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    server = build_server()
    mcp_http = server.streamable_http_app(
        streamable_http_path="/mcp",
        # Stateless + JSON: every request stands alone, so the server can be
        # restarted or replicated without breaking a client mid-conversation.
        stateless_http=True,
        json_response=True,
        # Host and Origin are checked by McpGate (see the module docstring);
        # the SDK still rejects a POST that is not application/json.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        install_log_masking()
        db.configure(settings.database_url)
        try:
            if settings.mcp_enabled:
                await db.warn_if_writable()
            async with server.session_manager.run():
                yield
        finally:
            await db.dispose()

    app = FastAPI(
        title="Sales MCP server",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    async def health() -> dict[str, object]:
        """No auth on purpose: the first thing to check is whether it is on."""
        return {
            "status": "ok",
            "mcp_enabled": settings.mcp_enabled,
            "tools": len(TOOLS),
            "protocol_versions": [*HANDSHAKE_PROTOCOL_VERSIONS, *MODERN_PROTOCOL_VERSIONS],
            "version": __version__,
        }

    app.add_middleware(McpGate, settings=settings)
    app.mount("/", mcp_http)
    return app
