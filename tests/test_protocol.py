"""End to end with the OFFICIAL MCP client, the same one MCP hosts build on.

If these pass, the server speaks the protocol, not just our idea of it.
"""

import json
import re
from contextlib import asynccontextmanager

import asyncpg
import httpx2
import pytest
from conftest import TOKEN, make_settings, serve
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

TOOL_NAMES = {
    "data_health",
    "sales_summary",
    "online_orders",
    "product_performance",
    "stock_status",
    "margins",
    "ads_performance",
    "customer_segments",
    "whatsapp_funnel",
}

# What personal data would look like if it leaked.
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"\+593[\d ]{8,}")
NATIONAL_ID = re.compile(r"\b\d{10}\b")
STREET = re.compile(r"\b(Av\.|Calle) ")


@pytest.fixture(scope="module")
def mcp_url(seeded):
    with serve(make_settings(seeded)) as base:
        yield base + "/mcp"


@asynccontextmanager
async def connect(url: str, mode: str = "auto"):
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as http:
        async with Client(streamable_http_client(url, http_client=http), mode=mode) as client:
            yield client


async def test_a_current_client_negotiates_the_newest_protocol(mcp_url):
    async with connect(mcp_url) as client:
        assert client.session.protocol_version == "2026-07-28"
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == TOOL_NAMES


async def test_an_older_client_still_gets_in_through_the_handshake(mcp_url):
    async with connect(mcp_url, mode="legacy") as client:
        assert client.session.protocol_version == "2025-11-25"
        result = await client.call_tool("sales_summary", {})
    assert result.is_error is False


async def test_every_tool_is_declared_read_only(mcp_url):
    async with connect(mcp_url) as client:
        tools = (await client.list_tools()).tools
    for tool in tools:
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name
        assert "s" not in tool.input_schema.get("properties", {}), tool.name


@pytest.mark.parametrize("name", sorted(TOOL_NAMES))
async def test_every_tool_runs_on_the_demo_data_and_leaks_nobody(mcp_url, name):
    async with connect(mcp_url) as client:
        result = await client.call_tool(name, {})
    assert result.is_error is False, result.content
    payload = json.dumps(result.structured_content)
    for pattern in (EMAIL, PHONE, NATIONAL_ID, STREET):
        assert not pattern.search(payload), f"{name} leaked {pattern.pattern}"
    # The model reads the text copy; it must say the same as the structured one.
    assert json.loads(result.content[0].text) == result.structured_content


async def test_a_reversed_range_comes_back_as_a_readable_error(mcp_url):
    async with connect(mcp_url) as client:
        result = await client.call_tool(
            "sales_summary", {"date_from": "2026-09-10", "date_to": "2026-09-01"}
        )
    assert result.is_error is True
    assert "after" in result.content[0].text


async def test_a_malformed_date_is_rejected_by_the_schema(mcp_url):
    async with connect(mcp_url) as client:
        result = await client.call_tool("sales_summary", {"date_from": "last tuesday"})
    assert result.is_error is True


async def test_the_playbook_resource(mcp_url):
    async with connect(mcp_url) as client:
        result = await client.read_resource("store://playbook")
    assert "CTR x CPA" in result.contents[0].text


async def test_the_weekly_review_prompt(mcp_url):
    async with connect(mcp_url) as client:
        result = await client.get_prompt("weekly_review", {"week_of": "2026-09-28"})
    text = result.messages[0].content.text
    assert "2026-09-28" in text
    assert "data_health" in text


async def test_the_server_role_cannot_write_even_if_a_tool_tried(seeded):
    conn = await asyncpg.connect(seeded.read_only)
    try:
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("DELETE FROM store.sale")
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(
                "SET TRANSACTION READ WRITE; INSERT INTO store.business_setting "
                "VALUES ('x', 1, 'x')"
            )
    finally:
        await conn.close()


async def test_the_role_can_read_tables_created_after_it(seeded):
    # ALTER DEFAULT PRIVILEGES at work: a table from a future migration is
    # readable without a new GRANT.
    admin = await asyncpg.connect(seeded.admin)
    try:
        await admin.execute("CREATE TABLE IF NOT EXISTS store.future_table (id int)")
        ro = await asyncpg.connect(seeded.read_only)
        try:
            assert await ro.fetchval("SELECT count(*) FROM store.future_table") == 0
        finally:
            await ro.close()
    finally:
        await admin.execute("DROP TABLE store.future_table")
        await admin.close()
