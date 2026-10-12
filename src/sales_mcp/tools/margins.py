"""How much is really earned per unit sold, by product, brand and tier.

THE COST IS NOT AN AVERAGE. Every physical unit is tracked, so the cost of a
sale is the landed cost of the unit that actually left:

    landed = acquisition_unit_cost + import_cost (freight and duties)

It is computed on what SOLD in the range, not on the catalog: the margin of
something that did not sell is a hypothesis, not a fact.

TWO MARGINS, AND THE DIFFERENCE SINKS CAMPAIGNS. Gross margin is price minus
landed cost. It ignores seller commissions, card fees and packing. The
maximum cost per acquisition and the break-even ROAS must come from the
CONTRIBUTION margin, which subtracts them. A campaign judged on gross margin
looks profitable while it loses money.
"""

from typing import Annotated, Any, Literal

from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sales_mcp.timeutil import DayRange, day_range, money, pct
from sales_mcp.tools.params import Brand, DateFrom, DateTo, Limit200

LANDED = "(iu.acquisition_unit_cost + coalesce(iu.import_cost, 0))"

ORDER_BY = {
    "margin": "margin DESC",
    "margin_pct": "margin_pct DESC NULLS LAST",
    "units": "units DESC",
}

OrderBy = Annotated[
    Literal["margin", "margin_pct", "units"],
    Field(description="Sort key for the breakdowns. Default 'margin' (total dollars)."),
]


async def contribution(s: AsyncSession, r: DayRange) -> dict[str, Any]:
    """Contribution margin of the sales in the range.

    Revenue without shipping (the courier keeps it), minus the landed cost of
    the units, minus what each sale costs to make: the seller's commission,
    the card processor's fee and the packing.

    commission_rate is a FRACTION (0.20), never a percentage. Dividing it by
    100 a second time turns a $12 commission on a $60 sale into $0.12, and the
    contribution margin quietly comes out inflated for months.
    """
    args = {"start": r.start, "end": r.end}
    row = (
        await s.execute(
            text(f"""
        WITH sales AS (
            SELECT s.id, s.total_amount - s.shipping_fee AS net, sel.commission_rate
            FROM store.sale s
            LEFT JOIN store.seller sel ON sel.username = s.seller_username
            WHERE s.sold_at >= :start AND s.sold_at < :end AND s.is_internal IS NOT TRUE
        )
        SELECT
          (SELECT count(*) FROM sales) AS n,
          (SELECT coalesce(sum(net), 0) FROM sales) AS revenue,
          (SELECT coalesce(sum({LANDED}), 0)
             FROM store.sale_line sl JOIN store.inventory_unit iu ON iu.id = sl.inventory_unit_id
            WHERE sl.sale_id IN (SELECT id FROM sales)) AS cogs,
          (SELECT coalesce(sum(net * commission_rate), 0) FROM sales
            WHERE commission_rate IS NOT NULL) AS commissions,
          (SELECT coalesce(sum(p.gateway_fee), 0) FROM store.sale_payment p
            WHERE p.sale_id IN (SELECT id FROM sales)) AS fees,
          (SELECT coalesce(max(value), 0) FROM store.business_setting
            WHERE key = 'packing_cost_per_sale') AS packing_each
    """),
            args,
        )
    ).one()

    packing = row.packing_each * row.n
    result = row.revenue - row.cogs - row.commissions - row.fees - packing
    share = result / row.revenue if row.revenue else None
    return {
        "sales": row.n,
        "revenue_without_shipping": money(row.revenue),
        "landed_cost": money(row.cogs),
        "seller_commissions": money(row.commissions),
        "card_fees": money(row.fees),
        "packing": money(packing),
        "contribution": money(result),
        "contribution_pct": pct(result, row.revenue),
        "contribution_per_sale": money(result / row.n) if row.n else None,
        # An ad pays for itself when revenue / spend = 1 / contribution share.
        "break_even_roas": round(float(1 / share), 2) if share and share > 0 else None,
        "note": "contribution_per_sale is the most a sale can cost in ads before it loses "
        "money (the break-even CPA).",
    }


async def margins(
    s: AsyncSession,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    brand: Brand = None,
    order_by: OrderBy = "margin",
    limit: Limit200 = 50,
) -> dict[str, Any]:
    r = day_range(date_from, date_to)
    args = {"start": r.start, "end": r.end, "brand": brand, "limit": limit}
    sold = """
        FROM store.sale_line sl
        JOIN store.inventory_unit iu ON iu.id = sl.inventory_unit_id
        JOIN store.sale s ON s.id = sl.sale_id
        JOIN store.product_variant pv ON pv.id = iu.variant_id
        JOIN store.product p ON p.id = pv.product_id
        LEFT JOIN store.brand b ON b.id = p.brand_id
        WHERE s.sold_at >= :start AND s.sold_at < :end AND s.is_internal IS NOT TRUE
          AND (CAST(:brand AS text) IS NULL OR b.name ILIKE '%' || CAST(:brand AS text) || '%')
    """

    totals = (
        await s.execute(
            text(f"""
        SELECT count(*) units, coalesce(sum(sl.unit_sale_price), 0) revenue,
               coalesce(sum({LANDED}), 0) cost
        {sold}
    """),
            args,
        )
    ).one()

    async def breakdown(
        label: str, key: str, group_by: str, with_brand: bool = False
    ) -> list[dict[str, Any]]:
        brand_col = ", b.name AS brand" if with_brand else ""
        rows = (
            await s.execute(
                text(f"""
            SELECT {key} AS key {brand_col},
                   count(*) units,
                   sum(sl.unit_sale_price) revenue,
                   sum({LANDED}) cost,
                   sum(sl.unit_sale_price - {LANDED}) margin,
                   round(sum(sl.unit_sale_price - {LANDED}) * 100
                         / nullif(sum(sl.unit_sale_price), 0), 1) margin_pct
            {sold}
            GROUP BY {group_by}
            ORDER BY {ORDER_BY[order_by]}, 1
            LIMIT :limit
        """),
                args,
            )
        ).all()
        out = []
        for row in rows:
            item = {
                label: row.key,
                "units": row.units,
                "revenue": money(row.revenue),
                "landed_cost": money(row.cost),
                "margin": money(row.margin),
                "margin_per_unit": money(row.margin / row.units),
                "average_price": money(row.revenue / row.units),
                "margin_pct": float(row.margin_pct) if row.margin_pct is not None else None,
            }
            if with_brand:
                item["brand"] = row.brand
            out.append(item)
        return out

    margin_total = totals.revenue - totals.cost
    return {
        "range": r.as_dict(),
        "totals": {
            "units": totals.units,
            "revenue": money(totals.revenue),
            "landed_cost": money(totals.cost),
            "gross_margin": money(margin_total),
            "gross_margin_pct": pct(margin_total, totals.revenue),
        },
        "contribution": await contribution(s, r) if brand is None else None,
        # Lists, not dicts: these count units, and only counts of PEOPLE go
        # through k-anonymity.
        "products": await breakdown("product", "p.name", "p.id, p.name, b.name", with_brand=True),
        "brands": await breakdown("brand", "coalesce(b.name, 'no brand')", "1"),
        "tiers": await breakdown("tier", "coalesce(b.tier, 'MID')", "1"),
        "notes": [
            "gross_margin = price - landed cost (purchase + freight). It does NOT subtract "
            "commissions, card fees or packing. Use `contribution` for max CPA and "
            "break-even ROAS.",
            "Revenue here is summed per sale line: no shipping and no order-level discounts, "
            "so it runs slightly above sales_summary. The gap is exactly those discounts.",
            "contribution is computed for the whole store, so it is null when a brand "
            "filter is set.",
        ],
    }
