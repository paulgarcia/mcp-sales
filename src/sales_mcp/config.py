"""Runtime settings, read from the environment (or a .env file).

Everything is off or empty by default. A fresh deploy with no configuration
answers 404 on /mcp instead of opening a door nobody has configured yet.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# A list setting accepts a plain comma-separated value in the environment
# (MCP_ALLOWED_HOSTS=a.example.com,localhost:*), not only JSON.
CommaList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The connection the TOOLS use. Point it at the read-only role created by
    # `sales-mcp setup`: then even a buggy tool cannot write, because Postgres
    # refuses. That is what makes "read-only" a guarantee instead of a promise.
    database_url: str = "postgresql+asyncpg://mcp_reader@127.0.0.1:5432/shop"

    # Only `sales-mcp setup` and the tests use this one.
    admin_database_url: str = "postgresql://postgres@127.0.0.1:5432/shop"

    # Password given to the read-only role when `setup` creates it. No default:
    # a public repo must not ship one.
    mcp_db_password: str = ""

    mcp_enabled: bool = False

    # Door 1: `Authorization: Bearer <token>`. An empty token opens nothing.
    mcp_token: str = ""

    # Door 2: the token travels in the path, `/mcp/t/<token>`. It exists because
    # Claude.ai custom connectors cannot send custom headers. It is a credential
    # in a URL, so it has its own token, its own off switch, and it is masked in
    # the access log.
    mcp_url_token: str = ""
    mcp_url_token_enabled: bool = False

    # Calls per minute, per door.
    mcp_rate_per_min: int = Field(default=60, ge=1)

    # DNS-rebinding protection: the Host headers the MCP endpoint accepts.
    # `*.trycloudflare.com` lets a Cloudflare quick tunnel work with no edit,
    # although its name changes on every start. Add your own domain here.
    mcp_allowed_hosts: CommaList = ["localhost:*", "127.0.0.1:*", "*.trycloudflare.com"]
    # Origin is checked only when a client sends one. Claude's own web origins
    # are listed so a Claude.ai connector is never refused for sending it; the
    # token, not the origin, is what grants access.
    mcp_allowed_origins: CommaList = [
        "http://localhost:*",
        "http://127.0.0.1:*",
        "https://claude.ai",
        "https://claude.com",
    ]

    # Every "day" in a tool is a local day in this zone.
    business_tz: str = "America/Guayaquil"

    @field_validator("mcp_allowed_hosts", "mcp_allowed_origins", mode="before")
    @classmethod
    def _split_commas(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
