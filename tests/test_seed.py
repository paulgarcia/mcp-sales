"""The demo data is part of the product: a recorded demo must say the same
thing every time, and the stories it was built to tell must stay true."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from sales_mcp import seed

TZ = ZoneInfo("America/Guayaquil")
ANCHOR = date(2026, 10, 2)


@pytest.fixture(scope="module")
def rows():
    return seed.generate(ANCHOR, TZ)


def test_same_seed_same_data(rows):
    assert seed.generate(ANCHOR, TZ) == rows


def test_nothing_happens_after_the_cutoff(rows):
    cutoff = datetime.combine(ANCHOR, time(12), tzinfo=TZ)
    assert max(r[1] for r in rows["sale"]) <= cutoff
    assert max(r[1] for r in rows["web_event"]) <= cutoff


def test_every_sold_unit_existed_and_was_received_before_it_sold(rows):
    units = {r[0]: r for r in rows["inventory_unit"]}
    sales = {r[0]: r for r in rows["sale"]}
    for _line, sale_id, unit_id, _price in rows["sale_line"]:
        assert units[unit_id][2] == "SOLD"
        assert units[unit_id][5] < sales[sale_id][1]


def test_the_window_shopping_perfume_never_sells(rows):
    perfume = next(
        r[0]
        for r in rows["product_variant"]
        if r[1] == next(p[0] for p in rows["product"] if p[2] == seed.WINDOW_SHOPPING)
    )
    sold_units = {r[2] for r in rows["sale_line"]}
    assert not any(u[1] == perfume and u[0] in sold_units for u in rows["inventory_unit"])


def test_people_are_fake_on_purpose(rows):
    for _id, _name, email, phone, *_ in rows["customer"]:
        assert email is None or email.endswith("@example.com")
        assert phone.startswith("+593 00")  # not a valid Ecuadorian number
