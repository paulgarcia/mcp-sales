"""`sales-mcp connect-info`: print exactly what to paste to connect each client.

Docker Compose runs it once the server is healthy, so `docker compose up` ends
with a box of ready-to-copy lines instead of a list of steps. With the `tunnel`
profile, cloudflared's metrics endpoint (`/quicktunnel`) tells us the random
`*.trycloudflare.com` name, and the Claude.ai connector URL is printed too.
"""

import json
import socket
import time
import urllib.error
import urllib.request

from sales_mcp.config import Settings

LOCAL_URL = "http://127.0.0.1:8000/mcp"


def tunnel_host(metrics_url: str, wait_s: float = 60.0, every_s: float = 2.0) -> str | None:
    """The quick tunnel's hostname; None when no tunnel runs; "" when one runs
    but has no public name after `wait_s`.

    A tunnel that does not resolve is not running (Compose was started without
    the profile). One that resolves gets `wait_s` to register its name.
    """
    reachable = False
    deadline = time.monotonic() + wait_s
    while True:
        try:
            with urllib.request.urlopen(f"{metrics_url}/quicktunnel", timeout=3) as r:  # noqa: S310
                host = json.load(r).get("hostname") or ""
            reachable = True
            if host:
                return host
        except urllib.error.URLError as e:
            if isinstance(e.reason, socket.gaierror):
                return None
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            return "" if reachable else None
        time.sleep(every_s)


def render(settings: Settings, host: str | None) -> str:
    token = settings.mcp_token
    lines = [
        "",
        "=" * 78,
        " The MCP server is ready. Copy what you need below.",
        " These lines contain your secrets: do not show them in a screenshot or video.",
        "=" * 78,
        "",
        " Claude Code: run this inside the project folder (PowerShell, bash or zsh):",
        "",
        f"   claude mcp add --transport http sales {LOCAL_URL}"
        f' --header "Authorization: Bearer {token}"',
        "",
        " MCP Inspector: Add Servers -> streamable-http -> URL, then Settings -> Custom Headers:",
        "",
        f"   URL:     {LOCAL_URL}",
        f"   Header:  Authorization = Bearer {token}",
        "",
        " Claude.ai / Claude Desktop: Settings -> Connectors -> Add custom connector:",
        "",
    ]
    if host is None:
        lines += [
            "   No tunnel is running. For a public URL, start with:",
            "     docker compose --profile tunnel up --build",
            "   (If you did, the tunnel stopped: docker compose logs tunnel)",
        ]
    elif not host:
        lines += [
            "   The tunnel is running but has no public address yet. Check its log:",
            "     docker compose logs tunnel",
        ]
    elif not settings.mcp_url_token_enabled:
        lines.append("   Set MCP_URL_TOKEN_ENABLED=true in .env, then: docker compose up")
    else:
        lines += [
            f"   URL:  https://{host}/mcp/t/{settings.mcp_url_token}",
            "",
            "   The tunnel's name changes every time it restarts: copy the URL again then.",
        ]
    lines += ["", "=" * 78, ""]
    return "\n".join(lines)
