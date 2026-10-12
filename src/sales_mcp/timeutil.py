"""Local days and money, the two things every tool gets wrong once.

Days are LOCAL. Asking for "September 8" and getting 19:00 of the 7th to 19:00
of the 8th (the UTC day) is the kind of error that makes people distrust every
other number, so ranges are built from local midnights and converted to UTC
instants only at the edge.

Money travels as a string with two decimals. A float in JSON loses cents.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sales_mcp.config import get_settings

CENT = Decimal("0.01")


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().business_tz)


def today() -> date:
    return datetime.now(tz()).date()


@dataclass(frozen=True)
class DayRange:
    """Whole local days, from `first` to `last` inclusive."""

    first: date
    last: date

    @property
    def start(self) -> datetime:
        """Local midnight that opens the range, as an aware datetime."""
        return datetime.combine(self.first, time.min, tzinfo=tz())

    @property
    def end(self) -> datetime:
        """Local midnight AFTER the last day (exclusive bound)."""
        return datetime.combine(self.last + timedelta(days=1), time.min, tzinfo=tz())

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1

    def previous(self) -> "DayRange":
        """The range of the same length that ends right before this one."""
        return DayRange(self.first - timedelta(days=self.days), self.first - timedelta(days=1))

    def as_dict(self) -> dict[str, str]:
        return {
            "from": self.first.isoformat(),
            "to": self.last.isoformat(),
            "tz": get_settings().business_tz,
        }


def day_range(date_from: date | None, date_to: date | None, default_days: int = 30) -> DayRange:
    """The requested range, or the last `default_days` local days ending today.

    A reversed range is a caller mistake, and saying so beats returning an
    empty answer that reads like "no sales".
    """
    last = date_to or today()
    first = date_from or last - timedelta(days=default_days - 1)
    if first > last:
        raise ValueError(f"date_from ({first}) is after date_to ({last}).")
    return DayRange(first, last)


def money(value: Decimal | int | float | str | None) -> str | None:
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))


def pct(part: Decimal | int | float, whole: Decimal | int | float, digits: int = 1) -> float | None:
    """`part / whole` as a percentage, or None when there is no base.

    None and not 0: without a base there is no rate to report, and a 0 reads
    as "nothing happened", which is the opposite.
    """
    whole = Decimal(str(whole or 0))
    if whole == 0:
        return None
    return float(round(Decimal(str(part or 0)) / whole * 100, digits))


def change_pct(now: Decimal | int, before: Decimal | int) -> float | None:
    """Percentage change against a previous value. None when there was no base."""
    before = Decimal(str(before or 0))
    if before == 0:
        return None
    return float(round((Decimal(str(now or 0)) - before) / before * 100, 1))


def local_iso(instant: datetime | None) -> str | None:
    return instant.astimezone(tz()).isoformat() if instant else None
