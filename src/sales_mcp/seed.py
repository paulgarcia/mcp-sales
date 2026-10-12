"""Deterministic fake data for the demo.

Same seed + same anchor day = same database, so a demo can be recorded twice
and say the same thing. Nothing here is real business data: brands, people,
prices and volumes are invented. The SHAPES are real, and so are the traps the
tools have to handle:

  * most sales come from sellers, which Meta never sees;
  * web sales peak at night, when the local day and the UTC day disagree;
  * some sales are paid in installments, so "sold" and "collected" differ;
  * staff test orders are flagged and must not count as business;
  * ad-driven orders carry the ad id in utm_content, and Meta only sees about
    two thirds of them (cookie consent);
  * one winning ad keeps spending on a product that is out of stock;
  * one perfume gets plenty of views and no sales.
"""

from __future__ import annotations

import math
import random
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import asyncpg

SEED = 20261009
DAYS = 240
CENT = Decimal("0.01")
SHIPPING_FEE = Decimal("3.50")
FREE_SHIPPING_FROM = Decimal("40.00")

# brand, tier, category, product, price, sales weight, variants
CATALOG: list[tuple[str, str, str, str, str, float, list[str] | None]] = [
    ("Lumen Skin", "PREMIUM", "SKINCARE", "Vitamin C Brightening Serum 30ml", "34.90", 10, None),
    ("Lumen Skin", "PREMIUM", "SKINCARE", "Retinol Night Serum 30ml", "39.90", 6, None),
    ("Lumen Skin", "PREMIUM", "SKINCARE", "Hyaluronic Gel Cream 50ml", "32.50", 7, None),
    ("Petal Lab", "MID", "SKINCARE", "Niacinamide 10% Toner 150ml", "18.90", 8, None),
    ("Petal Lab", "MID", "SKINCARE", "Gentle Foaming Cleanser 200ml", "14.50", 9, None),
    ("Petal Lab", "MID", "SKINCARE", "SPF 50 Daily Fluid 50ml", "22.90", 9, None),
    ("Petal Lab", "MID", "SKINCARE", "Ceramide Barrier Cream 50ml", "24.90", 5, None),
    ("Solace Derm", "PREMIUM", "SKINCARE", "Azelaic Acid 10% Gel 30ml", "29.90", 3, None),
    ("Solace Derm", "PREMIUM", "SKINCARE", "Peptide Eye Cream 15ml", "36.00", 3, None),
    (
        "Solace Derm",
        "PREMIUM",
        "SKINCARE",
        "Tinted Mineral Sunscreen SPF 50",
        "31.50",
        4,
        ["Light", "Medium", "Deep"],
    ),
    ("Kaya Botanics", "VALUE", "SKINCARE", "Aloe Soothing Gel 250ml", "8.90", 7, None),
    ("Kaya Botanics", "VALUE", "SKINCARE", "Rice Water Sheet Mask (5 pack)", "9.90", 6, None),
    ("Kaya Botanics", "VALUE", "SKINCARE", "Micellar Water 400ml", "10.50", 8, None),
    (
        "Vela Cosmetics",
        "MID",
        "MAKEUP",
        "Matte Lipstick",
        "12.90",
        8,
        ["Rosewood", "Ruby", "Nude Petal", "Berry"],
    ),
    (
        "Vela Cosmetics",
        "MID",
        "MAKEUP",
        "Long-Wear Foundation 30ml",
        "21.90",
        7,
        ["110 Ivory", "220 Sand", "330 Honey", "440 Caramel"],
    ),
    ("Vela Cosmetics", "MID", "MAKEUP", "Volume Mascara", "13.50", 8, None),
    ("Vela Cosmetics", "MID", "MAKEUP", "Brow Sculpt Gel", "10.90", 4, ["Blonde", "Brown", "Dark"]),
    ("Vela Cosmetics", "MID", "MAKEUP", "Blush Duo", "15.90", 3, ["Peach", "Coral"]),
    ("Andina Glow", "VALUE", "MAKEUP", "Lip & Cheek Tint", "7.90", 6, ["Cherry", "Rose"]),
    ("Andina Glow", "VALUE", "MAKEUP", "Setting Powder", "9.50", 4, None),
    ("Andina Glow", "VALUE", "MAKEUP", "Eyeliner Pen", "6.90", 5, None),
    ("Nori Beauty", "MID", "MAKEUP", "Glass Skin Primer 30ml", "19.90", 3, None),
    ("Nori Beauty", "MID", "MAKEUP", "Cushion Foundation", "27.90", 4, ["21 Light", "23 Natural"]),
    ("Pura Vita", "VALUE", "SUPPLEMENTS", "Collagen Powder 300g", "28.90", 6, None),
    ("Pura Vita", "VALUE", "SUPPLEMENTS", "Biotin Gummies (60)", "16.90", 6, None),
    ("Pura Vita", "VALUE", "SUPPLEMENTS", "Magnesium + Zinc (90 caps)", "14.90", 3, None),
    ("Pura Vita", "VALUE", "SUPPLEMENTS", "Hair, Skin & Nails (60 caps)", "19.50", 4, None),
    ("Mira Hair", "MID", "HAIRCARE", "Argan Repair Mask 250ml", "17.90", 5, None),
    ("Mira Hair", "MID", "HAIRCARE", "Keratin Shampoo 400ml", "13.90", 5, None),
    ("Mira Hair", "MID", "HAIRCARE", "Curl Defining Cream 250ml", "15.50", 3, None),
    ("Mira Hair", "MID", "HAIRCARE", "Heat Protect Spray 200ml", "12.90", 3, None),
    # Plenty of views, no sales: the "window shopping" finding.
    ("Noir Atelier", "PREMIUM", "FRAGRANCE", "Eau de Parfum Velours 100ml", "119.00", 0, None),
    ("Noir Atelier", "PREMIUM", "FRAGRANCE", "Body Mist Aurora 200ml", "24.90", 3, None),
]

WINDOW_SHOPPING = "Eau de Parfum Velours 100ml"
RUNNING_LOW = {
    "Vitamin C Brightening Serum 30ml",
    "Matte Lipstick / Ruby",
    "Collagen Powder 300g",
    "Micellar Water 400ml",
}
# label -> days before the anchor when the last unit was sold
STOCKOUTS = {"Hyaluronic Gel Cream 50ml": 6, "Long-Wear Foundation 30ml / 330 Honey": 15}
IN_TRANSIT = {"Long-Wear Foundation 30ml / 330 Honey": 12}

# id, name, campaign, destination, product, first day, last day (None = still on),
# daily spend, CTR, CPM, cost per order (WEB) or per conversation (WHATSAPP)
ADS: list[tuple[str, str, str, str, str | None, int, int | None, float, float, float, float]] = [
    (
        "120210000000001",
        "VitC Serum | Reel before-after",
        "Catalog sales | Skincare",
        "WEB",
        "Vitamin C Brightening Serum 30ml",
        -120,
        None,
        9.0,
        0.016,
        5.2,
        6.5,
    ),
    (
        "120210000000002",
        "Matte Lipstick | Carousel 4 shades",
        "Catalog sales | Makeup",
        "WEB",
        "Matte Lipstick",
        -90,
        None,
        6.0,
        0.006,
        4.8,
        24.0,
    ),
    (
        "120210000000003",
        "Foundation | Shade finder video",
        "Catalog sales | Makeup",
        "WEB",
        "Long-Wear Foundation 30ml",
        -60,
        None,
        8.0,
        0.014,
        5.5,
        30.0,
    ),
    (
        "120210000000004",
        "Collagen | Static benefits",
        "Catalog sales | Wellness",
        "WEB",
        "Collagen Powder 300g",
        -100,
        None,
        3.0,
        0.007,
        4.1,
        7.5,
    ),
    (
        "120210000000005",
        "SPF 50 | UGC test",
        "Catalog sales | Skincare",
        "WEB",
        "SPF 50 Daily Fluid 50ml",
        -3,
        None,
        7.0,
        0.012,
        5.0,
        10.0,
    ),
    (
        "120210000000006",
        "Retinol | Reel night routine",
        "Catalog sales | Skincare",
        "WEB",
        "Retinol Night Serum 30ml",
        -170,
        -110,
        7.0,
        0.011,
        5.3,
        11.0,
    ),
    (
        "120210000000007",
        "WhatsApp | Ask our advisor",
        "Messages | Advisor",
        "WHATSAPP",
        None,
        -150,
        None,
        5.0,
        0.021,
        4.4,
        1.5,
    ),
    (
        "120210000000008",
        "HA Gel Cream | Reel texture",
        "Catalog sales | Skincare",
        "WEB",
        "Hyaluronic Gel Cream 50ml",
        -75,
        None,
        6.5,
        0.013,
        5.1,
        9.0,
    ),
    (
        "120210000000009",
        "Argan Mask | Carousel",
        "Catalog sales | Hair",
        "WEB",
        "Argan Repair Mask 250ml",
        -45,
        None,
        4.0,
        0.009,
        4.6,
        22.0,
    ),
]

SELLERS = [
    ("seller_01", "0.2000", 5),
    ("seller_02", "0.1800", 4),
    ("seller_03", "0.1500", 3),
    ("seller_04", "0.2200", 2),
    ("seller_05", "0.2000", 1),
]

SETTINGS = [
    ("cpa_ceiling", "12.00", "Highest cost per sale the ads playbook accepts (USD)."),
    ("packing_cost_per_sale", "0.85", "Standard packing cost per sale. An estimate."),
    ("min_days_to_judge_ad", "5", "Learning phase: an ad with fewer delivery days is not judged."),
]

FIRST_NAMES = [
    "María",
    "Ana",
    "Daniela",
    "Andrea",
    "Gabriela",
    "Fernanda",
    "Valentina",
    "Camila",
    "Sofía",
    "Paola",
    "Diana",
    "Verónica",
    "Karla",
    "Nathaly",
    "Dayana",
    "Carmen",
    "Lucía",
    "Mónica",
    "Patricia",
    "Estefanía",
    "José",
    "Luis",
    "Jorge",
    "Carlos",
]
LAST_NAMES = [
    "Pérez",
    "González",
    "Rodríguez",
    "Sánchez",
    "Ramírez",
    "Torres",
    "Flores",
    "Rivera",
    "Gómez",
    "Díaz",
    "Vera",
    "Zambrano",
    "Mendoza",
    "Cedeño",
    "Moreira",
    "Bravo",
    "Andrade",
    "Villacís",
    "Ortiz",
    "Salazar",
    "Castro",
    "León",
]
CITIES = [
    ("Quito", 38),
    ("Guayaquil", 27),
    ("Cuenca", 10),
    ("Ambato", 5),
    ("Manta", 5),
    ("Loja", 4),
    ("Ibarra", 3),
    ("Riobamba", 3),
    ("Machala", 3),
    ("Esmeraldas", 1),
    ("Puyo", 1),
]
STREETS = [
    "Av. Amazonas",
    "Av. 6 de Diciembre",
    "Calle Larga",
    "Av. 9 de Octubre",
    "Av. Remigio Crespo",
    "Calle Bolívar",
    "Av. Shyris",
    "Calle Sucre",
]

WEEKDAY = [0.9, 0.95, 1.0, 1.0, 1.15, 1.2, 0.8]  # Monday first
SELLER_WEEKDAY = [1.1, 1.1, 1.1, 1.1, 1.15, 0.9, 0.3]
WEB_HOURS = [
    (h, w)
    for h, w in zip(
        range(7, 24), [1, 2, 3, 4, 4, 5, 6, 5, 4, 4, 5, 6, 8, 10, 11, 9, 6], strict=True
    )
]
SHOP_HOURS = [(h, w) for h, w in zip(range(9, 20), [3, 5, 6, 6, 5, 5, 6, 6, 5, 4, 2], strict=True)]


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _money(value: Decimal | float) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Variant:
    id: int
    product_id: int
    product: str
    label: str
    price: Decimal
    weight: float
    cost_ratio: float


class World:
    def __init__(self, anchor: date, cutoff: datetime, tz: ZoneInfo, seed: int):
        self.rng = random.Random(seed)
        self.anchor = anchor
        self.cutoff = cutoff
        self.tz = tz
        self.first_day = anchor - timedelta(days=DAYS - 1)
        self.rows: dict[str, list[tuple]] = {}
        self.ids: dict[str, int] = {}
        self.variants: list[Variant] = []
        self.by_product: dict[str, list[Variant]] = {}
        self.product_ids: dict[str, int] = {}
        self.customers: list[int] = []
        self.loyal: list[int] = []
        self.sold: dict[int, list[tuple[int, datetime]]] = {}  # variant -> (sale_line id, when)
        self.order_seq = 1000

    # -- helpers ---------------------------------------------------------------

    def add(self, table: str, row: tuple) -> None:
        self.rows.setdefault(table, []).append(row)

    def next_id(self, table: str) -> int:
        self.ids[table] = self.ids.get(table, 0) + 1
        return self.ids[table]

    def poisson(self, lam: float) -> int:
        if lam <= 0:
            return 0
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= limit:
                return k
            k += 1

    def pick(self, weighted: list[tuple]) -> object:
        total = sum(w for _, w in weighted)
        r = self.rng.uniform(0, total)
        for item, w in weighted:
            r -= w
            if r <= 0:
                return item
        return weighted[-1][0]

    def at(self, day: date, hours: list[tuple[int, int]]) -> datetime:
        hour = self.pick(hours)
        return datetime.combine(
            day, time(hour, self.rng.randrange(60), self.rng.randrange(60)), tzinfo=self.tz
        )

    def trend(self, day: date) -> float:
        return 0.8 + 0.4 * ((day - self.first_day).days / DAYS)

    def offset(self, day: date) -> int:
        return (day - self.anchor).days

    def in_stock(self, variant: Variant, day: date) -> bool:
        gone = STOCKOUTS.get(variant.label) or STOCKOUTS.get(variant.product)
        return gone is None or self.offset(day) < -gone

    # -- catalog -----------------------------------------------------------------

    def build_catalog(self) -> None:
        brands: dict[str, int] = {}
        for brand, tier, category, product, price, weight, names in CATALOG:
            if brand not in brands:
                brands[brand] = self.next_id("brand")
                self.add("brand", (brands[brand], brand, tier))
            pid = self.next_id("product")
            self.product_ids[product] = pid
            self.add("product", (pid, brands[brand], product, category))
            cost_ratio = self.rng.uniform(0.36, 0.55)
            for name in names or [None]:
                vid = self.next_id("product_variant")
                label = f"{product} / {name}" if name else product
                sku = f"{_ascii(brand)[:3].upper()}-{pid:03d}-{vid:03d}"
                share = weight / len(names) if names else weight
                v = Variant(vid, pid, product, label, Decimal(price), share, cost_ratio)
                self.variants.append(v)
                self.by_product.setdefault(product, []).append(v)
                self.add("product_variant", (vid, pid, sku, name, Decimal(price)))
        for username, rate, _ in SELLERS:
            self.add("seller", (username, Decimal(rate)))
        for key, value, note in SETTINGS:
            self.add("business_setting", (key, Decimal(value), note))

    def random_lines(self, day: date, first: Variant | None = None) -> list[Variant]:
        n = self.pick([(1, 55), (2, 28), (3, 12), (4, 5)])
        lines = [first] if first else []
        pool = [(v, v.weight) for v in self.variants if v.weight > 0 and self.in_stock(v, day)]
        while len(lines) < n:
            lines.append(self.pick(pool))
        return lines

    # -- people ------------------------------------------------------------------

    def new_customer(self, when: datetime, lead_source: str) -> int:
        cid = self.next_id("customer")
        first, last = self.rng.choice(FIRST_NAMES), self.rng.choice(LAST_NAMES)
        email = (
            f"{_ascii(first)}.{_ascii(last)}{cid}@example.com".lower()
            if self.rng.random() > 0.08
            else None
        )
        # +593 00... is not a valid Ecuadorian number: clearly fake on purpose.
        phone = f"+593 00{self.rng.randrange(1_000_000, 9_999_999)}"
        address = (
            f"{self.rng.choice(STREETS)} N{self.rng.randint(10, 80)}-"
            f"{self.rng.randint(10, 200)} y {self.rng.choice(STREETS)}"
        )
        national_id = "".join(str(self.rng.randint(0, 9)) for _ in range(10))
        self.add(
            "customer",
            (
                cid,
                f"{first} {last}",
                email,
                phone,
                self.pick(CITIES),
                address,
                national_id,
                lead_source,
                when,
            ),
        )
        self.customers.append(cid)
        if self.rng.random() < 0.2:
            self.loyal.append(cid)
        return cid

    def customer_for(self, when: datetime, returning: float, lead_source: str) -> int:
        if self.customers and self.rng.random() < returning:
            pool = self.loyal if self.loyal and self.rng.random() < 0.6 else self.customers
            return self.rng.choice(pool)
        return self.new_customer(when, lead_source)

    # -- money -------------------------------------------------------------------

    def add_sale(
        self,
        when: datetime,
        channel: str,
        customer: int | None,
        seller: str | None,
        lines: list[Variant],
        discount_rate: float = 0.0,
        internal: bool = False,
    ) -> int:
        sid = self.next_id("sale")
        subtotal = sum((v.price for v in lines), Decimal(0))
        discount = _money(subtotal * Decimal(str(discount_rate)))
        shipping = (
            SHIPPING_FEE
            if channel in ("WEB", "WHATSAPP") and subtotal - discount < FREE_SHIPPING_FROM
            else Decimal(0)
        )
        total = subtotal - discount + shipping
        self.add(
            "sale",
            (sid, when, channel, customer, seller, subtotal, discount, shipping, total, internal),
        )
        for v in lines:
            lid = self.next_id("sale_line")
            self.sold.setdefault(v.id, []).append((lid, when))
            self.add("sale_line_pending", (lid, sid, v.id, v.price))
        self.add_payments(sid, when, channel, total)
        return sid

    def add_payments(self, sale_id: int, when: datetime, channel: str, total: Decimal) -> None:
        day = when.astimezone(self.tz).date()
        if channel == "CONSIGNMENT":
            plan = [(day + timedelta(days=self.rng.randint(20, 40)), total, "TRANSFER")]
        elif channel == "SELLER" and self.rng.random() < 0.15:
            half = _money(total / 2)
            plan = [(day, half, "TRANSFER"), (day + timedelta(days=15), total - half, "TRANSFER")]
        else:
            methods = {
                "WEB": [("CARD", 60), ("TRANSFER", 40)],
                "WHATSAPP": [("TRANSFER", 75), ("CARD", 25)],
                "SELLER": [("CASH", 40), ("TRANSFER", 60)],
            }[channel]
            plan = [(day, total, self.pick(methods))]
        for paid_at, amount, method in plan:
            if paid_at > self.anchor:
                continue  # not collected yet
            fee = _money(amount * Decimal("0.045") + Decimal("0.25")) if method == "CARD" else 0
            self.add(
                "sale_payment",
                (self.next_id("sale_payment"), sale_id, paid_at, method, amount, Decimal(fee)),
            )

    # -- orders --------------------------------------------------------------------

    def add_order(
        self,
        when: datetime,
        source: str,
        status: str,
        customer: int,
        total: Decimal,
        sale_id: int | None,
        utm: tuple,
        landing: str | None,
        conversation: int | None = None,
        code: str | None = None,
        discount: Decimal = Decimal(0),
        internal: bool = False,
    ) -> None:
        self.order_seq += 1
        city = self.city_of(customer)
        self.add(
            "shop_order",
            (
                self.next_id("shop_order"),
                f"AUR-{self.order_seq}",
                when,
                source,
                status,
                customer,
                conversation,
                city,
                f"{self.rng.choice(STREETS)} {self.rng.randint(100, 999)}",
                total,
                discount,
                code,
                sale_id,
                *utm,
                landing,
                internal,
            ),
        )

    def city_of(self, customer: int) -> str:
        return self.rows["customer"][customer - 1][4]

    def web_orders(self, day: date, ads_today: list[tuple]) -> list[tuple[datetime, Variant]]:
        """Paid and unpaid web orders of one day. Returns what the funnel needs."""
        cyber = -62 <= self.offset(day) <= -60
        created: list[tuple[datetime, Variant]] = []

        def place(variant_first: Variant | None, utm: tuple, ad_product: str | None) -> bool:
            """One web order. True when it was paid and became a sale."""
            when = self.at(day, WEB_HOURS)
            if when > self.cutoff:
                return False
            lines = self.random_lines(day, variant_first)
            cid = self.customer_for(when, 0.35, "META_ADS" if utm[3] else "ORGANIC")
            rate, code = (0.15, "CYBER15") if cyber and self.rng.random() < 0.8 else (0.0, None)
            if code is None and self.rng.random() < 0.12:
                rate, code = 0.10, "WELCOME10"
            total_est = sum((v.price for v in lines), Decimal(0))
            landing = f"/p/{lines[0].id}"
            created.append((when, lines[0]))
            if ad_product and not self.in_stock(lines[0], day):
                # The ad keeps sending people to a product nobody can buy.
                self.add_order(when, "WEB", "CANCELLED", cid, total_est, None, utm, landing)
                return False
            sid = self.add_sale(when, "WEB", cid, None, lines, rate)
            sale = self.rows["sale"][-1]
            self.add_order(
                when - timedelta(minutes=self.rng.randint(2, 30)),
                "WEB",
                "PAID",
                cid,
                sale[8],
                sid,
                utm,
                landing,
                code=code,
                discount=sale[6],
            )
            return True

        for ad in ads_today:
            ad_id, _, campaign, destination, product, *_rest, cpa = ad
            if destination != "WEB":
                continue
            spend = self.ad_spend_today[ad_id]
            for _ in range(self.poisson(spend / cpa * (1.8 if cyber else 1.0))):
                variant = self.rng.choice(self.by_product[product])
                source = self.rng.choice(["facebook", "instagram"])
                slug = campaign.lower().replace(" | ", "-").replace(" ", "-")
                utm = (source, "paid_social", slug, ad_id)
                if place(variant, utm, product):
                    self.ad_orders[ad_id] = self.ad_orders.get(ad_id, 0) + 1

        boost = 2.5 if cyber else 1.0
        organic = self.poisson(1.6 * self.trend(day) * WEEKDAY[day.weekday()] * boost)
        for _ in range(organic):
            utm = self.pick(
                [
                    ((None, None, None, None), 40),
                    (("instagram", "bio", None, None), 25),
                    (("google", "organic", None, None), 20),
                    (("whatsapp", "share", None, None), 15),
                ]
            )
            place(None, utm, None)

        # Orders that never got paid.
        for _ in range(self.poisson(1.2 * self.trend(day))):
            when = self.at(day, WEB_HOURS)
            if when > self.cutoff:
                continue
            lines = self.random_lines(day)
            cid = self.customer_for(when, 0.3, "ORGANIC")
            status = (
                "PENDING_PAYMENT"
                if self.offset(day) >= -1
                else self.pick([("EXPIRED", 70), ("CANCELLED", 30)])
            )
            self.add_order(
                when,
                "WEB",
                status,
                cid,
                sum((v.price for v in lines), Decimal(0)),
                None,
                (None, None, None, None),
                f"/p/{lines[0].id}",
            )
            created.append((when, lines[0]))
        return created

    # -- WhatsApp bot ----------------------------------------------------------------

    def conversations(self, day: date, ads_today: list[tuple]) -> None:
        organic = self.poisson(9 * self.trend(day) * WEEKDAY[day.weekday()])
        starts: list[str | None] = [None] * organic
        for ad in ads_today:
            if ad[3] == "WHATSAPP":
                starts += [ad[0]] * self.poisson(self.ad_spend_today[ad[0]] / ad[10])
        for ad_id in starts:
            when = self.at(day, WEB_HOURS)
            if when > self.cutoff:
                continue
            conv = self.next_id("conversation")
            cid = self.customer_for(when, 0.4, "META_ADS" if ad_id else "WHATSAPP")
            recent = self.offset(day) >= -1
            # How far this chat gets, step by step. A step that would happen
            # after the cutoff has not happened yet.
            chances = [
                ("BAG_ADDED", 0.75 * (0.45 if ad_id else 0.38)),
                ("CHECKOUT_STARTED", 0.7),
                ("ORDER_CREATED", 0.8),
                ("ORDER_PAID", 0.39 if recent else 0.78),
            ]
            reached: dict[str, datetime] = {}
            t = when
            for event, chance in chances:
                if self.rng.random() >= chance:
                    break
                t += timedelta(minutes=self.rng.randint(2, 40))
                if t > self.cutoff:
                    break
                reached[event] = t
                self.add("conversation_event", (self.next_id("conversation_event"), conv, t, event))
            engaged = bool(reached) or self.rng.random() < 0.6
            bag, ordered, paid = (
                "BAG_ADDED" in reached,
                "ORDER_CREATED" in reached,
                "ORDER_PAID" in reached,
            )
            handoff = self.rng.random() < 0.12
            escalated = None
            if handoff:
                escalated = when + timedelta(minutes=self.rng.randint(1, 20))
                self.add(
                    "conversation_event",
                    (self.next_id("conversation_event"), conv, escalated, "HANDOFF"),
                )
            stage = (
                "CUSTOMER"
                if paid
                else "CART"
                if bag
                else "ENGAGED"
                if engaged
                else "NEW"
                if recent
                else "LOST"
            )
            self.add("conversation", (conv, when, cid, stage, escalated, ad_id))
            if ordered:
                lines = self.random_lines(day)
                ordered_at = reached["ORDER_CREATED"]
                if paid:
                    sid = self.add_sale(reached["ORDER_PAID"], "WHATSAPP", cid, None, lines)
                    sale = self.rows["sale"][-1]
                    self.add_order(
                        ordered_at,
                        "BOT",
                        "PAID",
                        cid,
                        sale[8],
                        sid,
                        ("whatsapp", "bot", None, ad_id),
                        None,
                        conversation=conv,
                    )
                else:
                    status = "PENDING_PAYMENT" if recent else "EXPIRED"
                    self.add_order(
                        ordered_at,
                        "BOT",
                        status,
                        cid,
                        sum((v.price for v in lines), Decimal(0)),
                        None,
                        ("whatsapp", "bot", None, ad_id),
                        None,
                        conversation=conv,
                    )

    # -- the day loop ------------------------------------------------------------------

    def simulate(self) -> None:
        self.ad_orders: dict[str, int] = {}
        day = self.first_day
        while day <= self.anchor:
            ads_today = [
                a
                for a in ADS
                if a[5] <= self.offset(day) and (a[6] is None or self.offset(day) <= a[6])
            ]
            self.ad_spend_today = {
                a[0]: max(0.5, self.rng.gauss(a[7], a[7] * 0.15)) for a in ads_today
            }
            before = dict(self.ad_orders)
            created = self.web_orders(day, ads_today)
            self.conversations(day, ads_today)
            self.seller_sales(day)
            self.internal_activity(day)
            self.funnel_events(day, created)
            if day < self.anchor:  # Meta reports a day once it is over
                self.ad_insights(day, ads_today, before)
            day += timedelta(days=1)

    def seller_sales(self, day: date) -> None:
        sellers = [(u, w) for u, _, w in SELLERS]
        for channel, base in (("SELLER", 4.6), ("CONSIGNMENT", 1.0)):
            for _ in range(self.poisson(base * self.trend(day) * SELLER_WEEKDAY[day.weekday()])):
                when = self.at(day, SHOP_HOURS)
                if when > self.cutoff:
                    continue
                cid = self.customer_for(when, 0.6, self.pick([("SELLER", 80), ("REFERRAL", 20)]))
                self.add_sale(when, channel, cid, self.pick(sellers), self.random_lines(day))

    def internal_activity(self, day: date) -> None:
        """Staff testing the checkout. Flagged, so every tool can ignore it."""
        if day.weekday() not in (1, 3):
            return
        when = self.at(day, SHOP_HOURS)
        if when > self.cutoff:
            return
        lines = self.random_lines(day)[:1]
        sid = self.add_sale(when, "WEB", None, None, lines, internal=True)
        self.order_seq += 1
        self.add(
            "shop_order",
            (
                self.next_id("shop_order"),
                f"AUR-{self.order_seq}",
                when,
                "WEB",
                "PAID",
                None,
                None,
                "Quito",
                None,
                self.rows["sale"][-1][8],
                Decimal(0),
                None,
                sid,
                "qa",
                None,
                None,
                None,
                "/p/test",
                True,
            ),
        )
        for name in ("VIEW_PRODUCT", "ADD_TO_CART", "BEGIN_CHECKOUT", "ORDER_CREATED"):
            self.add(
                "web_event", (self.next_id("web_event"), when, "qa-staff", name, lines[0].id, True)
            )

    def funnel_events(self, day: date, created: list[tuple[datetime, Variant]]) -> None:
        """Storefront events consistent with the orders of the day."""
        visitors = [f"v{day.toordinal()}-{i}" for i in range(max(30, len(created) * 22))]

        def emit(name: str, variant: Variant, when: datetime | None = None) -> None:
            when = when or self.at(day, WEB_HOURS)
            if when <= self.cutoff:
                self.add(
                    "web_event",
                    (
                        self.next_id("web_event"),
                        when,
                        self.rng.choice(visitors),
                        name,
                        variant.id,
                        False,
                    ),
                )

        for when, variant in created:
            emit("ORDER_CREATED", variant, when)
        sellable = [(v, v.weight) for v in self.variants if v.weight > 0]
        checkouts = [v for _, v in created] + [
            self.pick(sellable) for _ in range(round(len(created) * 0.7))
        ]
        for v in checkouts:
            emit("BEGIN_CHECKOUT", v)
        carts = checkouts + [self.pick(sellable) for _ in range(round(len(checkouts) * 1.2))]
        for v in carts:
            emit("ADD_TO_CART", v)
        view_weights = [
            (v, v.weight**0.7 + (6 if v.product == WINDOW_SHOPPING else 0.4)) for v in self.variants
        ]
        for _ in range(round(len(carts) * 11 + 25)):
            emit("VIEW_PRODUCT", self.pick(view_weights))

    def ad_insights(self, day: date, ads_today: list[tuple], before: dict[str, int]) -> None:
        for ad in ads_today:
            ad_id, destination, ctr, cpm = ad[0], ad[3], ad[8], ad[9]
            spend = _money(self.ad_spend_today[ad_id])
            impressions = int(float(spend) / cpm * 1000)
            clicks = max(0, round(impressions * ctr * self.rng.uniform(0.85, 1.15)))
            ours = self.ad_orders.get(ad_id, 0) - before.get(ad_id, 0)
            # Meta only sees buyers who accepted cookies, and never sees a chat sale.
            meta = (
                0
                if destination == "WHATSAPP"
                else sum(1 for _ in range(ours) if self.rng.random() < 0.65)
            )
            self.add("ad_insight_daily", (ad_id, day, spend, impressions, clicks, meta))

    # -- inventory, decided after the fact so every sold unit existed ----------------------

    def build_inventory(self) -> None:
        recent_from = self.anchor - timedelta(days=30)
        lines: list[tuple] = []
        for v in self.variants:
            sales = sorted(self.sold.get(v.id, []), key=lambda s: s[1])
            recent = sum(1 for _, when in sales if when.astimezone(self.tz).date() >= recent_from)
            daily = recent / 30
            if v.label in STOCKOUTS or v.product in STOCKOUTS:
                on_hand = 0
            elif v.product == WINDOW_SHOPPING:
                on_hand = 8
            elif v.label in RUNNING_LOW or v.product in RUNNING_LOW:
                on_hand = max(1, round(daily * self.rng.uniform(3, 9)))
            else:
                on_hand = max(4, math.ceil(daily * self.rng.uniform(25, 60)))
            total = len(sales) + on_hand
            # Units arrive in shipments. Every unit of a shipment shares its
            # cost and its arrival date, and costs creep up shipment after
            # shipment, which is why an average cost would lie.
            batch_size = max(6, math.ceil(total / 3))
            n_batches = math.ceil(total / batch_size) if total else 0
            uncosted_last = self.rng.random() < 0.1
            batches = []
            for b in range(n_batches):
                cost = _money(
                    float(v.price) * v.cost_ratio * (1 + 0.04 * b) * self.rng.uniform(0.97, 1.03)
                )
                freight = _money(float(cost) * self.rng.uniform(0.07, 0.14))
                if uncosted_last and b == n_batches - 1:
                    freight = None  # the newest shipment is not costed yet
                start = b * batch_size
                opens = (
                    sales[start][1]
                    if start < len(sales)
                    else datetime.combine(self.anchor - timedelta(days=20), time(9), tzinfo=self.tz)
                )
                batches.append((cost, freight, opens - timedelta(days=self.rng.randint(3, 12))))
            for i in range(total):
                cost, freight, received = batches[i // batch_size]
                uid = self.next_id("inventory_unit")
                if i < len(sales):
                    line_id, _ = sales[i]
                    self.add("inventory_unit", (uid, v.id, "SOLD", cost, freight, received))
                    lines.append((line_id, uid))
                else:
                    self.add("inventory_unit", (uid, v.id, "AVAILABLE", cost, freight, received))
            for _ in range(IN_TRANSIT.get(v.label, 0)):
                cost = _money(float(v.price) * v.cost_ratio * 1.1)
                self.add(
                    "inventory_unit",
                    (self.next_id("inventory_unit"), v.id, "IN_TRANSIT", cost, None, None),
                )
        unit_of = dict(lines)
        for line_id, sale_id, _variant, price in self.rows.pop("sale_line_pending", []):
            self.add("sale_line", (line_id, sale_id, unit_of[line_id], price))

    def build_ads(self) -> None:
        for ad_id, name, campaign, destination, product, _f, last, *_ in ADS:
            status = "PAUSED" if last is not None else "ACTIVE"
            self.add(
                "ad",
                (
                    ad_id,
                    name,
                    campaign,
                    destination,
                    self.product_ids.get(product) if product else None,
                    status,
                ),
            )


COLUMNS = {
    "brand": ["id", "name", "tier"],
    "product": ["id", "brand_id", "name", "category"],
    "product_variant": ["id", "product_id", "sku", "variant_name", "public_price"],
    "seller": ["username", "commission_rate"],
    "business_setting": ["key", "value", "note"],
    "customer": [
        "id",
        "full_name",
        "email",
        "phone",
        "city",
        "address_text",
        "national_id",
        "lead_source",
        "created_at",
    ],
    "inventory_unit": [
        "id",
        "variant_id",
        "status",
        "acquisition_unit_cost",
        "import_cost",
        "received_at",
    ],
    "sale": [
        "id",
        "sold_at",
        "channel",
        "customer_id",
        "seller_username",
        "subtotal",
        "discount_total",
        "shipping_fee",
        "total_amount",
        "is_internal",
    ],
    "sale_line": ["id", "sale_id", "inventory_unit_id", "unit_sale_price"],
    "sale_payment": ["id", "sale_id", "paid_at", "method", "amount", "gateway_fee"],
    "ad": ["id", "name", "campaign_name", "destination", "product_id", "status"],
    "ad_insight_daily": ["ad_id", "day", "spend", "impressions", "clicks", "meta_purchases"],
    "conversation": ["id", "created_at", "customer_id", "lead_stage", "escalated_at", "from_ad_id"],
    "conversation_event": ["id", "conversation_id", "created_at", "event_type"],
    "shop_order": [
        "id",
        "order_number",
        "created_at",
        "source",
        "status",
        "customer_id",
        "conversation_id",
        "delivery_city",
        "delivery_address",
        "total",
        "discount_amount",
        "discount_code",
        "sale_id",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "landing_path",
        "is_internal",
    ],
    "web_event": ["id", "created_at", "visitor_id", "event_name", "variant_id", "is_internal"],
}
# Parents before children.
LOAD_ORDER = [
    "brand",
    "product",
    "product_variant",
    "seller",
    "business_setting",
    "customer",
    "inventory_unit",
    "sale",
    "sale_line",
    "sale_payment",
    "ad",
    "ad_insight_daily",
    "conversation",
    "conversation_event",
    "shop_order",
    "web_event",
]


def generate(
    anchor: date, tz: ZoneInfo, cutoff: datetime | None = None, seed: int = SEED
) -> dict[str, list[tuple]]:
    """Build every row in memory. Pure: no database involved."""
    cutoff = cutoff or datetime.combine(anchor, time(12), tzinfo=tz)
    world = World(anchor, cutoff, tz, seed)
    world.build_catalog()
    world.build_ads()
    world.simulate()
    world.build_inventory()
    return {table: world.rows.get(table, []) for table in LOAD_ORDER}


async def load(conn: asyncpg.Connection, rows: dict[str, list[tuple]]) -> dict[str, int]:
    """Copy the rows in and move every sequence past the ids we chose."""
    counts = {}
    for table in LOAD_ORDER:
        records = rows[table]
        if records:
            await conn.copy_records_to_table(
                table, schema_name="store", records=records, columns=COLUMNS[table]
            )
        counts[table] = len(records)
        if COLUMNS[table][0] == "id" and table not in ("ad",):
            await conn.execute(
                f"SELECT setval(pg_get_serial_sequence('store.{table}', 'id'), "
                f"(SELECT coalesce(max(id), 0) + 1 FROM store.{table}), false)"
            )
    return counts
