"""How much was sold, and how that compares.

It exists because the most basic question, "how much did we sell yesterday?",
could not be asked. It had to be pieced together from per-product rows, which
silently stop at the row limit and cannot be split by channel, and the channel
is exactly the split that matters:

  * Only WEB sales send a Purchase event to Meta. Most volume comes through
    sellers and never reaches the ads platform, by design. Comparing the store
    total against Ads Manager without splitting by channel gives numbers that
    never reconcile, and the conclusion comes out backwards.
  * Revenue and money collected are different. `revenue` is what was sold in
    the range; `collected_in_range` is the money that came in during those
    days, from a sale of any date. A credit sale shows up whole in the first
    and in installments in the second.
  * Shipping is reported apart. Confusing it with revenue was expensive once:
    seller commissions were computed on totals that included shipping.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sales_mcp.timeutil import DayRange, change_pct, day_range, money
from sales_mcp.tools.params import DateFrom, DateTo

LIVE = "s.is_internal IS NOT TRUE"


async def _totals(s: AsyncSession, r: DayRange) -> Any:
    return (
        await s.execute(
            text(f"""
        SELECT count(*)                          AS sales,
               coalesce(sum(s.total_amount), 0)   AS revenue,
               coalesce(sum(s.shipping_fee), 0)   AS shipping,
               coalesce(sum(s.discount_total), 0) AS discounts,
               count(DISTINCT s.customer_id)      AS customers
        FROM store.sale s
        WHERE s.sold_at >= :start AND s.sold_at < :end AND {LIVE}
    """),
            {"start": r.start, "end": r.end},
        )
    ).one()


async def sales_summary(
    s: AsyncSession,
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> dict[str, Any]:
    r = day_range(date_from, date_to)
    args = {"start": r.start, "end": r.end, "tz": str(r.start.tzinfo)}

    tot = await _totals(s, r)
    units = (
        await s.execute(
            text(f"""
        SELECT count(*) FROM store.sale_line sl
        JOIN store.sale s ON s.id = sl.sale_id
        WHERE s.sold_at >= :start AND s.sold_at < :end AND {LIVE}
    """),
            args,
        )
    ).scalar_one()

    by_channel = [
        {
            "channel": row.channel,
            "sales": row.n,
            "revenue": money(row.revenue),
            "sends_purchase_to_meta": row.channel == "WEB",
        }
        for row in (
            await s.execute(
                text(f"""
            SELECT s.channel, count(*) n, sum(s.total_amount) revenue
            FROM store.sale s
            WHERE s.sold_at >= :start AND s.sold_at < :end AND {LIVE}
            GROUP BY 1 ORDER BY 3 DESC
        """),
                args,
            )
        ).all()
    ]

    # Grouped by LOCAL day. Without AT TIME ZONE, a sale at 20:00 in Quito
    # lands on the next day and the whole series shifts.
    by_day = [
        {"day": row.day.isoformat(), "sales": row.n, "revenue": money(row.revenue)}
        for row in (
            await s.execute(
                text(f"""
            SELECT (s.sold_at AT TIME ZONE :tz)::date AS day, count(*) n,
                   sum(s.total_amount) revenue
            FROM store.sale s
            WHERE s.sold_at >= :start AND s.sold_at < :end AND {LIVE}
            GROUP BY 1 ORDER BY 1
        """),
                args,
            )
        ).all()
    ]

    by_seller = [
        {"seller": row.seller or "unassigned", "sales": row.n, "revenue": money(row.revenue)}
        for row in (
            await s.execute(
                text(f"""
            SELECT s.seller_username AS seller, count(*) n, sum(s.total_amount) revenue
            FROM store.sale s
            WHERE s.sold_at >= :start AND s.sold_at < :end AND {LIVE}
            GROUP BY 1 ORDER BY 3 DESC
        """),
                args,
            )
        ).all()
    ]

    # paid_at is a calendar day, so it is compared with days, not instants.
    days = {"first": r.first, "last": r.last}
    collected = (
        await s.execute(
            text(f"""
        SELECT coalesce(sum(p.amount), 0) amount, count(*) n
        FROM store.sale_payment p JOIN store.sale s ON s.id = p.sale_id
        WHERE p.paid_at BETWEEN :first AND :last AND {LIVE}
    """),
            days,
        )
    ).one()
    by_method = [
        {"method": row.method, "payments": row.n, "amount": money(row.amount)}
        for row in (
            await s.execute(
                text(f"""
            SELECT p.method, count(*) n, sum(p.amount) amount
            FROM store.sale_payment p JOIN store.sale s ON s.id = p.sale_id
            WHERE p.paid_at BETWEEN :first AND :last AND {LIVE}
            GROUP BY 1 ORDER BY 3 DESC
        """),
                days,
            )
        ).all()
    ]

    # A total on its own says nothing: $500 only means something next to
    # last week's $300. Compare with the range of the same length right before.
    prev_range = r.previous()
    prev = await _totals(s, prev_range)

    return {
        "range": r.as_dict(),
        "totals": {
            "sales": tot.sales,
            "units": units,
            "distinct_customers": tot.customers,
            "revenue": money(tot.revenue),
            "shipping_included_in_revenue": money(tot.shipping),
            "revenue_without_shipping": money(tot.revenue - tot.shipping),
            "discounts": money(tot.discounts),
            "average_ticket": money(tot.revenue / tot.sales) if tot.sales else None,
        },
        "collected_in_range": {
            "amount": money(collected.amount),
            "payments": collected.n,
            "by_method": by_method,
            "note": "Money that CAME IN during these days, from sales of any date. "
            "Not the same as revenue.",
        },
        "by_channel": by_channel,
        "by_day": by_day,
        "by_seller": by_seller,
        "previous_period": {
            "range": prev_range.as_dict(),
            "sales": prev.sales,
            "revenue": money(prev.revenue),
            "revenue_change_pct": change_pct(tot.revenue, prev.revenue),
            "sales_change_pct": change_pct(tot.sales, prev.sales),
        },
        "notes": [
            "Only WEB sales send a Purchase event to Meta. Compare Ads Manager with the WEB "
            "channel, never with the store total.",
            "A change_pct of null means the previous period had nothing to compare with; "
            "it is not 0 %.",
        ],
    }
