# Ads & margin playbook

The rules the MCP tools use to judge numbers. Thresholds that the business
decides (not the data) live in the `business_setting` table.

## Definitions

| Metric | Definition |
|---|---|
| CTR | clicks / impressions |
| CPM | spend / impressions x 1000 |
| CPA | ad spend / sales the ad really brought (store data, not Meta's) |
| ROAS | revenue the ad brought / ad spend |
| MER | ALL store revenue / ALL ad spend. Needs no attribution at all. |
| Gross margin | price - landed cost of the unit (purchase + freight) |
| Contribution margin | revenue without shipping - landed cost - seller commission - card fees - packing |
| Break-even ROAS | 1 / contribution margin share |
| Break-even CPA | contribution per sale: the most a sale can cost in ads |

## Where the numbers come from

- Only **WEB** sales send a Purchase event to Meta. Sellers, consignment and
  WhatsApp sales never reach Ads Manager. Compare Ads Manager with the WEB
  channel, never with the store total.
- An ad's sales are matched by `utm_content={{ad.id}}` on web orders, and by
  the ad that opened the conversation for WhatsApp. Both are first-party, so
  they do not depend on cookie consent. Meta's own count travels alongside.

## The ad verdict (CTR x CPA)

1. Fewer than **5 days** of delivery in the ad's whole life: **NO_EVIDENCE**.
   It is still learning. Do not judge it, do not turn it off.
2. No sales and less spent than one CPA ceiling: **NO_EVIDENCE**.
3. CPA at or under the ceiling: **WINNER** if CTR >= 1 %, otherwise **NICHE**.
4. CPA over the ceiling: **HOOK** if CTR < 1 % (the hook does not stop the
   scroll), **STORE** if CTR >= 1 % (people click and do not buy: price, page
   or offer).

| Verdict | Action |
|---|---|
| WINNER | Duplicate it changing ONE thing; keep the original running. |
| HOOK | Change only the hook and launch a v2. |
| STORE | Fix the price, the product page or the offer, not the ad. |
| NICHE | Leave it alone: cheap but no volume. |
| NO_EVIDENCE | Wait. |

A winner whose product is out of stock is still a winner, and still paying to
send people to a dead end. Pause it or restock before duplicating it.

## Privacy

Emails and phones leave only as SHA-256 hashes (Meta's normalization).
Addresses, coordinates and national ids never leave. Names leave as initials.
Counts of fewer than 5 people leave as "< 5".
