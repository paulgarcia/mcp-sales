from datetime import date, timedelta

from conftest import local

from sales_mcp.timeutil import today
from sales_mcp.tools.products import product_performance
from sales_mcp.tools.stock import stock_status


async def sell(store, variant: int, n: int, days_ago: int = 3) -> None:
    for _ in range(n):
        await store.sale(
            local(today() - timedelta(days=days_ago)),
            total="20.00",
            lines=[(variant, "20.00", "8.00", "1.00")],
        )


async def test_days_of_cover_flags_what_is_running_low(session, store):
    v = await store.variant("Serum")
    await sell(store, v, 30)  # 1 unit a day
    for _ in range(5):
        await store.unit(v)
    item = (await stock_status(session))["items"][0]
    assert item["units_on_hand"] == 5
    assert item["days_of_cover"] == 5.0
    assert item["running_low"] is True
    assert item["out_of_stock"] is False


async def test_in_transit_and_reserved_are_not_on_hand(session, store):
    v = await store.variant()
    await store.unit(v, "IN_TRANSIT")
    await store.unit(v, "RESERVED")
    item = (await stock_status(session))["items"][0]
    assert (item["units_on_hand"], item["units_in_transit"], item["units_reserved"]) == (0, 1, 1)
    assert item["out_of_stock"] is True


async def test_an_ad_spending_on_an_empty_shelf_is_flagged(session, store):
    v = await store.variant("Gel Cream")
    await store.ad("A1", product=await store.product_of(v))
    await store.insights("A1", today() - timedelta(days=3), 2)
    item = (await stock_status(session))["items"][0]
    assert item["advertised_while_out_of_stock"] is True
    assert item["ad_spend_last_7d"] == "20.00"


async def test_stock_that_never_sells_is_dead_stock(session, store):
    v = await store.variant("Perfume", "119.00")
    for _ in range(8):
        await store.unit(v)
    assert (await stock_status(session))["items"][0]["dead_stock"] is True


async def test_filtering_does_not_hide_how_much_was_checked(session, store):
    # The trap: with only_problems=true, "0 of 0" reads as "no catalog"
    # instead of "no problems", the opposite reading.
    for name in ("A", "B", "C"):
        v = await store.variant(name)
        await sell(store, v, 3)
        for _ in range(50):
            await store.unit(v)
    out = await stock_status(session, only_problems=True)
    assert (out["checked"], out["with_problems"], out["returned"]) == (3, 0, 0)


async def test_window_shopping_is_many_views_and_no_sales(session, store):
    D = date(2026, 9, 8)
    perfume = await store.variant("Perfume", "119.00")
    serum = await store.variant("Serum", "20.00")
    for _ in range(25):
        await store.event(local(D), "VIEW_PRODUCT", perfume)
    for _ in range(25):
        await store.event(local(D), "VIEW_PRODUCT", serum)
    await store.sale(local(D), total="20.00", lines=[(serum, "20.00", "8.00", "1.00")])
    items = {
        i["product"]: i
        for i in (await product_performance(session, D, D, order_by="views"))["items"]
    }
    assert items["Perfume"]["window_shopping"] is True
    assert items["Serum"]["window_shopping"] is False
    assert items["Serum"]["conversion"] == 0.04


async def test_staff_clicks_are_not_customer_interest(session, store):
    D = date(2026, 9, 8)
    v = await store.variant()
    await store.event(local(D), "VIEW_PRODUCT", v, internal=True)
    assert (await product_performance(session, D, D))["items"] == []
