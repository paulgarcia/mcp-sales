"""The MCP server: tool catalog, the playbook resource and the review prompt.

Every tool goes through `register`, never through `server.tool()` directly.
`register` is the serialization layer: it opens the read-only session, runs
the tool, and passes the result through the privacy layer. Because there is no
other way in, a new tool is private by default.
"""

import functools
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal
from importlib.resources import files
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from sales_mcp import __version__, db
from sales_mcp.privacy import redact
from sales_mcp.tools.ads import ads_performance
from sales_mcp.tools.customers import customer_segments
from sales_mcp.tools.health import data_health
from sales_mcp.tools.margins import margins
from sales_mcp.tools.orders import online_orders
from sales_mcp.tools.products import product_performance
from sales_mcp.tools.sales import sales_summary
from sales_mcp.tools.stock import stock_status
from sales_mcp.tools.whatsapp import whatsapp_funnel

log = logging.getLogger("sales_mcp.tools")

INSTRUCTIONS = """\
Read-only access to the store's data: a beauty e-commerce business in Ecuador
(skincare, makeup, supplements). Sales come from four channels: WEB catalog,
WHATSAPP bot, SELLER (sellers in person) and CONSIGNMENT.

Conventions for every tool:
- Dates are LOCAL days in America/Guayaquil, YYYY-MM-DD, both ends inclusive.
- Money is USD, sent as strings with two decimals ("12.30").
- Staff/QA test activity is excluded unless a tool says otherwise.
- Personal data never leaves: emails and phones arrive as SHA-256 hashes,
  names as initials, and counts of fewer than 5 people as "< 5".

Call data_health first. If it reports status CHECK, say so before quoting
other numbers. Prefer the tool built for the question (margins for profit,
ads_performance for ads) over recomputing from another tool's rows.
"""

TOOLS: list[tuple[Callable[..., Awaitable[dict[str, Any]]], str, str]] = [
    (
        data_health,
        "Data health",
        "Is the data fresh, and is ad attribution working? Call it FIRST, and again whenever a "
        "number looks odd. If a source is stale or attribution is broken, the other tools still "
        "answer, with numbers that look plausible and are wrong.",
    ),
    (
        sales_summary,
        "Sales summary",
        "How much was sold in a range: totals, units, average ticket, the split BY CHANNEL (only "
        "WEB sales are visible to Meta), a daily series by local day, sales by seller, the money "
        "actually collected by payment method, and a comparison with the previous period of the "
        "same length. For profit, use margins instead.",
    ),
    (
        online_orders,
        "Online orders",
        "Web-catalog and WhatsApp-bot orders in a range: counts by status, city and UTM source, "
        "plus the latest orders with their attribution (ad id) and whether they became a sale. "
        "Customer names come back as initials and emails as hashes.",
    ),
    (
        product_performance,
        "Product performance",
        "Per product variant: views, add-to-carts, units sold, conversion and revenue, from the "
        "store's own events. Flags `window_shopping` on products many people view and nobody "
        "buys. For profit use margins; for inventory use stock_status.",
    ),
    (
        stock_status,
        "Stock status",
        "Stock per variant: units on hand, reserved and in transit, recent sales and days of "
        "cover, with problem flags: out_of_stock, running_low (under 14 days of cover), "
        "advertised_while_out_of_stock (an active ad keeps spending on it) and dead_stock (no "
        "sales in 90 days). Use only_problems=true for 'what needs attention?'.",
    ),
    (
        margins,
        "Margins",
        "Real profit. Gross margin by product, brand and tier using each unit's landed cost "
        "(purchase + freight), plus the store's CONTRIBUTION margin after seller commissions, "
        "card fees and packing, with the break-even ROAS and the most a sale can cost in ads. "
        "Use this, not product_performance, for any profit question.",
    ),
    (
        ads_performance,
        "Ads performance",
        "Per Meta ad: spend, CTR, CPM and the sales each ad REALLY brought (store data, not "
        "Meta's), with CPA, ROAS, Meta's own count for comparison, and the playbook verdict "
        "(WINNER, HOOK, STORE, NICHE or NO_EVIDENCE) with the action to take. Flags ads that "
        "spend on out-of-stock products and reports MER. Use it to decide which ad to duplicate, "
        "fix or turn off.",
    ),
    (
        customer_segments,
        "Customer segments",
        "Repeat purchase, days between purchases, LTV at 90 and 180 days and RFM segments (NEW, "
        "REPEAT, DORMANT, LOST), by how customers arrived. Rows carry the SHA-256 email hash Meta "
        "uses instead of the email, ready to match custom audiences. Default range: last 365 "
        "days.",
    ),
    (
        whatsapp_funnel,
        "WhatsApp funnel",
        "The WhatsApp sales bot: new conversations per week, how many came from ads, how far "
        "each one got (bag, checkout, order, paid), the sales the bot closed, and how many chats "
        "were handed to a human.",
    ),
]

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


def jsonable(value: Any) -> Any:
    """Last line of defence for types JSON cannot carry. Tools already send
    money as strings; a stray Decimal still becomes a string, never a float."""
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def register(
    server: MCPServer, fn: Callable[..., Awaitable[dict[str, Any]]], title: str, description: str
) -> None:
    """Expose `fn(session, **params)` as a tool whose schema shows only params."""
    signature = inspect.signature(fn, eval_str=True)
    params = list(signature.parameters.values())[1:]  # the session is ours, not Claude's
    name = fn.__name__

    @functools.wraps(fn)
    async def call(**kwargs: Any) -> dict[str, Any]:
        started = time.monotonic()
        try:
            async with db.read_only_session() as session:
                raw = await fn(session, **kwargs)
        except ValueError as exc:
            # A caller mistake (a reversed range, say). The model can fix it,
            # so it gets the message. Anything else is a crash: the SDK hides
            # the details from the client and logs the traceback.
            raise ToolError(str(exc)) from exc
        result = redact(jsonable(raw))
        log.info("tool=%s ms=%d", name, (time.monotonic() - started) * 1000)
        return result

    call.__signature__ = signature.replace(parameters=params)  # type: ignore[attr-defined]
    server.add_tool(call, name=name, title=title, description=description, annotations=READ_ONLY)


def playbook() -> str:
    """The rules the numbers are judged by."""
    return files("sales_mcp").joinpath("playbook.md").read_text(encoding="utf-8")


WeekOf = Annotated[
    str | None,
    Field(description="Monday of the week to review, YYYY-MM-DD. Default: last full week."),
]


def weekly_review(week_of: WeekOf = None) -> str:
    """The store's Monday review, as a step-by-step brief for Claude."""
    week = f"the week starting {week_of}" if week_of else "the last full week (Monday-Sunday)"
    return f"""Run the store's weekly business review for {week}.

1. Call data_health. If its status is CHECK, open the review by saying what is wrong
   and which numbers below it affects.
2. sales_summary for the week: total, channel split, average ticket, and the change
   against the previous week.
3. ads_performance for the week and for the last 30 days. For each ad give the verdict
   and its action. Call out any ad spending on an out-of-stock product.
4. stock_status with only_problems=true: what is running low among the best sellers,
   and what is dead stock.
5. whatsapp_funnel for the week: conversations, paid share, handoffs.
6. Close with at most five decisions for this week, each with the number behind it.

Read the store://playbook resource for the definitions before judging anything."""


def build_server() -> MCPServer:
    server = MCPServer(
        name="sales",
        title="Store data",
        description="Sales, ads, stock, margins and customers of a beauty e-commerce store.",
        instructions=INSTRUCTIONS,
        version=__version__,
    )
    for fn, title, description in TOOLS:
        register(server, fn, title, description)
    server.resource(
        "store://playbook",
        name="playbook",
        title="Ads & margin playbook",
        description="Metric definitions, verdict rules and thresholds.",
        mime_type="text/markdown",
    )(playbook)
    server.prompt(
        name="weekly_review",
        title="Weekly business review",
        description="Monday review: data health, sales, ads verdicts, stock, "
        "WhatsApp, and five decisions.",
    )(weekly_review)
    return server
