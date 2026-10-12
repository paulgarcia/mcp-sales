"""Create the schema, the read-only role and the demo data.

Runs with the ADMIN connection. The server itself never does.
"""

from __future__ import annotations

from datetime import date, datetime
from importlib.resources import files
from zoneinfo import ZoneInfo

import asyncpg

from sales_mcp import seed

READ_ONLY_ROLE = "mcp_reader"


def schema_sql() -> str:
    return files("sales_mcp").joinpath("schema.sql").read_text(encoding="utf-8")


async def apply_schema(conn: asyncpg.Connection, *, reset: bool) -> bool:
    """Create the `store` schema. Returns False when it already existed."""
    if reset:
        await conn.execute("DROP SCHEMA IF EXISTS store CASCADE")
    exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'store')"
    )
    if exists:
        return False
    await conn.execute(schema_sql())
    return True


async def create_read_only_role(
    conn: asyncpg.Connection, password: str, role: str = READ_ONLY_ROLE
) -> None:
    """The role the server connects with: it can read `store` and nothing else.

    Three layers, so no single mistake opens a write path:
      1. it is only GRANTed SELECT;
      2. its sessions start read-only, so even a stray GRANT would not help;
      3. queries are cut at 15 s, so one slow question cannot hog the database.

    `ALTER DEFAULT PRIVILEGES` matters more than it looks: without it the next
    table a migration creates is invisible to the role, and a new tool fails
    with "permission denied" for no obvious reason.
    """
    exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
    verb = "ALTER" if exists else "CREATE"
    # Identifiers and literals are quoted by the server (format %I / %L),
    # never by string concatenation.
    statement = await conn.fetchval(
        f"SELECT format('{verb} ROLE %I WITH LOGIN PASSWORD %L', $1::text, $2::text)",
        role,
        password,
    )
    await conn.execute(statement)
    database = await conn.fetchval("SELECT current_database()")
    for template, args in (
        ("ALTER ROLE %I SET default_transaction_read_only = on", [role]),
        ("ALTER ROLE %I SET statement_timeout = '15s'", [role]),
        ("GRANT CONNECT ON DATABASE %I TO %I", [database, role]),
        ("GRANT USAGE ON SCHEMA store TO %I", [role]),
        ("GRANT SELECT ON ALL TABLES IN SCHEMA store TO %I", [role]),
        ("ALTER DEFAULT PRIVILEGES IN SCHEMA store GRANT SELECT ON TABLES TO %I", [role]),
    ):
        placeholders = ", ".join(f"${i}::text" for i in range(2, len(args) + 2))
        sql = await conn.fetchval(f"SELECT format($1::text, {placeholders})", template, *args)
        await conn.execute(sql)


async def run(
    admin_url: str, ro_password: str, *, reset: bool, with_seed: bool, anchor: date | None, tz: str
) -> dict[str, int]:
    zone = ZoneInfo(tz)
    conn = await asyncpg.connect(admin_url)
    try:
        created = await apply_schema(conn, reset=reset)
        await create_read_only_role(conn, ro_password)
        counts: dict[str, int] = {}
        if with_seed:
            if not created:
                raise SystemExit("The schema already has data. Use --reset to rebuild it.")
            now = datetime.now(zone)
            day = anchor or now.date()
            cutoff = now if day == now.date() else None
            async with conn.transaction():
                counts = await seed.load(conn, seed.generate(day, zone, cutoff=cutoff))
        return counts
    finally:
        await conn.close()
