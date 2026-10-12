"""Per product: views, carts, units sold, conversion and revenue.

`window_shopping: true` marks what many people look at and nobody buys. It is
the finding that never shows up in a sales ranking, because a ranking only
lists what sold. The cause is usually the price, the photo or the page.
"""

from typing import Annotated, Any, Literal

from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sales_mcp.timeutil import day_range, money
from sales_mcp.tools.params import Brand, DateFrom, DateTo, Limit200

WINDOW_SHOPPING_MIN_VIEWS = 20

ORDER_BY = {
    "sales": "units_sold DESC, views DESC",
    "views": "views DESC, units_sold DESC",
    "conversion": "conversion DESC NULLS LAST, views DESC",
}

OrderBy = Annotated[
    Literal["sales", "views", "conversion"],
    Field(description="Sort key. Default 'sales'."),
]


async def product_performance(
    s: AsyncSession,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    order_by: OrderBy = "sales",
    brand: Brand = None,
    limit: Limit200 = 25,
) -> dict[str, Any]:
    r = day_range(date_from, date_to)
    rows = (
        await s.execute(
            text(f"""
        WITH views AS (
            SELECT variant_id, count(*) n FROM store.web_event
            WHERE event_name = 'VIEW_PRODUCT' AND created_at >= :start AND created_at < :end
              AND is_internal IS NOT TRUE
            GROUP BY 1
        ), carts AS (
            SELECT variant_id, count(*) n FROM store.web_event
            WHERE event_name = 'ADD_TO_CART' AND created_at >= :start AND created_at < :end
              AND is_internal IS NOT TRUE
            GROUP BY 1
        ), sold AS (
            SELECT iu.variant_id, count(*) n, sum(sl.unit_sale_price) revenue
            FROM store.sale_line sl
            JOIN store.inventory_unit iu ON iu.id = sl.inventory_unit_id
            JOIN store.sale s ON s.id = sl.sale_id
            WHERE s.sold_at >= :start AND s.sold_at < :end AND s.is_internal IS NOT TRUE
            GROUP BY 1
        )
        SELECT pv.id, pv.sku, p.name product, pv.variant_name variant, b.name brand,
               coalesce(views.n, 0) views, coalesce(carts.n, 0) carts,
               coalesce(sold.n, 0) units_sold, coalesce(sold.revenue, 0) revenue,
               CASE WHEN coalesce(views.n, 0) > 0
                    THEN round(coalesce(sold.n, 0)::numeric / views.n, 4) END conversion
        FROM store.product_variant pv
        JOIN store.product p ON p.id = pv.product_id
        LEFT JOIN store.brand b ON b.id = p.brand_id
        LEFT JOIN views ON views.variant_id = pv.id
        LEFT JOIN carts ON carts.variant_id = pv.id
        LEFT JOIN sold ON sold.variant_id = pv.id
        WHERE (coalesce(views.n, 0) > 0 OR coalesce(sold.n, 0) > 0)
          AND (CAST(:brand AS text) IS NULL OR b.name ILIKE '%' || CAST(:brand AS text) || '%')
        ORDER BY {ORDER_BY[order_by]}, pv.id
        LIMIT :limit
    """),
            {"start": r.start, "end": r.end, "brand": brand, "limit": limit},
        )
    ).all()

    items = [
        {
            "sku": row.sku,
            "product": row.product,
            "variant": row.variant,
            "brand": row.brand,
            "views": row.views,
            "add_to_carts": row.carts,
            "units_sold": row.units_sold,
            "revenue": money(row.revenue),
            "conversion": float(row.conversion) if row.conversion is not None else None,
            "window_shopping": row.views >= WINDOW_SHOPPING_MIN_VIEWS and row.units_sold == 0,
        }
        for row in rows
    ]
    return {
        "range": r.as_dict(),
        "order_by": order_by,
        "window_shopping_min_views": WINDOW_SHOPPING_MIN_VIEWS,
        "returned": len(items),
        "items": items,
        "notes": [
            "conversion = units sold / product views, from the store's own events "
            "(no cookie consent involved).",
            "revenue here is summed per line: no shipping and no order-level discounts, "
            "so it runs slightly above sales_summary.",
        ],
    }
