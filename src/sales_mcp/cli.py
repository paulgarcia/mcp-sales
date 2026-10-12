"""Command line: `sales-mcp setup`, `serve`, `token` and `connect-info`."""

import argparse
import asyncio
import logging
import secrets
from datetime import date

from sales_mcp.config import get_settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sales-mcp", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Create the schema, the read-only role and demo data.")
    setup.add_argument("--reset", action="store_true", help="Drop the `store` schema first.")
    setup.add_argument("--seed", action="store_true", help="Load the deterministic demo data.")
    setup.add_argument(
        "--anchor",
        type=date.fromisoformat,
        help="Last day of the demo data, YYYY-MM-DD. Default: today.",
    )

    serve = sub.add_parser("serve", help="Run the HTTP server.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    sub.add_parser("token", help="Print a random token for MCP_TOKEN or MCP_URL_TOKEN.")

    info = sub.add_parser("connect-info", help="Print what to paste to connect each client.")
    info.add_argument(
        "--tunnel", help="cloudflared metrics URL, e.g. http://tunnel:2000. Optional."
    )
    info.add_argument("--wait", type=float, default=60.0, help="Seconds to wait for the tunnel.")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()

    if args.command == "token":
        print(secrets.token_urlsafe(32))
    elif args.command == "connect-info":
        from sales_mcp.connect import render, tunnel_host

        host = tunnel_host(args.tunnel, args.wait) if args.tunnel else None
        print(render(settings, host), flush=True)
    elif args.command == "setup":
        from sales_mcp import bootstrap
        from sales_mcp.db import asyncpg_url

        if not settings.mcp_db_password:
            raise SystemExit("Set MCP_DB_PASSWORD: the read-only role needs a password.")

        counts = asyncio.run(
            bootstrap.run(
                asyncpg_url(settings.admin_database_url),
                settings.mcp_db_password,
                reset=args.reset,
                with_seed=args.seed,
                anchor=args.anchor,
                tz=settings.business_tz,
            )
        )
        for table, n in counts.items():
            print(f"{table:20s} {n:>8,}")
        print("Schema and read-only role ready.")
    elif args.command == "serve":
        import uvicorn

        from sales_mcp.app import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
