"""Create `.env` from `.env.example`, with fresh random secrets.

    python scripts/init_env.py            # refuses to overwrite an existing .env
    python scripts/init_env.py --force    # new secrets; the demo database must be recreated

Standard library only, so it runs before anything is installed. Each placeholder
is replaced everywhere it appears, so the connection URLs in `.env` carry the
same password as the variable they belong to.
"""

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# placeholder in .env.example -> the variable it stands for
PLACEHOLDERS = {
    "replace-me-admin": "POSTGRES_PASSWORD",
    "replace-me-read-only": "MCP_DB_PASSWORD",
    "replace-me-with-a-long-random-token": "MCP_TOKEN",
    "replace-me-with-another-long-random-token": "MCP_URL_TOKEN",
}


def render(template: str) -> str:
    for placeholder in PLACEHOLDERS:
        if placeholder not in template:
            raise ValueError(f"{placeholder!r} is missing from .env.example")
        # token_urlsafe only uses [A-Za-z0-9_-], so it is safe inside a URL too.
        template = template.replace(placeholder, secrets.token_urlsafe(32))
    return template


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="overwrite an existing .env")
    args = parser.parse_args(argv)

    target = root / ".env"
    if target.exists() and not args.force:
        print(".env already exists; nothing changed. Use --force to replace it.")
        return 1
    template = (root / ".env.example").read_text(encoding="utf-8")
    target.write_text(render(template), encoding="utf-8", newline="\n")
    print(f".env created with new {', '.join(PLACEHOLDERS.values())}.")
    if args.force:
        print("New passwords: run `docker compose down -v` before the next `up`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
