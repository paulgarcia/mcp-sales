# Sales MCP server

[![CI](https://github.com/paulgarcia/mcp-sales/actions/workflows/ci.yml/badge.svg)](https://github.com/paulgarcia/mcp-sales/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![MCP 2026-07-28 + legacy](https://img.shields.io/badge/MCP-2026--07--28%20%2B%20legacy-6E56CF)
![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Tests: 120+ on real PostgreSQL](https://img.shields.io/badge/tests-120%2B%20on%20real%20PostgreSQL-2EA44F)
![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey)

**Ask Claude about your store in plain language, and get answers computed from the store's own
database, without customer data ever leaving it.**

- **The problem.** A small e-commerce business keeps its numbers in three places: its database,
  Meta Ads Manager and a WhatsApp bot. A weekly review means hours of spreadsheets, and Meta
  never sees most of the sales its ads produce.
- **What this is.** A read-only [Model Context Protocol](https://modelcontextprotocol.io) server
  (FastAPI + the official MCP Python SDK) that gives Claude nine tools over the store's
  PostgreSQL database: sales, ads, stock, margins, customers and WhatsApp.
- **The result.** "Run the weekly review" becomes one question. Claude checks that the data is
  fresh, calls five tools and answers with the store's numbers, an ad verdict and next steps.
- **Try it.** Four commands and Docker. No API key, no account, synthetic data.
  → [Quick start](#quick-start)

[See it](#see-it-in-action) · [Quick start](#quick-start) · [How it works](#how-it-works) ·
[Lessons from production](#lessons-from-production-pinned-by-tests) ·
[Security and privacy](#security-and-privacy) · [Design decisions](#design-decisions) ·
[Full setup](#full-setup-guide) · [Tests](#tests)

> [!NOTE]
> **Case study 1** of my AI-automation portfolio. I run a beauty e-commerce business in
> Ecuador, and this server is the cleaned-up, public version of the one in its backend. The
> **code follows the production design; the data does not**: every number here comes from a
> deterministic generator. Next case study: the WhatsApp sales bot that produces the
> conversations behind `whatsapp_funnel`.

## See it in action

You ask **"Run the weekly review."** Claude calls `data_health` first, then four more tools, and
answers. An excerpt from a real run on the demo data (the numbers change every day):

<!-- TODO: add docs/demo.gif, a 10-second recording of this answer. -->

> **Weekly review: Mon Oct 5 – Sun Oct 11, 2026**
>
> **1. Data health: OK.** All sources are fresh, and ad attribution is working.
>
> **2. Sales: up 22% on the week before.** Revenue $4,005.72 (was $3,275.22) from 111 sales;
> average ticket about the same. WhatsApp grew fastest: +70% (10 → 20 sales).
>
> **3. Ads: $322.89 spent, MER 12.4.** Ads brought 29 sales (ROAS 3.6) against a CPA ceiling
> of $12.
>
> | Ad | Week CPA / CTR | Verdict | Action |
> |---|---|---|---|
> | VitC Serum, Reel | $4.20 / 1.57% | WINNER | Duplicate it, but **restock first** |
> | WhatsApp advisor | $6.67 / 2.10% | WINNER | Duplicate it, changing one thing |
>
> *...then stock, WhatsApp, and five decisions for the week.*

<details>
<summary><b>The raw tool answer: <code>data_health</code></b></summary>

This is what Claude receives (a real response, shortened). Money travels as strings, every day
is a local day in `America/Guayaquil`, and the `note` tells Claude how to use the result.

```json
{
  "status": "OK",
  "sources": [
    {"source": "sales", "newest": "2026-10-12T15:28:50-05:00", "hours_ago": 0.3, "stale_after_hours": 48, "status": "OK"},
    {"source": "web_events", "newest": "2026-10-12T15:34:22-05:00", "hours_ago": 0.2, "stale_after_hours": 24, "status": "OK"},
    {"source": "meta_ads", "newest": "2026-10-11", "days_ago": 1, "stale_after_days": 2, "status": "OK"}
  ],
  "attribution": {
    "range": {"from": "2026-10-06", "to": "2026-10-12", "tz": "America/Guayaquil"},
    "broken": false,
    "web": {"ad_spend": "244.54", "paid_orders": 29, "paid_orders_with_ad_id": 18, "orders_with_unknown_ad_id": 0},
    "whatsapp": {"ad_spend": "35.24", "conversations": 93, "conversations_from_ads": 22}
  },
  "note": "If status is CHECK, say so before quoting any other number: a stale source or broken attribution makes the other tools wrong in a way that looks plausible."
}
```

</details>

## Quick start

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/),
[Git](https://git-scm.com/downloads), Python 3 (or Docker alone, see
[step 2](#2-create-env-with-random-secrets)) and
[Claude Code](https://docs.claude.com/en/docs/claude-code/overview). **No Claude API key**: the
server never calls Claude; Claude calls the server.

```bash
git clone https://github.com/paulgarcia/mcp-sales.git
cd mcp-sales
python scripts/init_env.py          # creates .env with random secrets
docker compose up --build           # database + demo data + server
```

When the server is ready, the console prints a box with the exact `claude mcp add ...` line to
paste. Run it inside the project folder, start `claude`, and ask **"Run the weekly review."**

To use it from **Claude.ai** instead, start with `docker compose --profile tunnel up --build`:
the box then also prints a connector URL. Details in the [full setup guide](#full-setup-guide).

## What you can ask

| Question | Tools Claude uses | What comes back |
|---|---|---|
| "How much did we sell last week, and how does it compare?" | `sales_summary` | Totals, units, average ticket, split by channel and seller, a daily series by local day, the previous period |
| "Which ads should I scale, fix or turn off?" | `ads_performance` | Per ad: spend, CTR, CPA, ROAS, Meta's own count beside the store's, and a verdict with an action |
| "Are we paying for ads on products we can't sell?" | `stock_status`, `ads_performance` | The ads whose product is out of stock, with their spend |
| "What is about to run out?" | `stock_status` | Units on hand, in transit and reserved, days of cover, four problem flags |
| "What is our real margin? What is our break-even ROAS?" | `margins` | Gross margin from each unit's real cost, contribution margin after commissions, card fees and packing, break-even ROAS and CPA |
| "Which products get attention but no sales?" | `product_performance` | Views, add-to-carts, units and conversion per variant, flagged "window shopping" |
| "How many WhatsApp chats became orders?" | `whatsapp_funnel` | Conversations, how far each got, sales the bot closed, handoffs to humans |
| "Run the weekly review." | the `weekly_review` prompt | Five tools in order (ads for the week and for 30 days), then a written review with up to five decisions |

The demo data is built so that these questions have interesting answers: a winning ad whose
product is almost sold out, an ad still spending money on an empty shelf, a perfume that many
people view and nobody buys, and a WhatsApp ad that Meta reports as zero sales while the store
records real orders from it.

## How it works

<p align="center">
  <img src="docs/architecture.svg" width="880" alt="Claude Code, Claude.ai or MCP Inspector call the server over HTTPS. A gate checks host, origin, token and rate limit; the MCP SDK speaks the protocol; register() opens a read-only session and runs one of nine SQL tools; privacy.redact() cleans the result before the answer goes back. PostgreSQL is reached through a SELECT-only role.">
</p>

1. **The gate** refuses unknown hosts and browser origins (DNS-rebinding protection), checks
   the token (in a header, or in the URL for clients that cannot send headers) and applies a
   rate limit per door.
2. **The official MCP SDK** speaks the protocol: the newer `server/discover` era
   (`2026-07-28`) and the classic `initialize` handshake (`2024-11-05` to `2025-11-25`). The
   transport is stateless with JSON replies, so a restart never breaks a client.
3. **`register()`** is the only way a tool reaches the server. It opens a read-only database
   session and runs the tool: a small module of plain SQL that returns a dict.
4. **`privacy.redact()`** cleans every result before it leaves. See
   [Security and privacy](#security-and-privacy).

<details>
<summary><b>The nine tools, one resource and one prompt</b></summary>

Every tool is declared read-only (`readOnlyHint: true`) and works with no input: dates, brand
and filters are optional.

| Tool | What it answers |
|---|---|
| `data_health` | Is every data source fresh, and is ad attribution working? Claude is told to call it first. |
| `sales_summary` | Totals, units, average ticket, split by channel, daily series by local day, by seller, money collected, previous period. |
| `online_orders` | Web and bot orders with status, city, UTM source, ad id, and whether each became a sale. |
| `product_performance` | Views, add-to-carts, units sold and conversion per variant; flags "window shopping". |
| `stock_status` | Units on hand, in transit and reserved, days of cover, and four problem flags. |
| `margins` | Gross margin from each unit's real landed cost, plus contribution margin, break-even ROAS and break-even CPA. |
| `ads_performance` | Spend, CTR, CPM and the sales each ad really brought, with CPA, ROAS, Meta's own count, and a verdict. |
| `customer_segments` | Repeat rate, days between purchases, LTV at 90/180 days and RFM segments, with hashed emails. |
| `whatsapp_funnel` | Bot conversations, how far each one got, sales the bot closed, and handoffs to humans. |

The **resource** `store://playbook` holds the metric definitions and verdict rules. The
**prompt** `weekly_review` is the Monday review as a step-by-step brief.

</details>

## Lessons from production, pinned by tests

Problems that shipped in the production system, or were caught just before they did, with the
fix and the test that now keeps each one from coming back.

| What went wrong | The fix | The test that guards it |
|---|---|---|
| A commission rate stored as a fraction (0.20) was divided by 100 again: a $12 commission became $0.12, and the contribution margin was inflated for months. | The rate is a fraction everywhere and is never divided again. | `test_gross_and_contribution_are_different_numbers` |
| The Meta Conversions API stopped sending events for 8 days, and nobody noticed. | `data_health` checks every source's freshness and whether attribution still works, and Claude is told to call it first. | `test_health.py` |
| Counts of *things* (units) would have gone through the anonymity rule meant for *people*, turning "7 units" into "< 5". | The rule applies only to `by_*` breakdowns of people; breakdowns of things are lists. | `test_breakdowns_are_lists_and_survive_k_anonymity` |
| With a filter, a total of returned rows said "0 of 0", which reads as "empty catalog". | Two counts: how many were checked and how many came back. | `test_filtering_does_not_hide_how_much_was_checked` |
| A daily series grouped by UTC day moved evening sales to the next day. | Every day is a local day in `America/Guayaquil`. | `test_the_daily_series_uses_the_local_day` |
| *Found while building this repository:* `bool_or()` over a product with no stock rows returns `NULL`, which silently hid the "out of stock" warning on ads. | `coalesce(..., false)` | `test_an_ad_spending_on_an_out_of_stock_product_is_called_out` |

## Security and privacy

Each control below is **enforced** by code or by the database, and **tested**. Design intent
is labeled as such.

| Control | How it is enforced | Tested by |
|---|---|---|
| The server cannot write | The `mcp_reader` role is granted `SELECT` only, starts every session with `default_transaction_read_only = on`, and stops any query after 15 s. The server also asks for a read-only transaction on each connection, so even a wrong `DATABASE_URL` cannot write. | `test_the_server_role_cannot_write_even_if_a_tool_tried` |
| Personal data is redacted in one place | `register()` passes every result through `privacy.redact()`. A tool cannot skip it, and a new tool is redacted by default. | `test_every_tool_runs_on_the_demo_data_and_leaks_nobody`, `test_redacts_at_any_depth` |
| Only a token gets in | A constant-time comparison. An empty token, or a placeholder from `.env.example`, opens nothing. | `test_no_token_no_entry`, `test_an_empty_configured_token_lets_nobody_in`, `test_tokens_compare_safely` |
| The token in the URL stays out of logs | It is masked as `/mcp/t/***` in the access log, and errors never repeat it. | `test_the_url_token_never_reaches_the_access_log`, `test_the_url_door_rejects_a_wrong_token_without_echoing_it` |
| Unknown hosts and browser origins are refused | DNS-rebinding protection: `421` for a host, `403` for an origin outside the allow-list. | `test_a_foreign_host_header_is_refused`, `test_a_foreign_browser_origin_is_refused` |
| No client can flood the database | A sliding-window rate limit per door, with `Retry-After`. | `test_rate_limit_says_when_to_retry` |

**What `privacy.redact()` does**, shown on a made-up row (this is the function's real output):

| In the database (never leaves) | What Claude receives |
|---|---|
| `"customer_name": "Ana María Pérez"` | `"customer_name": "A. M. P."` |
| `"email": "Ana.Perez@Example.com "` | `"hash_email": "3c6c5c25f4b6…"` (SHA-256 of `ana.perez@example.com`) |
| `"phone": "+593 99 123 4567"` | `"hash_phone": "3fb8bc4d3942…"` (SHA-256 of `593991234567`) |
| `"address": "Av. Amazonas N34-12, Quito"` | *(key dropped)* |
| `"national_id": "1712345678"` | *(key dropped)* |
| `"city": "Quito"` | `"city": "Quito"` (aggregable, needed to plan delivery) |
| `"by_city": {"Quito": 42, "Puyo": 3}` | `"by_city": {"Quito": 42, "Puyo": "< 5"}` |

> [!IMPORTANT]
> Hashing an email or a phone is **pseudonymization, not anonymization**. The hash is
> deliberate: it is normalized exactly as Meta's Conversions API expects, so a list can still be
> matched against ad audiences. Anyone who already has a person's email can compute the same hash
> and recognize them. Treat hashed lists as personal data.

**Not covered here, by design:** OAuth (one static token per door, rotated by regenerating
`.env`), several tenants (one store, one read-only role), and load testing.

## Design decisions

<details open>
<summary><b>Tool descriptions are part of the product</b></summary>

Claude chooses tools from their descriptions, so each description says when to use the tool
*instead of another one*: "Use this, not product_performance, for any profit question." The
server instructions set the conventions once: local days, money as strings, and "call
`data_health` first; if it says CHECK, say so before quoting other numbers."

</details>

<details>
<summary><b>Numbers that are easy to get wrong</b></summary>

- **Local days.** A "day" is a day in America/Guayaquil (UTC-5). A sale at 20:30 in Quito
  belongs to that day, not to the next UTC day.
- **Money is a string** (`"12.30"`). A float in JSON loses cents.
- **No base, no percentage.** A change against zero is `null`, not `0 %`, because `0 %` reads
  as "nothing changed".
- **Sold and collected are different.** A credit sale counts once in revenue, and in
  installments in money collected.
- **"Checked" vs "returned".** With a filter, "0 of 0" reads as "no catalog" instead of "no
  problems", so `stock_status` reports both counts.

</details>

<details>
<summary><b>The store's own attribution, with Meta's next to it</b></summary>

Meta only sees buyers who accept cookies, and never sees a sale closed in a chat. The store
matches every web order to its ad through `utm_content={{ad.id}}`, and every chat to the ad that
opened it. `ads_performance` uses the store's numbers for CPA and ROAS and shows Meta's count
beside them, so the undercount is measured, not guessed. It also reports MER (all revenue
divided by all ad spend), which needs no attribution at all.

</details>

<details>
<summary><b>A verdict for every ad, but never for a new one</b></summary>

The verdict follows the store's ads playbook, a CTR × CPA tree: **WINNER**, **HOOK** (the hook
does not stop the scroll), **STORE** (people click, but the page or the price does not close),
**NICHE**, or **NO_EVIDENCE**. An ad with fewer than 5 days of delivery in its *whole life* is
not judged: turning an ad off after two bad days is the most common way to kill one that was
about to work.

</details>

<details>
<summary><b>Two doors, and a host check that works with any quick tunnel</b></summary>

`/mcp` takes `Authorization: Bearer <MCP_TOKEN>`. Some clients take only a URL, so
`/mcp/t/<MCP_URL_TOKEN>` exists too. A credential in a URL is a known risk, so that door has its
own token and its own off switch (`MCP_URL_TOKEN_ENABLED`), its token is masked in the access
log, and errors never repeat it. Each door has its own rate-limit bucket. Tokens do not expire:
they change when `.env` is regenerated (`python scripts/init_env.py --force`).

Both doors sit behind the Host and Origin check. The SDK's own check has no subdomain wildcard,
and a Cloudflare quick tunnel gets a new random `*.trycloudflare.com` name on every start, so
the gate does the check itself and allows that pattern by default: a reviewer can open a tunnel
without editing anything. It is safe because those names resolve to Cloudflare, never to
`127.0.0.1`, so they cannot rebind a browser onto your machine.

</details>

<details>
<summary><b>Deterministic demo data, anchored to today</b></summary>

Every `docker compose up` rebuilds 240 days of synthetic history that ends now, in the store's
time zone. The same seed always produces the same patterns, so a demo can be recorded twice and
tell the same stories; only the exact amounts depend on the day. Meta data ends yesterday, as
Meta reports a day only once it is over.

</details>

## Full setup guide

### Prerequisites

| Tool | What for | Needed? |
|---|---|---|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Compose v2) | Runs the database, the demo data, the server, the tunnel and the tests | Yes |
| [Git](https://git-scm.com/downloads) | Gets the code | Yes |
| [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) | Asks the questions, with any Claude plan | Yes, to talk to the data |
| Python 3, or Docker alone | Generates the random secrets (step 2) | One of the two |
| [Node.js](https://nodejs.org/) 18+ | Runs MCP Inspector, to call the tools by hand | Optional |

When Windows and macOS/Linux need different commands, both are shown with a label: use only the
one for your system.

### 1. Get the code

```bash
git clone https://github.com/paulgarcia/mcp-sales.git
cd mcp-sales
```

### 2. Create `.env` with random secrets

One command creates `.env` and fills `POSTGRES_PASSWORD`, `MCP_DB_PASSWORD`, `MCP_TOKEN` and
`MCP_URL_TOKEN` with fresh random secrets:

```bash
python scripts/init_env.py
```

No Python? Docker can run it (PowerShell and bash; in `cmd.exe`, write `%cd%` instead of
`${PWD}`):

```bash
docker run --rm -v "${PWD}:/app" -w /app python:3.12-slim python scripts/init_env.py
```

It never overwrites an existing `.env`. To start again with new secrets, run it with `--force`,
then `docker compose down -v` so the database is recreated with them.

### 3. Start it

Choose one:

```bash
docker compose up --build                    # for Claude Code and MCP Inspector
docker compose --profile tunnel up --build   # the same + a public URL for Claude.ai
```

The first builds and starts PostgreSQL, fresh demo data and the server on `127.0.0.1:8000`. The
second also starts a free
[Cloudflare quick tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
in Docker (no account, nothing to install), so Claude.ai can reach your server.

When everything is ready, the console prints a box with **everything you need to paste**:

```text
connect-1  | ==============================================================================
connect-1  |  The MCP server is ready. Copy what you need below.
connect-1  |  ...
connect-1  |  Claude Code: run this inside the project folder (PowerShell, bash or zsh):
connect-1  |    claude mcp add --transport http sales http://127.0.0.1:8000/mcp --header "Authorization: Bearer ..."
connect-1  |  MCP Inspector: ...
connect-1  |  Claude.ai / Claude Desktop: Settings -> Connectors -> Add custom connector:
connect-1  |    URL:  https://<random-name>.trycloudflare.com/mcp/t/...
```

To see the box again, run `docker compose logs connect --no-log-prefix` in a second terminal.

> [!WARNING]
> The box contains your secrets. Do not show it in a screenshot or a video.

Two quick checks, if you want them: `http://127.0.0.1:8000/health` in a browser answers
`"status":"ok"` with `"tools":9`, and `http://127.0.0.1:8000/mcp` answers **"Missing or invalid
token."**, which is correct: the server refuses anyone without the token.

Stop it with `Ctrl+C`. The next `docker compose up` rebuilds the demo data, anchored to today.

### 4. Connect Claude Code

Copy the `claude mcp add ...` line from the box and run it **inside the project folder**. Claude
Code saves a server for the folder you add it from (its default "local" scope), so a server added
from another folder does not appear here. Then check it:

```bash
claude mcp list               # "sales" should show as connected
```

Start `claude` in the same folder; `/mcp` lists `sales` with 9 tools. To use it from every
folder, add `--scope user` to `claude mcp add`. To remove it: `claude mcp remove sales`.

Then ask:

- "Run the weekly review."
- "Are we paying for ads on products we can't sell?"
- "What is our real margin, and our break-even ROAS?"

### 5. Optional: connect Claude.ai or Claude Desktop

<details>
<summary><b>Step by step, with the connector form</b></summary>

Claude.ai and Claude Desktop connect from **Anthropic's cloud**, not from your computer, so they
cannot reach `127.0.0.1`: they need a public HTTPS address. That is what the `tunnel` profile
adds. Nothing to edit: the server accepts any `*.trycloudflare.com` address by default, and the
URL door is on in `.env`.

1. Start with the tunnel:

   ```bash
   docker compose --profile tunnel up --build
   ```

2. **Find the connector URL.** In the box, the **last** part is *Claude.ai / Claude Desktop*,
   and the line you need starts with `URL:  https://` and contains `trycloudflare.com/mcp/t/`:

   ```text
   connect-1  |  Claude.ai / Claude Desktop: Settings -> Connectors -> Add custom connector:
   connect-1  |
   connect-1  |    URL:  https://some-random-words.trycloudflare.com/mcp/t/AbC123...xYz
   ```

   Copy from `https://` to the **end of the line**: the long token after `/mcp/t/` is part of
   it. Do not copy `connect-1  |` or `URL:`.

   The tunnel keeps logging after the box, so it scrolls up. To get only that line, run this in
   a **second terminal**, in the project folder:

   **Windows (PowerShell):**

   ```powershell
   docker compose logs connect --no-log-prefix | Select-String trycloudflare
   ```

   **macOS / Linux (bash):**

   ```bash
   docker compose logs connect --no-log-prefix | grep trycloudflare
   ```

   To copy in Windows Terminal, select the text with the mouse and press `Ctrl+C`.

3. In Claude.ai (or the Claude Desktop app): **Settings → Connectors → Add custom connector**.
   The form has two screens.

   **First screen:**

   | Field | Value |
   |---|---|
   | Name | `Sales demo` (any name) |
   | Remote MCP server URL | the URL from step 2 |

   **Second screen** (it shows the name and URL at the top):

   | Section | What to do |
   |---|---|
   | Authentication | Keep **No sign-in** (Claude marks it *Detected*). This server has no OAuth login: the token is already inside the URL. |
   | The orange warning | *"Without sign-in, anyone with the server URL can use this connector."* True here, because the URL contains the token. Nothing to change. |
   | Request headers | Leave empty. |
   | Advanced | Leave as it is. |

   Click **Add**.

4. In a new chat, turn the connector on from the tools menu and ask "Run the weekly review."
   Claude calls `data_health` first; `"status": "OK"` means the whole path works: Claude →
   Cloudflare → your Docker server → the demo data. Claude Desktop uses the same connectors as
   your Claude.ai account.

*Optional, safer:* the same form can send a header instead. Use the URL **without** the token
(`https://<random-name>.trycloudflare.com/mcp`) and, under **Request headers → Add header**, the
name `Authorization` with the value `Bearer <MCP_TOKEN>` (the Claude Code line in the box shows
it). Then the URL alone opens nothing, and Claude stores the token securely.

> [!WARNING]
> While the tunnel runs, **the connector URL is a password**: anyone who has it can read the
> demo data. Stop with `Ctrl+C` when you are done. To close the URL door completely, set
> `MCP_URL_TOKEN_ENABLED=false` in `.env`. The tunnel gets a **new random name every time it
> starts**: after a restart, copy the new URL and edit the connector.

| What you see | Why | Fix |
|---|---|---|
| The box says *No tunnel is running* | Started without `--profile tunnel`, or cloudflared stopped | Start with the profile; read `docker compose logs tunnel` |
| `401` / "Missing or invalid token. Check the connector URL." | The URL is incomplete or from an older `.env` | Copy it again from the box |
| Claude cannot connect at all | The tunnel restarted and got a new name | Get the new URL with the command in step 2 and edit the connector |
| `421 Host not allowed` | You expose the server under your own domain | Add that host to `MCP_ALLOWED_HOSTS` in `.env` |

Prefer your own `cloudflared`? `cloudflared tunnel --url http://127.0.0.1:8000` works too; take
the address it prints and add `/mcp/t/<MCP_URL_TOKEN>`.

</details>

### 6. Optional: test the tools by hand with MCP Inspector

<details>
<summary><b>Step by step, with what to try and how to troubleshoot</b></summary>

[MCP Inspector](https://github.com/modelcontextprotocol/inspector) is the official test tool for
MCP servers. It calls the tools **directly, without any AI**, so you see exactly what Claude
would receive. It needs [Node.js](https://nodejs.org/) 18 or newer, and the server from step 3
must be running.

**Start it** (in a new terminal, from any folder):

```bash
npx @modelcontextprotocol/inspector
```

It opens your browser at `http://127.0.0.1:6274/?MCP_INSPECTOR_API_TOKEN=...`. That token
belongs to Inspector itself, not to this server; leave it in the address bar.

**Add the server**

1. Click **Add Servers** and fill in the form:

   | Field | Value |
   |---|---|
   | Server ID | `sales` (any name; letters, numbers, `-`, `_`) |
   | Transport | `streamable-http` |
   | URL | `http://127.0.0.1:8000/mcp` |

   Click **Add**. A `sales` card appears, **Disconnected**.

2. On that card, click **Settings**, open **Custom Headers** and add one header:

   | Name | Value |
   |---|---|
   | `Authorization` | `Bearer <your MCP_TOKEN>` |

   The box from step 3 shows this exact value. Leave **OAuth Settings** empty: this server uses
   a fixed token, not OAuth.

3. Optional, in the same window: **Protocol Era**. The server speaks both, so either works:
   `Legacy (2025-11-25 handshake)` is the classic `initialize` handshake; the modern era uses
   `2026-07-28` and `server/discover`. The card shows which version was negotiated.

4. Close the window (**X**) and turn on the card's switch. It turns green: **Connected**.

**Try it**

| Tab | Do this | You should see |
|---|---|---|
| Tools | List the tools | 9 tools, each with a description that tells Claude when to use it, and `readOnlyHint: true` |
| Tools | Run `data_health` | `"status": "OK"`, every source fresh, and the attribution check |
| Tools | Run `ads_performance` | 8 ads, each with a verdict (`WINNER`, `HOOK`, `STORE`, ...). The `HA Gel Cream` ad warns: *"The advertised product is OUT OF STOCK"* |
| Tools | Run `stock_status` with `only_problems` = `true` | Only the products with a problem: out of stock, advertised while out of stock, running low, or dead stock |
| Tools | Run `customer_segments` | Customers as `hash_email` only: no names, emails or phones |
| Tools | Run `sales_summary` with `date_from` **after** `date_to` | An error Claude can read and fix: *"date_from (...) is after date_to (...)"* |
| Prompts | Get `weekly_review` | The Monday review, step by step, starting with `data_health` |
| Resources | Read `store://playbook` | The metric definitions and the rules behind each verdict |

The panel on the right has two views: **Protocol** shows each JSON-RPC message, and **Network**
shows each HTTP request with its status code.

**If it does not connect**

| What you see | Why | Fix |
|---|---|---|
| **Failed**; Network shows `401` on `/mcp`, then `404`s on `/.well-known/...` and `/register` | No token was sent, so Inspector looked for OAuth, which this server does not offer | Add the `Authorization` header (step 2) and connect again |
| `401` with the header set | Wrong token, or `Bearer ` is missing | Copy the value from the box again; check the space after `Bearer` |
| Connection refused, or nothing answers | The server is not running | Start it (step 3); `http://127.0.0.1:8000/health` must answer |
| `421 Host not allowed` | The URL uses a host the server does not trust | Use `127.0.0.1` or `localhost`, or add the host to `MCP_ALLOWED_HOSTS` |
| `429 Too Many Requests` | The rate limit (`MCP_RATE_PER_MIN`, 60 by default) | Wait a minute |

(Labels from MCP Inspector v2.10.)

</details>

### Without Docker

<details>
<summary><b>Python 3.12, uv and your own PostgreSQL 16</b></summary>

Shown in bash.

```bash
uv sync
createdb shop
export ADMIN_DATABASE_URL=postgresql://postgres:<password>@127.0.0.1:5432/shop
export MCP_DB_PASSWORD=<choose one>
uv run sales-mcp setup --seed            # schema + read-only role + demo data

export DATABASE_URL=postgresql://mcp_reader:$MCP_DB_PASSWORD@127.0.0.1:5432/shop
export MCP_ENABLED=true MCP_TOKEN=$(uv run sales-mcp token)
uv run sales-mcp serve                   # http://127.0.0.1:8000/mcp
```

</details>

## Tests

With Docker only (no Python or PostgreSQL on your machine):

```bash
docker compose --profile test run --rm --build tests
docker compose --profile test down -v     # remove the test database
```

This starts a **separate, throwaway** PostgreSQL, so the tests never touch the demo's data.

<details>
<summary><b>With Python and your own PostgreSQL</b></summary>

**Windows (PowerShell):**

```powershell
$env:TEST_ADMIN_DATABASE_URL = "postgresql://<user>:<password>@127.0.0.1:5432/postgres"
uv run pytest
```

**macOS / Linux (bash):**

```bash
export TEST_ADMIN_DATABASE_URL=postgresql://<user>:<password>@127.0.0.1:5432/postgres
uv run pytest
```

</details>

More than 120 tests, most of them against a real PostgreSQL, because the bugs worth catching here
live in SQL: local days, NULLs and privileges. A mock would agree with whatever the code assumed.

- **Unit:** privacy rules, local-day ranges, the ad verdict tree, host and origin matching.
- **Tools:** each tool on small, hand-made data, with exact expected numbers.
- **HTTP:** both doors, the rate limit, DNS-rebinding protection and log masking, over real
  HTTP.
- **Protocol:** the official MCP client negotiates `2026-07-28` and the legacy handshake, calls
  every tool on the full demo data, and checks that no email, phone, address or national id
  appears in any answer.
- **Seed:** the same seed always produces the same data, and the stories it was built to tell
  stay true.

**Not tested:** load and concurrency limits, a real Cloudflare tunnel (the tests use a stand-in
for cloudflared's metrics endpoint), and the Claude clients themselves.

## Production vs this repository

| | Production | This repository |
|---|---|---|
| Tools | 17, including competitor prices and weekly marketing snapshots | 9 |
| Protocol layer | Hand-written JSON-RPC router in FastAPI | Official MCP Python SDK, both protocol eras |
| Data | Live PostgreSQL on AWS RDS | Deterministic synthetic data |
| Secrets | AWS SSM Parameter Store | `.env` file |
| Read-only role, privacy layer, two doors, verdict tree | Yes | Yes, same design |

## Project layout

```text
src/sales_mcp/
  app.py         FastAPI app: the gate (host, origin, doors, rate limit), /health, MCP mount
  server.py      MCPServer: register(), tool catalog, resource, prompt
  privacy.py     the redaction rules
  db.py          read-only engine
  timeutil.py    local days, money, percentages
  tools/         one module per tool, plain SQL
  schema.sql     demo schema
  seed.py        deterministic demo data
  bootstrap.py   creates schema, read-only role and data
  cli.py         sales-mcp setup | serve | token | connect-info
  connect.py     the "copy what you need" box printed by docker compose
scripts/init_env.py   creates .env with random secrets
docs/architecture.svg the diagram above
tests/                most against PostgreSQL
```

## License

[MIT](LICENSE)
