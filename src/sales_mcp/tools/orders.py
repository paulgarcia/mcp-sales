"""Online orders (web catalog and WhatsApp bot), with their attribution."""

from typing import Annotated, Any, Literal

from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sales_mcp.timeutil import day_range, local_iso, money, pct
from sales_mcp.tools.params import DateFrom, DateTo, IncludeInternal, Limit200

Source = Annotated[
    Literal["ALL", "WEB", "BOT"],
    Field(description="WEB = catalog checkout, BOT = WhatsApp bot. Default ALL."),
]


async def online_orders(
    s: AsyncSession,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    source: Source = "ALL",
    limit: Limit200 = 25,
    include_internal: IncludeInternal = False,
) -> dict[str, Any]:
    r = day_range(date_from, date_to)
    where = """o.created_at >= :start AND o.created_at < :end
               AND (:source = 'ALL' OR o.source = :source)
               AND (:internal OR o.is_internal IS NOT TRUE)"""
    args = {
        "start": r.start,
        "end": r.end,
        "source": source,
        "internal": include_internal,
        "limit": limit,
    }

    agg = (
        await s.execute(
            text(f"""
        SELECT count(*) n, coalesce(sum(o.total), 0) value,
               count(*) FILTER (WHERE o.sale_id IS NOT NULL) paid
        FROM store.shop_order o WHERE {where}
    """),
            args,
        )
    ).one()

    async def counts(column: str, empty: str) -> dict[str, int]:
        rows = (
            await s.execute(
                text(f"""
            SELECT coalesce({column}, :empty) k, count(*) n
            FROM store.shop_order o WHERE {where} GROUP BY 1 ORDER BY 2 DESC, 1
        """),
                {**args, "empty": empty},
            )
        ).all()
        return {row.k: row.n for row in rows}

    items = [
        {
            "order_number": row.order_number,
            "created_at": local_iso(row.created_at),
            "source": row.source,
            "status": row.status,
            "city": row.delivery_city,
            "total": money(row.total),
            "discount": money(row.discount_amount),
            "discount_code": row.discount_code,
            # Paid orders become sales. An order that never converts is the
            # first thing to look at when revenue and orders disagree.
            "converted_to_sale": row.sale_id is not None,
            "customer_name": row.full_name,  # leaves as initials
            "customer_email": row.email,  # leaves as a hash
            "attribution": {
                "utm_source": row.utm_source,
                "utm_medium": row.utm_medium,
                "utm_campaign": row.utm_campaign,
                "ad_id": row.utm_content,
                "landing_path": row.landing_path,
            },
        }
        for row in (
            await s.execute(
                text(f"""
            SELECT o.*, c.full_name, c.email
            FROM store.shop_order o LEFT JOIN store.customer c ON c.id = o.customer_id
            WHERE {where}
            ORDER BY o.created_at DESC, o.id DESC LIMIT :limit
        """),
                args,
            )
        ).all()
    ]

    return {
        "range": r.as_dict(),
        "source": source,
        "summary": {
            "orders": agg.n,
            "value": money(agg.value),
            "average_order": money(agg.value / agg.n) if agg.n else None,
            "paid": agg.paid,
            "paid_pct": pct(agg.paid, agg.n),
            # by_* dicts count orders of people, so small groups leave as "< 5".
            "by_status": await counts("o.status", "unknown"),
            "by_city": await counts("o.delivery_city", "no city"),
            "by_utm_source": await counts("o.utm_source", "direct"),
        },
        "returned": len(items),
        "items": items,
    }
