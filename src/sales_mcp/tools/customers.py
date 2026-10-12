"""Repeat purchase, time between purchases and customer value (RFM), by how
each customer arrived.

THE ROWS NEVER CARRY A READABLE EMAIL. The tool hands over `email` and the
privacy layer turns it into `hash_email`: the same SHA-256 that travels to
Meta's Conversions API, so the list can be matched with custom audiences
without the store exposing anyone. Doing it in the privacy layer and not here
is what guarantees that a future tool cannot forget.
"""

import statistics
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sales_mcp.timeutil import day_range, money, pct
from sales_mcp.tools.params import DateFrom, DateTo, Limit500

SEGMENTS = ("NEW", "REPEAT", "DORMANT", "LOST")
DIRECTIONAL_BELOW = 200


def segment_of(recency_days: int, frequency: int) -> str:
    if recency_days > 180:
        return "LOST"
    if recency_days > 90:
        return "DORMANT"
    return "REPEAT" if frequency >= 2 else "NEW"


async def customer_segments(
    s: AsyncSession,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    limit: Limit500 = 50,
) -> dict[str, Any]:
    # A year by default: repeat purchase is invisible in a 30-day window.
    r = day_range(date_from, date_to, default_days=365)
    args = {"start": r.start, "end": r.end}

    rows = (
        await s.execute(
            text("""
        WITH purchases AS (
            SELECT s.customer_id, count(*) frequency, sum(s.total_amount) value,
                   min(s.sold_at) first_at, max(s.sold_at) last_at
            FROM store.sale s
            WHERE s.sold_at >= :start AND s.sold_at < :end
              AND s.is_internal IS NOT TRUE AND s.customer_id IS NOT NULL
            GROUP BY 1
        )
        SELECT c.id, c.email, c.lead_source, p.frequency, p.value, p.first_at, p.last_at
        FROM purchases p JOIN store.customer c ON c.id = p.customer_id
        ORDER BY p.value DESC, c.id
    """),
            args,
        )
    ).all()

    # Recency is measured from the END of the range, not from now, so a report
    # about last quarter reads the same today and next month.
    customers = []
    for row in rows:
        recency = (r.end - row.last_at).days
        between = (
            (row.last_at - row.first_at).days / (row.frequency - 1) if row.frequency > 1 else None
        )
        customers.append(
            {
                "email": row.email,  # leaves as hash_email
                "lead_source": row.lead_source,
                "purchases": row.frequency,
                "value": money(row.value),
                "first_purchase": row.first_at.astimezone(r.start.tzinfo).date().isoformat(),
                "last_purchase": row.last_at.astimezone(r.start.tzinfo).date().isoformat(),
                "recency_days": recency,
                "days_between_purchases": round(between, 1) if between is not None else None,
                "segment": segment_of(recency, row.frequency),
            }
        )

    segments = []
    for name in SEGMENTS:
        group = [c for c in customers if c["segment"] == name]
        segments.append(
            {
                "segment": name,
                "customers": len(group),
                "revenue": money(sum((Decimal(c["value"]) for c in group), Decimal(0))),
            }
        )

    by_lead_source: dict[str, int] = {}
    for c in customers:
        by_lead_source[c["lead_source"]] = by_lead_source.get(c["lead_source"], 0) + 1

    repeaters = sum(1 for c in customers if c["purchases"] >= 2)
    gaps = [
        c["days_between_purchases"] for c in customers if c["days_between_purchases"] is not None
    ]

    return {
        "range": r.as_dict(),
        "customers": len(customers),
        "repeat_rate_pct": pct(repeaters, len(customers)),
        "median_days_between_purchases": statistics.median(gaps) if gaps else None,
        "ltv_90_days": await _ltv(s, r.end, 90),
        "ltv_180_days": await _ltv(s, r.end, 180),
        "segments": segments,
        # A dict of PEOPLE counts: groups under 5 leave as "< 5".
        "by_lead_source": by_lead_source,
        "returned": len(customers[:limit]),
        "rows": customers[:limit],
        "warning": (
            f"Only {len(customers)} customers: read this as directional, not statistical."
            if len(customers) < DIRECTIONAL_BELOW
            else None
        ),
        "notes": [
            "Segments: LOST > 180 days since the last purchase, DORMANT > 90, "
            "REPEAT = 2+ purchases, otherwise NEW.",
            "hash_email is the SHA-256 Meta expects, ready to match custom audiences.",
        ],
    }


async def _ltv(s: AsyncSession, until: datetime, days: int) -> dict[str, Any]:
    """Average revenue per customer in their first N days since their FIRST
    purchase. Only customers who have had N full days to get there count;
    otherwise the number comes out artificially low."""
    row = (
        await s.execute(
            text("""
        WITH firsts AS (
            SELECT customer_id, min(sold_at) t0 FROM store.sale
            WHERE is_internal IS NOT TRUE AND customer_id IS NOT NULL
            GROUP BY 1
            HAVING min(sold_at) < :cutoff
        )
        SELECT count(DISTINCT f.customer_id) n, coalesce(sum(s.total_amount), 0) total
        FROM firsts f
        JOIN store.sale s ON s.customer_id = f.customer_id
         AND s.sold_at < f.t0 + make_interval(days => :days)
        WHERE s.is_internal IS NOT TRUE
    """),
            {"cutoff": until - timedelta(days=days), "days": days},
        )
    ).one()
    return {
        "customers_counted": row.n,
        "average_revenue": money(row.total / row.n) if row.n else None,
    }
