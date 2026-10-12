from datetime import datetime, timedelta

from sales_mcp.timeutil import today, tz
from sales_mcp.tools.health import data_health


async def fresh_store(store) -> None:
    now = datetime.now(tz())
    await store.sale(now - timedelta(hours=2), channel="WEB", total="20.00")
    v = await store.variant()
    await store.event(now - timedelta(hours=1), "VIEW_PRODUCT", v)
    await store.conversation(now - timedelta(hours=3))


async def test_all_fresh_and_attributed_is_ok(session, store):
    await fresh_store(store)
    now = datetime.now(tz())
    await store.ad("A1")
    await store.insights("A1", today() - timedelta(days=1), 1)
    sale = await store.sale(now - timedelta(hours=5), channel="WEB", total="20.00")
    await store.order(now - timedelta(hours=5), sale=sale, ad_id="A1")
    out = await data_health(session)
    assert out["status"] == "OK", out
    assert out["attribution"]["broken"] is False


async def test_a_stale_ads_sync_is_reported(session, store):
    await fresh_store(store)
    await store.order(datetime.now(tz()), status="EXPIRED")
    await store.ad("A1")
    await store.insights("A1", today() - timedelta(days=6), 1)
    out = await data_health(session)
    ads = next(s for s in out["sources"] if s["source"] == "meta_ads")
    assert ads["status"] == "STALE"
    assert out["status"] == "CHECK"


async def test_spend_without_a_single_attributed_order_means_broken_web_lane(session, store):
    await fresh_store(store)
    now = datetime.now(tz())
    await store.ad("A1")
    await store.insights("A1", today() - timedelta(days=2), 2)
    sale = await store.sale(now - timedelta(hours=5), channel="WEB", total="20.00")
    await store.order(now - timedelta(hours=5), sale=sale, ad_id=None)
    att = (await data_health(session))["attribution"]
    assert att["broken_lane"] == "WEB"
    assert "utm_content" in att["remedy"]


async def test_a_whatsapp_lane_break_points_at_the_bot_not_at_url_parameters(session, store):
    await fresh_store(store)
    await store.order(datetime.now(tz()), status="EXPIRED")
    await store.ad("WA1", destination="WHATSAPP")
    await store.insights("WA1", today() - timedelta(days=2), 2)
    att = (await data_health(session))["attribution"]
    assert att["broken_lane"] == "WHATSAPP"
    assert "webhook" in att["remedy"]


async def test_an_empty_database_says_so(session):
    out = await data_health(session)
    assert {s["status"] for s in out["sources"]} == {"EMPTY"}
    assert out["status"] == "CHECK"
