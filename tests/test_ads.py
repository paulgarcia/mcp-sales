from datetime import date, timedelta
from decimal import Decimal

import pytest
from conftest import local
from sqlalchemy import text

from sales_mcp.tools.ads import ads_performance, verdict_key

CEILING = Decimal("12")


@pytest.mark.parametrize(
    ("ctr", "cpa", "days_alive", "spend", "expected"),
    [
        (1.5, Decimal("6"), 3, Decimal("40"), "NO_EVIDENCE"),  # still learning
        (1.5, None, 10, Decimal("8"), "NO_EVIDENCE"),  # no sale, but spent < 1 CPA
        (1.5, None, 10, Decimal("30"), "STORE"),  # no sale after 30 USD: CPA high
        (1.5, Decimal("6"), 10, Decimal("40"), "WINNER"),
        (0.6, Decimal("6"), 10, Decimal("40"), "NICHE"),
        (0.6, Decimal("25"), 10, Decimal("40"), "HOOK"),
        (1.5, Decimal("25"), 10, Decimal("40"), "STORE"),
        (0.9, Decimal("25"), 10, Decimal("40"), "HOOK"),  # grey zone: fix the hook first
        (None, Decimal("6"), 10, Decimal("40"), "NO_EVIDENCE"),  # no impressions, no CTR
    ],
)
def test_the_ctr_by_cpa_tree(ctr, cpa, days_alive, spend, expected):
    assert (
        verdict_key(
            ctr_pct=ctr,
            cpa=cpa,
            days_alive=days_alive,
            spend=spend,
            cpa_ceiling=CEILING,
            min_days=5,
        )
        == expected
    )


START = date(2026, 9, 1)
END = date(2026, 9, 10)


async def web_ad_with_sales(
    store,
    ad_id: str,
    *,
    sales: int,
    meta: int,
    clicks: int = 30,
    days: int = 10,
    first: date = START,
) -> int:
    v = await store.variant(f"Product {ad_id}", "40.00")
    await store.unit(v)
    await store.ad(ad_id, product=await store.product_of(v))
    await store.insights(ad_id, first, days, spend="10.00", impressions=2000, clicks=clicks, meta=0)
    for i in range(sales):
        when = local(START + timedelta(days=i % 10), "20:00")
        sale = await store.sale(when, channel="WEB", total="40.00")
        await store.order(when, sale=sale, ad_id=ad_id)
    # Meta's own count, all on the first day.
    if meta:
        await store.s.execute(
            text(
                "UPDATE store.ad_insight_daily SET meta_purchases = :m "
                "WHERE ad_id = :a AND day = :d"
            ),
            {"m": meta, "a": ad_id, "d": first},
        )
        await store.s.commit()
    return v


async def test_cpa_and_roas_come_from_our_sales_not_from_meta(session, store):
    await store.setting("cpa_ceiling", "12")
    await web_ad_with_sales(store, "A1", sales=20, meta=12)
    ad = (await ads_performance(session, START, END))["ads"][0]
    assert ad["spend"] == "100.00"
    assert ad["sales"] == 20
    assert ad["cpa"] == "5.00"
    assert ad["roas"] == 8.0
    assert ad["meta_purchases"] == 12
    assert ad["cpa_meta"] == "8.33"
    assert ad["meta_undercount_pct"] == 40.0
    assert ad["ctr_pct"] == 1.5
    assert ad["verdict"]["key"] == "WINNER"


async def test_the_learning_phase_looks_at_the_ads_life_not_the_window(session, store):
    await store.setting("cpa_ceiling", "12")
    await web_ad_with_sales(store, "OLD", sales=3, meta=0, days=40, first=date(2026, 8, 2))
    # Asking about 2 days does not make a 40-day-old ad "new" again.
    ad = (await ads_performance(session, date(2026, 9, 9), date(2026, 9, 10)))["ads"][0]
    assert ad["days_with_data"] == 2
    assert ad["days_alive"] == 40
    assert ad["verdict"]["key"] != "NO_EVIDENCE"


async def test_a_whatsapp_ad_gets_credit_for_the_chats_it_opened(session, store):
    await store.ad("WA1", destination="WHATSAPP")
    await store.insights("WA1", START, 10, spend="5.00", clicks=50)
    for i in range(4):
        when = local(START + timedelta(days=i), "15:00")
        chat = await store.conversation(
            when, from_ad="WA1", stage="CUSTOMER", events=("ORDER_CREATED", "ORDER_PAID")
        )
        sale = await store.sale(when, channel="WHATSAPP", total="30.00")
        await store.order(when, source="BOT", sale=sale, conversation=chat)
    ad = (await ads_performance(session, START, END))["ads"][0]
    assert ad["sales"] == 4
    assert ad["meta_purchases"] == 0
    assert any("never sees a sale closed in a chat" in w for w in ad["warnings"])


async def test_an_ad_spending_on_an_out_of_stock_product_is_called_out(session, store):
    v = await store.variant("Gone", "30.00")
    await store.ad("OOS", product=await store.product_of(v))
    await store.insights("OOS", START, 10)
    ad = (await ads_performance(session, START, END))["ads"][0]
    assert ad["product_in_stock"] is False
    assert any("OUT OF STOCK" in w for w in ad["warnings"])


async def test_mer_needs_no_attribution(session, store):
    await store.ad("A1")
    await store.insights("A1", START, 10, spend="10.00")
    await store.sale(local(START), channel="SELLER", total="500.00")  # not attributed
    totals = (await ads_performance(session, START, END))["totals"]
    assert totals["attributed_sales"] == 0
    assert totals["mer"] == 5.0


async def test_ads_without_spend_in_the_window_are_left_out(session, store):
    await store.ad("PAUSED", status="PAUSED")
    await store.insights("PAUSED", date(2026, 6, 1), 10)
    assert (await ads_performance(session, START, END))["ads"] == []
