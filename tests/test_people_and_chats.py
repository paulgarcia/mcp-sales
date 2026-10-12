from datetime import date, timedelta

from conftest import local

from sales_mcp.privacy import redact, sha256_hex
from sales_mcp.tools.customers import customer_segments, segment_of
from sales_mcp.tools.orders import online_orders
from sales_mcp.tools.whatsapp import whatsapp_funnel

D = date(2026, 9, 8)


def test_segments():
    assert segment_of(200, 5) == "LOST"
    assert segment_of(120, 5) == "DORMANT"
    assert segment_of(10, 2) == "REPEAT"
    assert segment_of(10, 1) == "NEW"


async def test_rfm_counts_repeat_purchase_and_value(session, store):
    ana = await store.customer("Ana Pérez", "ana@example.com")
    luis = await store.customer("Luis Vera", "luis@example.com")
    await store.sale(local(D - timedelta(days=20)), total="30.00", customer=ana)
    await store.sale(local(D), total="50.00", customer=ana)
    await store.sale(local(D), total="10.00", customer=luis)
    out = await customer_segments(session, D - timedelta(days=60), D)
    assert out["customers"] == 2
    assert out["repeat_rate_pct"] == 50.0
    top = out["rows"][0]
    assert (top["purchases"], top["value"], top["days_between_purchases"]) == (2, "80.00", 20.0)
    assert top["segment"] == "REPEAT"


async def test_rfm_never_returns_a_readable_email(session, store):
    ana = await store.customer(email="Ana@Example.com")
    await store.sale(local(D), total="30.00", customer=ana)
    row = redact(await customer_segments(session, D, D))["rows"][0]
    assert "email" not in row
    assert row["hash_email"] == sha256_hex("ana@example.com", "email")


async def test_rfm_warns_that_few_customers_are_directional(session, store):
    ana = await store.customer()
    await store.sale(local(D), total="30.00", customer=ana)
    assert "directional" in (await customer_segments(session, D, D))["warning"]


async def test_rfm_with_no_purchases_does_not_crash(session):
    out = await customer_segments(session, D, D)
    assert out["customers"] == 0
    assert out["repeat_rate_pct"] is None


async def test_orders_say_whether_they_became_a_sale(session, store):
    ana = await store.customer("Ana María Pérez")
    sale = await store.sale(local(D), channel="WEB", total="40.00", customer=ana)
    await store.order(local(D, "09:00"), sale=sale, customer=ana, ad_id="AD1")
    await store.order(local(D, "10:00"), status="EXPIRED", customer=ana)
    out = redact(await online_orders(session, D, D))
    assert out["summary"]["orders"] == 2
    assert out["summary"]["paid_pct"] == 50.0
    newest, oldest = out["items"]
    assert newest["converted_to_sale"] is False
    assert oldest["converted_to_sale"] is True
    assert oldest["attribution"]["ad_id"] == "AD1"
    assert oldest["customer_name"] == "A. M. P."
    assert "delivery_address" not in oldest


async def test_order_counts_by_city_hide_small_groups(session, store):
    for _ in range(6):
        await store.order(local(D), city="Quito", status="EXPIRED")
    await store.order(local(D), city="Puyo", status="EXPIRED")
    by_city = redact(await online_orders(session, D, D))["summary"]["by_city"]
    assert by_city == {"Quito": 6, "Puyo": "< 5"}


async def test_whatsapp_counts_conversations_and_how_far_they_got(session, store):
    await store.conversation(
        local(D),
        events=("BAG_ADDED", "CHECKOUT_STARTED", "ORDER_CREATED", "ORDER_PAID"),
        stage="CUSTOMER",
    )
    await store.conversation(local(D), events=("BAG_ADDED",), stage="CART", escalated=True)
    await store.conversation(local(D), stage="ENGAGED")
    out = await whatsapp_funnel(session, D, D)
    f = out["funnel"]
    assert (f["conversations"], f["added_to_bag"], f["created_order"], f["paid"]) == (3, 2, 1, 1)
    assert f["paid_pct"] == 33.3
    assert out["outcomes"]["handed_to_human"] == 1


async def test_whatsapp_bot_sales_come_from_bot_orders_only(session, store):
    chat = await store.conversation(local(D), stage="CUSTOMER", events=("ORDER_PAID",))
    sale = await store.sale(
        local(D), channel="WHATSAPP", total="25.00", paid=[(D, "25.00", "CARD")]
    )
    await store.order(local(D), source="BOT", sale=sale, conversation=chat)
    web = await store.sale(local(D), channel="WEB", total="99.00")
    await store.order(local(D), sale=web)
    bot = (await whatsapp_funnel(session, D, D))["bot_sales"]
    assert (bot["sales"], bot["revenue"]) == (1, "25.00")
    assert bot["by_method"] == [{"method": "CARD", "payments": 1, "amount": "25.00"}]
