"""Performance per AD, with the Monday verdict.

The difference with Ads Manager: here CPA and ROAS come from OUR sales, not
from the ones Meta manages to see. Meta only sees buyers who accepted cookies
and never sees a sale closed in a chat. The store sees every order that came
in with utm_content={{ad.id}}, because that capture is first-party, and every
chat a click-to-WhatsApp ad opened. Meta's number travels alongside, so the
undercount can be measured instead of guessed.

A NEW AD IS NOT JUDGED. The playbook asks for at least 5 days of delivery (the
learning phase) before calling anything a winner or a loser. Turning an ad off
after two bad days is the most common way to kill one that was about to work.
That guard looks at the ad's LIFE, not at the window asked: an ad that has run
for three months does not become new again because we look at one week. The
spend guard, on the other hand, IS about the window: did we spend enough in
THIS period to expect a sale in THIS period?
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sales_mcp.timeutil import day_range, money, pct
from sales_mcp.tools.health import attribution_health
from sales_mcp.tools.params import DateFrom, DateTo

CTR_HEALTHY_PCT = 1.0
CTR_BAD_PCT = 0.8

VERDICTS = {
    "NO_EVIDENCE": (
        "Not enough evidence",
        "Still in the learning phase: do not judge it and do not turn it off.",
    ),
    "WINNER": (
        "Winner",
        "Duplicate it changing ONE thing (another product or another hook) and keep "
        "the original running.",
    ),
    "HOOK": (
        "The hook does not stop the scroll",
        "Change ONLY the hook (same product, same video if it works) and launch a v2.",
    ),
    "STORE": (
        "Attracts but does not close",
        "The problem is not the ad: check the visible price, the product page and the offer.",
    ),
    "NICHE": (
        "Small niche that converts",
        "Leave it alone: it converts cheaply but has no volume to scale.",
    ),
}


def verdict_key(
    *,
    ctr_pct: float | None,
    cpa: Decimal | None,
    days_alive: int,
    spend: Decimal,
    cpa_ceiling: Decimal,
    min_days: int,
) -> str:
    """The playbook's CTR x CPA tree.

    cpa=None means ZERO sales. That only counts as a high CPA once the ad has
    spent at least one CPA ceiling; before that there was not enough money to
    expect a sale.
    """
    if days_alive < min_days or ctr_pct is None:
        return "NO_EVIDENCE"
    if cpa is None:
        if spend < cpa_ceiling:
            return "NO_EVIDENCE"
        cpa_high = True
    else:
        cpa_high = cpa > cpa_ceiling
    if not cpa_high:
        return "WINNER" if ctr_pct >= CTR_HEALTHY_PCT else "NICHE"
    if ctr_pct < CTR_BAD_PCT:
        return "HOOK"
    if ctr_pct >= CTR_HEALTHY_PCT:
        return "STORE"
    # Grey zone (CTR between 0.8 % and 1 %) with a high CPA: the hook is the
    # cheap thing to fix, so start there.
    return "HOOK"


async def ads_performance(
    s: AsyncSession,
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> dict[str, Any]:
    r = day_range(date_from, date_to)
    args = {"first": r.first, "last": r.last, "start": r.start, "end": r.end}

    setting = {
        row.key: row.value
        for row in (await s.execute(text("SELECT key, value FROM store.business_setting"))).all()
    }
    cpa_ceiling = setting.get("cpa_ceiling", Decimal("12"))
    min_days = int(setting.get("min_days_to_judge_ad", 5))

    ads = (
        await s.execute(
            text("""
        SELECT a.id, a.name, a.campaign_name, a.destination, a.status, a.product_id,
               p.name AS product,
               sum(i.spend) spend, sum(i.impressions) impressions, sum(i.clicks) clicks,
               sum(i.meta_purchases) meta_purchases,
               count(*) FILTER (WHERE i.spend > 0) days_with_data,
               (SELECT count(*) FROM store.ad_insight_daily life
                 WHERE life.ad_id = a.id AND life.spend > 0 AND life.day <= :last) days_alive
        FROM store.ad a
        JOIN store.ad_insight_daily i ON i.ad_id = a.id AND i.day BETWEEN :first AND :last
        LEFT JOIN store.product p ON p.id = a.product_id
        GROUP BY a.id, p.name
        HAVING sum(i.spend) > 0
        ORDER BY sum(i.spend) DESC, a.id
    """),
            args,
        )
    ).all()

    # Our own sales per ad: web orders by utm_content, chat sales by the ad
    # that opened the conversation.
    own = {
        row.ad_id: row
        for row in (
            await s.execute(
                text("""
        SELECT ad_id, count(*) sales, sum(total_amount) revenue FROM (
            SELECT o.utm_content AS ad_id, s.total_amount
            FROM store.shop_order o JOIN store.sale s ON s.id = o.sale_id
            WHERE o.source = 'WEB' AND o.utm_content IS NOT NULL
              AND s.sold_at >= :start AND s.sold_at < :end AND s.is_internal IS NOT TRUE
            UNION ALL
            SELECT c.from_ad_id, s.total_amount
            FROM store.conversation c
            JOIN store.shop_order o ON o.conversation_id = c.id
            JOIN store.sale s ON s.id = o.sale_id
            WHERE c.from_ad_id IS NOT NULL
              AND s.sold_at >= :start AND s.sold_at < :end AND s.is_internal IS NOT TRUE
        ) attributed
        GROUP BY ad_id
    """),
                args,
            )
        ).all()
    }

    # coalesce: a product with no unit rows at all is out of stock, not
    # "unknown". bool_or over nothing is NULL, and NULL would hide the warning.
    in_stock = {
        row.product_id: row.ok
        for row in (
            await s.execute(
                text("""
        SELECT pv.product_id, coalesce(bool_or(iu.status = 'AVAILABLE'), false) ok
        FROM store.product_variant pv
        LEFT JOIN store.inventory_unit iu ON iu.variant_id = pv.id
        GROUP BY 1
    """)
            )
        ).all()
    }

    rows = []
    total_spend, total_sales, total_revenue = Decimal(0), 0, Decimal(0)
    for ad in ads:
        mine = own.get(ad.id)
        sales = mine.sales if mine else 0
        revenue = mine.revenue if mine else Decimal(0)
        ctr = round(ad.clicks / ad.impressions * 100, 2) if ad.impressions else None
        cpa = ad.spend / sales if sales else None
        key = verdict_key(
            ctr_pct=ctr,
            cpa=cpa,
            days_alive=ad.days_alive,
            spend=ad.spend,
            cpa_ceiling=cpa_ceiling,
            min_days=min_days,
        )
        title, action = VERDICTS[key]
        stock = in_stock.get(ad.product_id) if ad.product_id else None
        warnings = []
        if stock is False:
            warnings.append(
                "The advertised product is OUT OF STOCK: this ad is paying to "
                "send people to something they cannot buy. Pause it or restock."
            )
        if ad.destination == "WHATSAPP":
            warnings.append(
                "Meta never sees a sale closed in a chat: its purchase count for "
                "this ad will always be 0."
            )
        rows.append(
            {
                "ad_id": ad.id,
                "name": ad.name,
                "campaign": ad.campaign_name,
                "destination": ad.destination,
                "product": ad.product,
                "status": ad.status,
                "spend": money(ad.spend),
                "impressions": ad.impressions,
                "clicks": ad.clicks,
                "ctr_pct": ctr,
                "cpm": money(ad.spend / ad.impressions * 1000) if ad.impressions else None,
                "sales": sales,
                "revenue": money(revenue),
                "cpa": money(cpa) if cpa is not None else None,
                "roas": round(float(revenue / ad.spend), 2),
                "meta_purchases": ad.meta_purchases,
                "cpa_meta": money(ad.spend / ad.meta_purchases) if ad.meta_purchases else None,
                "meta_undercount_pct": pct(sales - ad.meta_purchases, sales),
                "days_with_data": ad.days_with_data,
                "days_alive": ad.days_alive,
                "product_in_stock": stock,
                "verdict": {"key": key, "title": title, "action": action},
                "warnings": warnings,
            }
        )
        total_spend += ad.spend
        total_sales += sales
        total_revenue += revenue

    store_revenue = (
        await s.execute(
            text("""
        SELECT coalesce(sum(total_amount), 0) FROM store.sale
        WHERE sold_at >= :start AND sold_at < :end AND is_internal IS NOT TRUE
    """),
            args,
        )
    ).scalar_one()

    return {
        "range": r.as_dict(),
        "rules": {
            "cpa_ceiling": money(cpa_ceiling),
            "min_days_to_judge": min_days,
            "ctr_healthy_pct": CTR_HEALTHY_PCT,
            "ctr_bad_pct": CTR_BAD_PCT,
        },
        "totals": {
            "ad_spend": money(total_spend),
            "attributed_sales": total_sales,
            "attributed_revenue": money(total_revenue),
            "attributed_roas": round(float(total_revenue / total_spend), 2)
            if total_spend
            else None,
            "store_revenue": money(store_revenue),
            # MER = everything the store sold / everything spent on ads. It
            # does not depend on attribution at all, which is its point.
            "mer": round(float(store_revenue / total_spend), 2) if total_spend else None,
        },
        "ads": rows,
        "attribution": await attribution_health(s, r),
        "notes": [
            "sales, revenue, cpa and roas are the STORE's numbers (first-party, complete). "
            "meta_purchases and cpa_meta are what Meta reports.",
            "An ad with fewer than min_days_to_judge days of delivery in its whole life is "
            "NO_EVIDENCE on purpose: it is still learning.",
        ],
    }
