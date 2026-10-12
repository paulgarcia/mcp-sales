"""Is the data fresh, and is ad attribution working?

Call it first. Every other tool trusts the data underneath it: if the ads sync
stopped two days ago, or the ads stopped carrying their id into orders, the
other tools still answer, with numbers that look plausible and are wrong.
"""

from datetime import datetime, timedelta
from typing import Annotated, Any

from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sales_mcp.timeutil import DayRange, day_range, local_iso, money, today, tz

# How old the newest row of each source may be before it counts as stale.
STALE_AFTER_HOURS = {
    "sales": 48,
    "online_orders": 48,
    "web_events": 24,
    "whatsapp_conversations": 48,
}
# Meta reports a day once it is over, so yesterday is the newest possible.
ADS_STALE_AFTER_DAYS = 2

REMEDY = {
    "WEB": "Web orders no longer carry the ad id. Check that every WEB ad's URL parameters "
    "include utm_content={{ad.id}}.",
    "WHATSAPP": "Chats no longer record which ad opened them. Check the click-to-WhatsApp "
    "referral capture in the bot's webhook.",
}

Days = Annotated[int, Field(ge=1, le=90, description="Attribution window in days. Default 7.")]


async def attribution_health(s: AsyncSession, r: DayRange) -> dict[str, Any]:
    """Money spent on each lane against the orders/chats that name an ad.

    A lane is broken when it spent money, had orders or chats, and NONE of
    them carried an ad id. The lane matters for the fix: a WhatsApp ad has no
    "URL parameters" field to repair.
    """
    row = (
        await s.execute(
            text("""
        SELECT
          (SELECT coalesce(sum(i.spend), 0) FROM store.ad_insight_daily i
             JOIN store.ad a ON a.id = i.ad_id
            WHERE a.destination = 'WEB' AND i.day BETWEEN :first AND :last) AS web_spend,
          (SELECT coalesce(sum(i.spend), 0) FROM store.ad_insight_daily i
             JOIN store.ad a ON a.id = i.ad_id
            WHERE a.destination = 'WHATSAPP' AND i.day BETWEEN :first AND :last) AS chat_spend,
          (SELECT count(*) FROM store.shop_order o
            WHERE o.source = 'WEB' AND o.sale_id IS NOT NULL AND o.is_internal IS NOT TRUE
              AND o.created_at >= :start AND o.created_at < :end) AS web_orders,
          (SELECT count(*) FROM store.shop_order o
            WHERE o.source = 'WEB' AND o.sale_id IS NOT NULL AND o.is_internal IS NOT TRUE
              AND o.created_at >= :start AND o.created_at < :end
              AND o.utm_content IN (SELECT id FROM store.ad)) AS web_orders_with_ad,
          (SELECT count(*) FROM store.shop_order o
            WHERE o.source = 'WEB' AND o.is_internal IS NOT TRUE
              AND o.created_at >= :start AND o.created_at < :end
              AND o.utm_content IS NOT NULL
              AND o.utm_content NOT IN (SELECT id FROM store.ad)) AS unknown_ad_ids,
          (SELECT count(*) FROM store.conversation c
            WHERE c.created_at >= :start AND c.created_at < :end) AS chats,
          (SELECT count(*) FROM store.conversation c
            WHERE c.created_at >= :start AND c.created_at < :end
              AND c.from_ad_id IS NOT NULL) AS chats_with_ad
    """),
            {"first": r.first, "last": r.last, "start": r.start, "end": r.end},
        )
    ).one()

    web_broken = row.web_spend > 0 and row.web_orders > 0 and row.web_orders_with_ad == 0
    chat_broken = row.chat_spend > 0 and row.chats > 0 and row.chats_with_ad == 0
    lane = (
        "BOTH"
        if web_broken and chat_broken
        else "WEB"
        if web_broken
        else "WHATSAPP"
        if chat_broken
        else None
    )
    return {
        "range": r.as_dict(),
        "broken": lane is not None,
        "broken_lane": lane,
        "remedy": (" ".join(REMEDY[k] for k in ("WEB", "WHATSAPP") if lane in (k, "BOTH")) or None),
        "web": {
            "ad_spend": money(row.web_spend),
            "paid_orders": row.web_orders,
            "paid_orders_with_ad_id": row.web_orders_with_ad,
            "orders_with_unknown_ad_id": row.unknown_ad_ids,
        },
        "whatsapp": {
            "ad_spend": money(row.chat_spend),
            "conversations": row.chats,
            "conversations_from_ads": row.chats_with_ad,
        },
    }


async def data_health(s: AsyncSession, days: Days = 7) -> dict[str, Any]:
    now = datetime.now(tz())
    newest = (
        await s.execute(
            text("""
        SELECT
          (SELECT max(sold_at) FROM store.sale WHERE is_internal IS NOT TRUE) AS sales,
          (SELECT max(created_at) FROM store.shop_order WHERE is_internal IS NOT TRUE)
              AS online_orders,
          (SELECT max(created_at) FROM store.web_event WHERE is_internal IS NOT TRUE)
              AS web_events,
          (SELECT max(created_at) FROM store.conversation) AS whatsapp_conversations,
          (SELECT max(day) FROM store.ad_insight_daily) AS ads_day
    """)
        )
    ).one()

    sources = []
    for name, limit_h in STALE_AFTER_HOURS.items():
        last: datetime | None = getattr(newest, name)
        hours = round((now - last).total_seconds() / 3600, 1) if last else None
        sources.append(
            {
                "source": name,
                "newest": local_iso(last),
                "hours_ago": hours,
                "stale_after_hours": limit_h,
                "status": "EMPTY" if last is None else "STALE" if hours > limit_h else "OK",
            }
        )
    ads_age = (today() - newest.ads_day).days if newest.ads_day else None
    sources.append(
        {
            "source": "meta_ads",
            "newest": newest.ads_day.isoformat() if newest.ads_day else None,
            "days_ago": ads_age,
            "stale_after_days": ADS_STALE_AFTER_DAYS,
            "status": (
                "EMPTY" if ads_age is None else "STALE" if ads_age > ADS_STALE_AFTER_DAYS else "OK"
            ),
        }
    )

    attribution = await attribution_health(
        s, day_range(today() - timedelta(days=days - 1), today())
    )
    healthy = all(src["status"] == "OK" for src in sources) and not attribution["broken"]
    return {
        "checked_at": now.isoformat(),
        "status": "OK" if healthy else "CHECK",
        "sources": sources,
        "attribution": attribution,
        "note": "If status is CHECK, say so before quoting any other number: a stale source "
        "or broken attribution makes the other tools wrong in a way that looks "
        "plausible.",
    }
