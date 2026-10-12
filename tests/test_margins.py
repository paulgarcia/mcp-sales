from datetime import date

from conftest import local

from sales_mcp.privacy import redact
from sales_mcp.tools.margins import margins

D = date(2026, 9, 8)


async def test_margin_uses_the_real_cost_of_the_unit_sold(session, store):
    v = await store.variant("Serum", "20.00")
    # Two shipments, two costs. An average would hide which one sold.
    await store.sale(local(D), total="20.00", lines=[(v, "20.00", "8.00", "1.00")])
    await store.sale(local(D), total="20.00", lines=[(v, "20.00", "10.00", "1.50")])
    totals = (await margins(session, D, D))["totals"]
    assert totals["landed_cost"] == "20.50"
    assert totals["gross_margin"] == "19.50"
    assert totals["gross_margin_pct"] == 48.8


async def test_a_unit_not_costed_yet_does_not_break_the_cost(session, store):
    v = await store.variant()
    await store.sale(local(D), total="20.00", lines=[(v, "20.00", "8.00", None)])
    assert (await margins(session, D, D))["totals"]["landed_cost"] == "8.00"


async def test_internal_sales_do_not_count(session, store):
    v = await store.variant()
    await store.sale(local(D), total="20.00", lines=[(v, "20.00", "8.00", "1.00")], internal=True)
    assert (await margins(session, D, D))["totals"]["units"] == 0


async def test_gross_and_contribution_are_different_numbers(session, store):
    await store.setting("packing_cost_per_sale", "1.00")
    await store.seller("seller_01", "0.2000")
    v = await store.variant("Serum", "60.00")
    await store.sale(
        local(D),
        total="60.00",
        seller="seller_01",
        lines=[(v, "60.00", "20.00", "4.00")],
        fee="2.00",
    )
    out = await margins(session, D, D)
    assert out["totals"]["gross_margin"] == "36.00"
    c = out["contribution"]
    # The regression this test exists for: 20 % of $60 is $12, not $0.12.
    assert c["seller_commissions"] == "12.00"
    assert c["card_fees"] == "2.00"
    assert c["packing"] == "1.00"
    assert c["contribution"] == "21.00"  # 60 - 24 - 12 - 2 - 1
    assert c["contribution_pct"] == 35.0
    assert c["break_even_roas"] == 2.86  # 1 / 0.35


async def test_shipping_is_not_counted_as_contribution(session, store):
    v = await store.variant("Serum", "20.00")
    await store.sale(
        local(D),
        channel="WEB",
        total="23.50",
        shipping="3.50",
        lines=[(v, "20.00", "8.00", "1.00")],
    )
    c = (await margins(session, D, D))["contribution"]
    assert c["revenue_without_shipping"] == "20.00"


async def test_breakdowns_are_lists_and_survive_k_anonymity(session, store):
    v = await store.variant("Serum", "20.00", brand="Petal Lab")
    for _ in range(3):
        await store.sale(local(D), total="20.00", lines=[(v, "20.00", "8.00", "1.00")])
    out = redact(await margins(session, D, D))
    assert out["products"][0]["units"] == 3  # not "< 5": units are not people
    assert out["brands"][0] == {**out["brands"][0], "brand": "Petal Lab", "units": 3}


async def test_a_brand_filter_hides_the_store_wide_contribution(session, store):
    v = await store.variant("Serum", "20.00", brand="Petal Lab")
    await store.sale(local(D), total="20.00", lines=[(v, "20.00", "8.00", "1.00")])
    out = await margins(session, D, D, brand="petal")
    assert out["totals"]["units"] == 1
    assert out["contribution"] is None
