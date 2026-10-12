"""Shared fixtures. The tests run against a real PostgreSQL, never a mock:
the bugs worth catching here live in SQL (local days, NULL handling,
privileges), and a mock would agree with whatever the code assumed.

Set TEST_ADMIN_DATABASE_URL to a role that can create databases and roles.
"""

import asyncio
import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
import uvicorn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from sales_mcp import bootstrap, seed
from sales_mcp.app import create_app
from sales_mcp.config import Settings
from sales_mcp.db import sqlalchemy_url
from sales_mcp.timeutil import tz

ADMIN = os.environ.get("TEST_ADMIN_DATABASE_URL", "postgresql://dev:dev@127.0.0.1:5432/postgres")
TEST_DB = "sales_mcp_test"
RO_PASSWORD = "test-read-only"
TOKEN = "header-token-for-tests"
URL_TOKEN = "url-token-for-tests"

TABLES = [
    "web_event",
    "shop_order",
    "conversation_event",
    "conversation",
    "ad_insight_daily",
    "ad",
    "sale_payment",
    "sale_line",
    "sale",
    "inventory_unit",
    "customer",
    "seller",
    "product_variant",
    "product",
    "brand",
    "business_setting",
]


def _url_for(database: str, user: str | None = None, password: str | None = None) -> str:
    base, _, _ = ADMIN.rpartition("/")
    if user:
        scheme, rest = base.split("://", 1)
        host = rest.split("@", 1)[-1]
        base = f"{scheme}://{user}:{password}@{host}"
    return f"{base}/{database}"


@dataclass
class Urls:
    admin: str
    read_only: str


@pytest.fixture(scope="session")
def urls() -> Iterator[Urls]:
    async def create() -> None:
        conn = await asyncpg.connect(ADMIN)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
        finally:
            await conn.close()
        conn = await asyncpg.connect(_url_for(TEST_DB))
        try:
            await bootstrap.apply_schema(conn, reset=True)
            await bootstrap.create_read_only_role(conn, RO_PASSWORD)
        finally:
            await conn.close()

    async def drop() -> None:
        conn = await asyncpg.connect(ADMIN)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        finally:
            await conn.close()

    asyncio.run(create())
    yield Urls(
        admin=_url_for(TEST_DB), read_only=_url_for(TEST_DB, bootstrap.READ_ONLY_ROLE, RO_PASSWORD)
    )
    asyncio.run(drop())


async def truncate(admin_url: str) -> None:
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(
            f"TRUNCATE {', '.join('store.' + t for t in TABLES)} RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


@pytest.fixture
async def session(urls: Urls) -> AsyncIterator[AsyncSession]:
    """A clean database and an ADMIN session to build fixtures with."""
    await truncate(urls.admin)
    engine = create_async_engine(sqlalchemy_url(urls.admin), poolclass=NullPool)
    async with AsyncSession(engine) as s:
        yield s
    await engine.dispose()


def local(day: date, hhmm: str = "12:00") -> datetime:
    """An instant at a LOCAL wall-clock time in the business zone."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz())


class Store:
    """Tiny factory: each method inserts one row and returns its id."""

    def __init__(self, s: AsyncSession) -> None:
        self.s = s

    async def _insert(self, table: str, **values: Any) -> Any:
        cols = ", ".join(values)
        params = ", ".join(f":{k}" for k in values)
        key = "key" if table == "business_setting" else "username" if table == "seller" else "id"
        row = await self.s.execute(
            text(f"INSERT INTO store.{table} ({cols}) VALUES ({params}) RETURNING {key}"), values
        )
        await self.s.commit()
        return row.scalar_one()

    async def setting(self, key: str, value: str) -> None:
        await self._insert("business_setting", key=key, value=Decimal(value))

    async def variant(
        self,
        product: str = "Serum",
        price: str = "20.00",
        brand: str = "Lumen Skin",
        tier: str = "MID",
        variant_name: str | None = None,
    ) -> int:
        bid = (
            await self.s.execute(text("SELECT id FROM store.brand WHERE name = :n"), {"n": brand})
        ).scalar()
        if bid is None:
            bid = await self._insert("brand", name=brand, tier=tier)
        pid = (
            await self.s.execute(
                text("SELECT id FROM store.product WHERE name = :n AND brand_id = :b"),
                {"n": product, "b": bid},
            )
        ).scalar()
        if pid is None:
            pid = await self._insert("product", brand_id=bid, name=product, category="SKINCARE")
        n = (await self.s.execute(text("SELECT count(*) FROM store.product_variant"))).scalar()
        return await self._insert(
            "product_variant",
            product_id=pid,
            sku=f"SKU-{n + 1}",
            variant_name=variant_name,
            public_price=Decimal(price),
        )

    async def product_of(self, variant_id: int) -> int:
        return (
            await self.s.execute(
                text("SELECT product_id FROM store.product_variant WHERE id = :v"),
                {"v": variant_id},
            )
        ).scalar_one()

    async def unit(
        self,
        variant_id: int,
        status: str = "AVAILABLE",
        cost: str = "8.00",
        freight: str | None = "1.00",
    ) -> int:
        return await self._insert(
            "inventory_unit",
            variant_id=variant_id,
            status=status,
            acquisition_unit_cost=Decimal(cost),
            import_cost=Decimal(freight) if freight is not None else None,
            received_at=datetime(2026, 1, 1, tzinfo=tz()),
        )

    async def customer(
        self,
        name: str = "Ana María Pérez",
        email: str | None = "ana@example.com",
        city: str = "Quito",
        lead_source: str = "ORGANIC",
    ) -> int:
        return await self._insert(
            "customer",
            full_name=name,
            email=email,
            phone="+593 00 1234567",
            city=city,
            address_text="Av. Amazonas N24-123",
            national_id="1712345678",
            lead_source=lead_source,
            created_at=datetime(2026, 1, 1, tzinfo=tz()),
        )

    async def seller(self, username: str, rate: str) -> str:
        return await self._insert("seller", username=username, commission_rate=Decimal(rate))

    async def sale(
        self,
        when: datetime,
        *,
        channel: str = "SELLER",
        total: str = "20.00",
        shipping: str = "0.00",
        discount: str = "0.00",
        customer: int | None = None,
        seller: str | None = None,
        internal: bool = False,
        lines: list[tuple[int, str, str, str | None]] | None = None,
        paid: list[tuple[date, str, str]] | None = None,
        fee: str = "0.00",
    ) -> int:
        """`lines` = [(variant, price, unit_cost, freight)], `paid` = [(day, amount, method)].
        By default one line at the total price and one payment the same day."""
        total_d, shipping_d, discount_d = Decimal(total), Decimal(shipping), Decimal(discount)
        sid = await self._insert(
            "sale",
            sold_at=when,
            channel=channel,
            customer_id=customer,
            seller_username=seller,
            subtotal=total_d - shipping_d + discount_d,
            discount_total=discount_d,
            shipping_fee=shipping_d,
            total_amount=total_d,
            is_internal=internal,
        )
        for variant, price, cost, freight in lines or []:
            uid = await self.unit(variant, "SOLD", cost, freight)
            await self._insert(
                "sale_line", sale_id=sid, inventory_unit_id=uid, unit_sale_price=Decimal(price)
            )
        for day, amount, method in (
            paid if paid is not None else [(when.astimezone(tz()).date(), total, "TRANSFER")]
        ):
            await self._insert(
                "sale_payment",
                sale_id=sid,
                paid_at=day,
                method=method,
                amount=Decimal(amount),
                gateway_fee=Decimal(fee),
            )
        return sid

    async def order(
        self,
        when: datetime,
        *,
        source: str = "WEB",
        status: str = "PAID",
        total: str = "20.00",
        sale: int | None = None,
        customer: int | None = None,
        ad_id: str | None = None,
        conversation: int | None = None,
        city: str = "Quito",
        internal: bool = False,
    ) -> int:
        n = (await self.s.execute(text("SELECT count(*) FROM store.shop_order"))).scalar()
        return await self._insert(
            "shop_order",
            order_number=f"T-{n + 1}",
            created_at=when,
            source=source,
            status=status,
            customer_id=customer,
            conversation_id=conversation,
            delivery_city=city,
            delivery_address="Calle Sucre 123",
            total=Decimal(total),
            sale_id=sale,
            utm_source="facebook" if ad_id else None,
            utm_content=ad_id,
            is_internal=internal,
        )

    async def event(self, when: datetime, name: str, variant: int, internal: bool = False) -> int:
        return await self._insert(
            "web_event",
            created_at=when,
            visitor_id="v1",
            event_name=name,
            variant_id=variant,
            is_internal=internal,
        )

    async def ad(
        self,
        ad_id: str,
        *,
        destination: str = "WEB",
        product: int | None = None,
        status: str = "ACTIVE",
    ) -> str:
        return await self._insert(
            "ad",
            id=ad_id,
            name=f"Ad {ad_id}",
            campaign_name="Tests",
            destination=destination,
            product_id=product,
            status=status,
        )

    async def insights(
        self,
        ad_id: str,
        first: date,
        days: int,
        *,
        spend: str = "10.00",
        impressions: int = 2000,
        clicks: int = 30,
        meta: int = 0,
    ) -> None:
        for i in range(days):
            await self.s.execute(
                text("""
                INSERT INTO store.ad_insight_daily VALUES (:a, :d, :s, :i, :c, :m)
            """),
                {
                    "a": ad_id,
                    "d": first + timedelta(days=i),
                    "s": Decimal(spend),
                    "i": impressions,
                    "c": clicks,
                    "m": meta,
                },
            )
        await self.s.commit()

    async def conversation(
        self,
        when: datetime,
        *,
        stage: str = "ENGAGED",
        from_ad: str | None = None,
        escalated: bool = False,
        events: tuple[str, ...] = (),
    ) -> int:
        cid = await self._insert(
            "conversation",
            created_at=when,
            lead_stage=stage,
            escalated_at=when if escalated else None,
            from_ad_id=from_ad,
        )
        for i, name in enumerate(events):
            await self._insert(
                "conversation_event",
                conversation_id=cid,
                created_at=when + timedelta(minutes=i + 1),
                event_type=name,
            )
        return cid


@pytest.fixture
def store(session: AsyncSession) -> Store:
    return Store(session)


@pytest.fixture(scope="module")
def seeded(urls: Urls) -> Urls:
    """The full deterministic demo data, anchored to today."""

    async def load() -> None:
        await truncate(urls.admin)
        conn = await asyncpg.connect(urls.admin)
        try:
            now = datetime.now(tz())
            await seed.load(conn, seed.generate(now.date(), tz(), cutoff=now))
        finally:
            await conn.close()

    asyncio.run(load())
    return urls


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def serve(settings: Settings) -> Iterator[str]:
    """Run the real app on a free port, in a thread. Yields the base URL."""
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings), host="127.0.0.1", port=port, log_level="warning", lifespan="on"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("The test server did not start.")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def make_settings(urls: Urls, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": urls.read_only,
        "admin_database_url": urls.admin,
        "mcp_enabled": True,
        "mcp_token": TOKEN,
        "mcp_url_token": URL_TOKEN,
        "mcp_url_token_enabled": True,
        "mcp_rate_per_min": 1000,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)
