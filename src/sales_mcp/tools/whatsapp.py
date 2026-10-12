"""The WhatsApp bot: new conversations, how far they get, and what they end in."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sales_mcp.timeutil import day_range, money, pct
from sales_mcp.tools.params import DateFrom, DateTo


async def whatsapp_funnel(
    s: AsyncSession,
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> dict[str, Any]:
    r = day_range(date_from, date_to)
    args = {"start": r.start, "end": r.end, "tz": str(r.start.tzinfo)}

    new = (
        await s.execute(
            text("""
        SELECT count(*) n,
               count(*) FILTER (WHERE from_ad_id IS NOT NULL) from_ads,
               count(*) FILTER (WHERE escalated_at IS NOT NULL) handed_to_human
        FROM store.conversation WHERE created_at >= :start AND created_at < :end
    """),
            args,
        )
    ).one()

    by_week = [
        {"week_of": row.week.isoformat(), "conversations": row.n}
        for row in (
            await s.execute(
                text("""
            SELECT date_trunc('week', created_at AT TIME ZONE :tz)::date week, count(*) n
            FROM store.conversation
            WHERE created_at >= :start AND created_at < :end
            GROUP BY 1 ORDER BY 1
        """),
                args,
            )
        ).all()
    ]

    by_stage = {
        row.lead_stage: row.n
        for row in (
            await s.execute(
                text("""
        SELECT lead_stage, count(*) n FROM store.conversation
        WHERE created_at >= :start AND created_at < :end
        GROUP BY 1 ORDER BY 2 DESC, 1
    """),
                args,
            )
        ).all()
    }

    # Distinct conversations that reached each step, for the conversations
    # that STARTED in the range: a cohort, so the steps can be compared.
    steps = (
        await s.execute(
            text("""
        SELECT
          count(DISTINCT e.conversation_id) FILTER (WHERE e.event_type = 'BAG_ADDED') bag,
          count(DISTINCT e.conversation_id) FILTER (WHERE e.event_type = 'CHECKOUT_STARTED')
              checkout,
          count(DISTINCT e.conversation_id) FILTER (WHERE e.event_type = 'ORDER_CREATED')
              ordered,
          count(DISTINCT e.conversation_id) FILTER (WHERE e.event_type = 'ORDER_PAID') paid
        FROM store.conversation_event e
        JOIN store.conversation c ON c.id = e.conversation_id
        WHERE c.created_at >= :start AND c.created_at < :end
    """),
            args,
        )
    ).one()

    sales = (
        await s.execute(
            text("""
        SELECT count(*) n, coalesce(sum(s.total_amount), 0) revenue
        FROM store.shop_order o JOIN store.sale s ON s.id = o.sale_id
        WHERE o.source = 'BOT' AND s.sold_at >= :start AND s.sold_at < :end
          AND s.is_internal IS NOT TRUE
    """),
            args,
        )
    ).one()
    by_method = [
        {"method": row.method, "payments": row.n, "amount": money(row.amount)}
        for row in (
            await s.execute(
                text("""
            SELECT p.method, count(*) n, sum(p.amount) amount
            FROM store.shop_order o
            JOIN store.sale s ON s.id = o.sale_id
            JOIN store.sale_payment p ON p.sale_id = s.id
            WHERE o.source = 'BOT' AND s.sold_at >= :start AND s.sold_at < :end
              AND s.is_internal IS NOT TRUE
            GROUP BY 1 ORDER BY 3 DESC
        """),
                args,
            )
        ).all()
    ]

    return {
        "range": r.as_dict(),
        "new_conversations": new.n,
        "from_ads": new.from_ads,
        "by_week": by_week,
        # Counts of people: groups under 5 leave as "< 5".
        "by_stage": by_stage,
        "funnel": {
            "conversations": new.n,
            "added_to_bag": steps.bag,
            "started_checkout": steps.checkout,
            "created_order": steps.ordered,
            "paid": steps.paid,
            "paid_pct": pct(steps.paid, new.n),
            "note": "A cohort: the conversations that started in the range, and how far "
            "each one got.",
        },
        "bot_sales": {
            "sales": sales.n,
            "revenue": money(sales.revenue),
            "average_ticket": money(sales.revenue / sales.n) if sales.n else None,
            "by_method": by_method,
            "note": "Sales closed in the range, whenever their chat started.",
        },
        "outcomes": {
            "ended_in_order": steps.ordered,
            "handed_to_human": new.handed_to_human,
            "note": "Not exclusive and they do not add up to the total: a chat that ended in "
            "an order may also have passed through a person.",
        },
    }
