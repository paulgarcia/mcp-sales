"""The doors in front of the MCP endpoint, tested over real HTTP."""

import logging

import httpx2
import pytest
from conftest import TOKEN, URL_TOKEN, make_settings, serve

from sales_mcp.app import (
    MaskUrlToken,
    QuietHealthChecks,
    SlidingWindow,
    host_allowed,
    origin_allowed,
    tokens_match,
)

INIT = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
HEADERS = {"Accept": "application/json, text/event-stream"}


def post(base: str, path: str, token: str | None = None) -> httpx2.Response:
    headers = dict(HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}" if token else "Bearer"
    return httpx2.post(base + path, json=INIT, headers=headers, timeout=10)


@pytest.fixture(scope="module")
def base(urls):
    with serve(make_settings(urls)) as url:
        yield url


def test_health_answers_without_a_token(base):
    body = httpx2.get(base + "/health", timeout=10).json()
    assert body["mcp_enabled"] is True
    assert body["tools"] == 9
    assert "2026-07-28" in body["protocol_versions"]


def test_a_server_that_is_off_says_how_to_turn_it_on(urls):
    with serve(make_settings(urls, mcp_enabled=False)) as url:
        r = post(url, "/mcp", TOKEN)
    assert r.status_code == 404
    assert "MCP_ENABLED" in r.json()["error"]["data"]["hint"]


def test_no_token_no_entry(base):
    r = post(base, "/mcp")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")


def test_a_wrong_token_does_not_get_in_either(base):
    assert post(base, "/mcp", "nope").status_code == 401


def test_the_right_token_reaches_the_mcp_server(base):
    r = post(base, "/mcp", TOKEN)
    assert r.status_code == 200
    assert r.json()["id"] == 1


def test_an_empty_configured_token_lets_nobody_in(urls):
    with serve(make_settings(urls, mcp_token="")) as url:
        assert post(url, "/mcp", "").status_code == 401
        assert post(url, "/mcp", "anything").status_code == 401


def test_the_url_door_speaks_the_same_protocol(base):
    r = post(base, f"/mcp/t/{URL_TOKEN}")
    assert r.status_code == 200
    assert r.json()["id"] == 1


def test_the_url_door_rejects_a_wrong_token_without_echoing_it(base):
    r = post(base, "/mcp/t/guess-123")
    assert r.status_code == 401
    assert "guess-123" not in r.text


def test_the_header_token_does_not_open_the_url_door(base):
    assert post(base, f"/mcp/t/{TOKEN}").status_code == 401


def test_turning_the_url_door_off_leaves_the_header_door_working(urls):
    with serve(make_settings(urls, mcp_url_token_enabled=False)) as url:
        assert post(url, f"/mcp/t/{URL_TOKEN}").status_code == 404
        assert post(url, "/mcp", TOKEN).status_code == 200


def test_unknown_mcp_paths_are_refused(base):
    assert post(base, "/mcp/admin", TOKEN).status_code == 404


def test_rate_limit_says_when_to_retry(urls):
    with serve(make_settings(urls, mcp_rate_per_min=2)) as url:
        codes = [post(url, "/mcp", TOKEN).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        r = post(url, "/mcp", TOKEN)
        assert r.headers["retry-after"] == "60"
        # Each door has its own bucket: the URL door still works.
        assert post(url, f"/mcp/t/{URL_TOKEN}").status_code == 200


def test_a_foreign_host_header_is_refused(base):
    # DNS-rebinding protection: only configured Host headers reach the server.
    r = httpx2.post(
        base + "/mcp",
        json=INIT,
        timeout=10,
        headers={**HEADERS, "Authorization": f"Bearer {TOKEN}", "Host": "evil.example.com"},
    )
    assert r.status_code == 421
    assert "MCP_ALLOWED_HOSTS" in r.json()["error"]["data"]["hint"]


def test_a_quick_tunnel_host_and_claude_origin_get_through(base):
    # Behind `cloudflared tunnel --url`, the Host is the tunnel's random name.
    headers = {**HEADERS, "Host": "some-random-words.trycloudflare.com"}
    r = httpx2.post(f"{base}/mcp/t/{URL_TOKEN}", json=INIT, timeout=10, headers=headers)
    assert r.status_code == 200
    headers["Origin"] = "https://claude.ai"
    r = httpx2.post(f"{base}/mcp/t/{URL_TOKEN}", json=INIT, timeout=10, headers=headers)
    assert r.status_code == 200


def test_a_foreign_browser_origin_is_refused(base):
    headers = {**HEADERS, "Authorization": f"Bearer {TOKEN}", "Origin": "https://evil.example"}
    r = httpx2.post(base + "/mcp", json=INIT, timeout=10, headers=headers)
    assert r.status_code == 403


DEFAULT_HOSTS = ["localhost:*", "127.0.0.1:*", "*.trycloudflare.com"]


def test_host_allow_list_with_wildcards():
    assert host_allowed("127.0.0.1:8000", DEFAULT_HOSTS)
    assert host_allowed("localhost", DEFAULT_HOSTS)
    # A quick tunnel gets a new random name on every start.
    assert host_allowed("some-random-words.trycloudflare.com", DEFAULT_HOSTS)
    assert host_allowed("abc.trycloudflare.com:443", DEFAULT_HOSTS)
    assert not host_allowed("trycloudflare.com", DEFAULT_HOSTS)
    assert not host_allowed("eviltrycloudflare.com", DEFAULT_HOSTS)
    assert not host_allowed("abc.trycloudflare.com.evil.example", DEFAULT_HOSTS)
    assert not host_allowed("localhost.evil.example", DEFAULT_HOSTS)
    assert not host_allowed("", DEFAULT_HOSTS)


def test_origin_allow_list():
    allowed = ["http://localhost:*", "https://claude.ai"]
    assert origin_allowed(None, allowed)  # not a browser
    assert origin_allowed("http://localhost:6274", allowed)
    assert origin_allowed("https://claude.ai", allowed)
    assert not origin_allowed("https://claude.ai.evil.example", allowed)
    assert not origin_allowed("http://localhost.evil.example", allowed)
    assert not origin_allowed("http://localhost:6274.evil.example", allowed)


def test_tokens_compare_safely():
    assert tokens_match("abc", "abc")
    assert not tokens_match("abc", "abd")
    assert not tokens_match("", "")
    # The placeholders in .env.example are public, so they must open nothing.
    placeholder = "replace-me-with-a-long-random-token"
    assert not tokens_match(placeholder, placeholder)


def test_sliding_window_forgets_after_a_minute():
    window = SlidingWindow(per_minute=1)
    assert window.allow("door", now=0.0)
    assert not window.allow("door", now=30.0)
    assert window.allow("door", now=61.0)


def test_the_url_token_never_reaches_the_access_log():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "POST", f"/mcp/t/{URL_TOKEN}", "1.1", 200),
        None,
    )
    MaskUrlToken().filter(record)
    line = record.getMessage()
    assert URL_TOKEN not in line
    assert "/mcp/t/***" in line


def access_record(path: str, status: int) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", path, "1.1", status),
        None,
    )


def test_docker_health_checks_stay_out_of_the_console():
    quiet = QuietHealthChecks()
    assert not quiet.filter(access_record("/health", 200))
    assert quiet.filter(access_record("/health", 500))  # a failing check is news
    assert quiet.filter(access_record("/mcp", 200))


def test_masking_leaves_other_lines_alone():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/health", "1.1", 200),
        None,
    )
    MaskUrlToken().filter(record)
    assert '"GET /health HTTP/1.1" 200' in record.getMessage()
