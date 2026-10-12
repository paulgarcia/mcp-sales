from datetime import date

from conftest import local

from sales_mcp.tools.sales import sales_summary

D = date(2026, 9, 8)


async def test_the_total_is_what_the_database_holds(session, store):
    await store.sale(local(D, "10:00"), total="20.00")
    await store.sale(local(D, "11:00"), total="35.50")
    out = await sales_summary(session, D, D)
    assert out["totals"]["sales"] == 2
    assert out["totals"]["revenue"] == "55.50"
    assert out["totals"]["average_ticket"] == "27.75"


async def test_internal_test_sales_do_not_count(session, store):
    await store.sale(local(D), total="20.00")
    await store.sale(local(D), total="999.00", internal=True)
    out = await sales_summary(session, D, D)
    assert out["totals"]["revenue"] == "20.00"


async def test_the_channel_split_says_what_meta_can_see(session, store):
    await store.sale(local(D), channel="WEB", total="30.00")
    await store.sale(local(D), channel="SELLER", total="50.00")
    out = await sales_summary(session, D, D)
    channels = {c["channel"]: c for c in out["by_channel"]}
    assert channels["WEB"]["sends_purchase_to_meta"] is True
    assert channels["SELLER"]["sends_purchase_to_meta"] is False
    assert channels["SELLER"]["revenue"] == "50.00"


async def test_shipping_is_reported_apart_from_revenue(session, store):
    await store.sale(local(D), channel="WEB", total="23.50", shipping="3.50")
    totals = (await sales_summary(session, D, D))["totals"]
    assert totals["revenue"] == "23.50"
    assert totals["shipping_included_in_revenue"] == "3.50"
    assert totals["revenue_without_shipping"] == "20.00"


async def test_sold_and_collected_are_different_numbers(session, store):
    # A credit sale: half paid today, half in two weeks.
    await store.sale(
        local(D),
        total="100.00",
        paid=[(D, "50.00", "TRANSFER"), (date(2026, 9, 22), "50.00", "TRANSFER")],
    )
    out = await sales_summary(session, D, D)
    assert out["totals"]["revenue"] == "100.00"
    assert out["collected_in_range"]["amount"] == "50.00"


async def test_the_daily_series_uses_the_local_day(session, store):
    # 20:30 in Quito is already 01:30 of the NEXT day in UTC.
    await store.sale(local(D, "20:30"), total="20.00")
    out = await sales_summary(session, D, date(2026, 9, 9))
    assert out["by_day"] == [{"day": "2026-09-08", "sales": 1, "revenue": "20.00"}]


async def test_compares_with_the_previous_period_of_the_same_length(session, store):
    await store.sale(local(date(2026, 9, 1)), total="100.00")  # previous week
    await store.sale(local(date(2026, 9, 9)), total="150.00")  # this week
    out = await sales_summary(session, date(2026, 9, 8), date(2026, 9, 14))
    prev = out["previous_period"]
    assert prev["range"]["from"] == "2026-09-01"
    assert prev["revenue"] == "100.00"
    assert prev["revenue_change_pct"] == 50.0


async def test_without_a_base_the_change_is_null_not_zero(session, store):
    await store.sale(local(D), total="20.00")
    prev = (await sales_summary(session, D, D))["previous_period"]
    assert prev["revenue_change_pct"] is None


async def test_an_empty_range_does_not_crash(session):
    out = await sales_summary(session, D, D)
    assert out["totals"]["sales"] == 0
    assert out["totals"]["average_ticket"] is None
    assert out["by_day"] == []


async def test_sales_without_a_seller_do_not_disappear(session, store):
    await store.seller("seller_01", "0.20")
    await store.sale(local(D), total="10.00", seller="seller_01")
    await store.sale(local(D), channel="WEB", total="30.00")
    sellers = {r["seller"]: r["revenue"] for r in (await sales_summary(session, D, D))["by_seller"]}
    assert sellers == {"unassigned": "30.00", "seller_01": "10.00"}
