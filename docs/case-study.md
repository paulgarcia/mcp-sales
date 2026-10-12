# Case study: Claude answers business questions from my store's data

**Role:** sole developer · **Stack:** Python, FastAPI, MCP, PostgreSQL · **Status:** [TODO: confirm,
e.g. "running in production since <month>"]. This repository is the public version,
with synthetic data.

## The problem

My store sells beauty products in Ecuador through four channels: a web catalog, a WhatsApp bot,
sellers, and consignment. The numbers that run the business lived in different places. Sales
and stock were in our database, ads were in Meta Ads Manager, and chats were in the bot.

A simple question like "which ads should I turn off this week?" meant exporting, matching and
building spreadsheets: [TODO: X hours every Monday]. Ads Manager also undercounts. It never sees
seller or WhatsApp sales, and it loses buyers who reject cookies. Decisions were made on
incomplete numbers.

## What I built

A read-only **MCP server** that lets Claude query the store's data directly and answer in plain
language. It has nine tools (seventeen in production), one per kind of question: sales, orders,
products, stock, margins, ads, customers, WhatsApp, and data health. Claude chooses the tool
from its description, and every answer uses local days and exact money.

Three decisions make it safe to connect to a production database:

1. **Read-only is enforced by PostgreSQL.** The server's role can only `SELECT`, its sessions
   start read-only, and its queries time out.
2. **Privacy lives in one place.** Emails and phones leave as hashes in Meta's format.
   Addresses and IDs are dropped, names become initials, and groups of fewer than five people
   are hidden. A new tool is private by default.
3. **Authentication fits the clients.** It uses a bearer token, plus a URL-token door for
   Claude.ai connectors. That door has its own token, an off switch, a rate limit and log
   masking.

The ads tool uses the store's own attribution instead of Meta's. It gives each ad a verdict
from our playbook: scale it, fix the hook, fix the page, leave it, or wait.

## The result

- [TODO: Monday review: before, X hours of spreadsheets. After, Y minutes of conversation with Claude.]
- [TODO: one decision Claude surfaced, with its dollar impact, e.g. an ad spending on an
  out-of-stock product.]
- [TODO: measured Meta undercount: Ads Manager saw X % of the sales its ads produced.]
- 126 automated tests against a real PostgreSQL. It works with the newest MCP protocol
  (`2026-07-28`) and with older clients.

## Tech stack

Python 3.12 · FastAPI · MCP Python SDK (Streamable HTTP) · PostgreSQL 16 · SQLAlchemy 2 (async)
· pytest · Docker · GitHub Actions · Claude (claude.ai, Desktop and Code) as the client.

**Code:** [TODO: repository link] · **Video:** [TODO: Loom link]
