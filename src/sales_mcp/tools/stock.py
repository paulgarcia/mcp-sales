"""Stock per variant, and the stock problems that cost money.

Two questions an owner asks every week, answered with the same rows:
"what is about to run out?" and "am I paying to advertise something I cannot
sell?". The second one is the expensive one: an ad does not know the shelf is
empty, it keeps spending and keeps sending people to a dead end.
"""

from datetime import timedelta
from typing import Annotated, Any

from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sales_mcp.timeutil import day_range, money, today
from sales_mcp.tools.params import Brand, Limit500

RUNNING_LOW_DAYS = 14
VELOCITY_DAYS = 30
DEAD_STOCK_DAYS = 90
AD_LOOKBACK_DAYS = 7
PROBLEMS = ("out_of_stock", "running_low", "advertised_while_out_of_stock", "dead_stock")

OnlyProblems = Annotated[
    bool,
    Field(
        description="Return only variants with a problem (out of stock, running low, "
        "advertised while out of stock, dead stock). Default false."
    ),
]


async def stock_status(
    s: AsyncSession,
    brand: Brand = None,
    only_problems: OnlyProblems = False,
    limit: Limit500 = 200,
) -> dict[str, Any]:
    last = today()
    velocity = day_range(last - timedelta(days=VELOCITY_DAYS - 1), last)
    dead = day_range(last - timedelta(days=DEAD_STOCK_DAYS - 1), last)
    ads_from = last - timedelta(days=AD_LOOKBACK_DAYS)

    rows = (
        await s.execute(
            text("""
        WITH units AS (
            SELECT variant_id,
                   count(*) FILTER (WHERE status = 'AVAILABLE')  on_hand,
                   count(*) FILTER (WHERE status = 'RESERVED')   reserved,
                   count(*) FILTER (WHERE status = 'IN_TRANSIT') in_transit
            FROM store.inventory_unit GROUP BY 1
        ), sold AS (
            SELECT iu.variant_id,
                   count(*) FILTER (WHERE s.sold_at >= :v_start) recent,
                   count(*) FILTER (WHERE s.sold_at >= :d_start) longer
            FROM store.sale_line sl
            JOIN store.inventory_unit iu ON iu.id = sl.inventory_unit_id
            JOIN store.sale s ON s.id = sl.sale_id
            WHERE s.sold_at >= :d_start AND s.sold_at < :end AND s.is_internal IS NOT TRUE
            GROUP BY 1
        ), ad_spend AS (
            SELECT a.product_id, sum(i.spend) spend
            FROM store.ad a JOIN store.ad_insight_daily i ON i.ad_id = a.id
            WHERE a.status = 'ACTIVE' AND i.day >= :ads_from AND a.product_id IS NOT NULL
            GROUP BY 1
        )
        SELECT pv.id, pv.sku, p.name product, pv.variant_name variant, b.name brand,
               pv.public_price price,
               coalesce(u.on_hand, 0) on_hand, coalesce(u.reserved, 0) reserved,
               coalesce(u.in_transit, 0) in_transit,
               coalesce(sold.recent, 0) sold_recent, coalesce(sold.longer, 0) sold_longer,
               coalesce(ad_spend.spend, 0) ad_spend
        FROM store.product_variant pv
        JOIN store.product p ON p.id = pv.product_id
        LEFT JOIN store.brand b ON b.id = p.brand_id
        LEFT JOIN units u ON u.variant_id = pv.id
        LEFT JOIN sold ON sold.variant_id = pv.id
        LEFT JOIN ad_spend ON ad_spend.product_id = p.id
        WHERE CAST(:brand AS text) IS NULL OR b.name ILIKE '%' || CAST(:brand AS text) || '%'
        ORDER BY pv.id
    """),
            {
                "v_start": velocity.start,
                "d_start": dead.start,
                "end": velocity.end,
                "ads_from": ads_from,
                "brand": brand,
            },
        )
    ).all()

    items = []
    problems = 0
    for row in rows:
        daily = row.sold_recent / VELOCITY_DAYS
        cover = round(row.on_hand / daily, 1) if daily else None
        flags = {
            "out_of_stock": row.on_hand == 0,
            "running_low": row.on_hand > 0 and cover is not None and cover < RUNNING_LOW_DAYS,
            "advertised_while_out_of_stock": row.on_hand == 0 and row.ad_spend > 0,
            "dead_stock": row.on_hand > 0 and row.sold_longer == 0,
        }
        has_problem = any(flags[k] for k in PROBLEMS)
        problems += has_problem
        if only_problems and not has_problem:
            continue
        items.append(
            {
                "sku": row.sku,
                "product": row.product,
                "variant": row.variant,
                "brand": row.brand,
                "price": money(row.price),
                "units_on_hand": row.on_hand,
                "units_reserved": row.reserved,
                "units_in_transit": row.in_transit,
                f"units_sold_last_{VELOCITY_DAYS}d": row.sold_recent,
                "days_of_cover": cover,
                f"ad_spend_last_{AD_LOOKBACK_DAYS}d": money(row.ad_spend),
                **flags,
            }
        )

    return {
        # `checked` and `with_problems` are counted before the filter and the
        # limit. With only_problems=true, a single "total" counting what came
        # back would say "0 of 0" and read as "no catalog" instead of "no
        # problems", which is the opposite reading.
        "checked": len(rows),
        "with_problems": problems,
        "returned": len(items[:limit]),
        "items": items[:limit],
        "thresholds": {
            "running_low_below_days_of_cover": RUNNING_LOW_DAYS,
            "velocity_window_days": VELOCITY_DAYS,
            "dead_stock_after_days_without_sales": DEAD_STOCK_DAYS,
        },
        "notes": [
            "units_on_hand counts AVAILABLE units only. In-transit units are paid for and on "
            "their way; reserved units are held by unpaid bot orders.",
            "advertised_while_out_of_stock: an ACTIVE ad spent money on this product in the "
            f"last {AD_LOOKBACK_DAYS} days while this variant had no units. Pause the ad or "
            "restock.",
        ],
    }
