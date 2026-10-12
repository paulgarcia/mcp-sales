"""`sales-mcp connect-info`: the box `docker compose up` ends with."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from sales_mcp.config import Settings
from sales_mcp.connect import render, tunnel_host


def settings(**overrides) -> Settings:
    values = {"mcp_token": "tok-1", "mcp_url_token": "url-1", "mcp_url_token_enabled": True}
    return Settings(_env_file=None, **{**values, **overrides})


def test_without_a_tunnel_it_prints_the_local_lines_and_how_to_start_one():
    text = render(settings(), None)
    assert "claude mcp add --transport http sales http://127.0.0.1:8000/mcp" in text
    assert '--header "Authorization: Bearer tok-1"' in text
    assert "docker compose --profile tunnel up" in text
    assert "url-1" not in text


def test_with_a_tunnel_it_prints_the_connector_url():
    text = render(settings(), "some-random-words.trycloudflare.com")
    assert "https://some-random-words.trycloudflare.com/mcp/t/url-1" in text


def test_with_the_url_door_off_it_says_how_to_turn_it_on():
    text = render(settings(mcp_url_token_enabled=False), "abc.trycloudflare.com")
    assert "MCP_URL_TOKEN_ENABLED=true" in text
    assert "url-1" not in text


def test_a_tunnel_without_an_address_says_where_to_look():
    text = render(settings(), "")
    assert "docker compose logs tunnel" in text
    assert "url-1" not in text


def test_a_tunnel_that_does_not_resolve_is_not_waited_for():
    assert tunnel_host("http://no-such-tunnel.invalid:2000", wait_s=30) is None


def test_the_hostname_comes_from_cloudflared_metrics():
    # cloudflared answers {"hostname": ""} until the quick tunnel is registered.
    answers = iter(["", "", "abc.trycloudflare.com"])

    class Metrics(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"hostname": next(answers)}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Metrics)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        assert tunnel_host(url, wait_s=5, every_s=0.05) == "abc.trycloudflare.com"
        # Reachable but still nameless when time runs out: "", not None.
        answers = iter([""] * 1000)
        assert tunnel_host(url, wait_s=0.2, every_s=0.05) == ""
    finally:
        server.shutdown()
