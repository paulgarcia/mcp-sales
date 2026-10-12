from datetime import UTC, date, datetime

import pytest

from sales_mcp.timeutil import change_pct, day_range, money, pct


def test_a_local_day_is_not_a_utc_day():
    r = day_range(date(2026, 9, 8), date(2026, 9, 8))
    # Quito is UTC-5: the local 8th runs from 05:00 UTC on the 8th to 05:00 UTC on the 9th.
    assert r.start.astimezone(UTC) == datetime(2026, 9, 8, 5, tzinfo=UTC)
    assert r.end.astimezone(UTC) == datetime(2026, 9, 9, 5, tzinfo=UTC)


def test_default_range_is_thirty_days_ending_today():
    r = day_range(None, date(2026, 9, 30))
    assert (r.first, r.last, r.days) == (date(2026, 9, 1), date(2026, 9, 30), 30)


def test_previous_period_has_the_same_length_and_touches_this_one():
    prev = day_range(date(2026, 9, 8), date(2026, 9, 14)).previous()
    assert (prev.first, prev.last) == (date(2026, 9, 1), date(2026, 9, 7))


def test_a_reversed_range_is_an_error_not_an_empty_answer():
    with pytest.raises(ValueError, match="after"):
        day_range(date(2026, 9, 10), date(2026, 9, 1))


def test_money_keeps_cents_as_a_string():
    assert money("10") == "10.00"
    assert money(0.1 + 0.2) == "0.30"
    assert money(None) is None


def test_without_a_base_there_is_no_percentage():
    assert pct(3, 0) is None
    assert change_pct(10, 0) is None
    assert change_pct(15, 10) == 50.0
    assert pct(1, 3) == 33.3
