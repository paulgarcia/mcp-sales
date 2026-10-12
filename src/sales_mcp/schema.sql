-- Demo schema for the sales MCP server.
--
-- A simplified, English copy of the production tables that the MCP tools read.
-- The production system has ~190 migrations; this keeps only what the tools
-- need, with the same shapes and the same traps.
--
-- Conventions:
--   * Money is numeric(12,2). Never float.
--   * Every instant is timestamptz, stored in UTC. "Days" are local days in the
--     business time zone (America/Guayaquil) and are computed at query time.
--   * is_internal marks staff/QA activity. Every tool excludes it by default,
--     so nobody reads their own testing as business.

CREATE SCHEMA IF NOT EXISTS store;

CREATE TABLE store.brand (
    id    bigserial PRIMARY KEY,
    name  text NOT NULL UNIQUE,
    tier  text NOT NULL DEFAULT 'MID' CHECK (tier IN ('VALUE', 'MID', 'PREMIUM'))
);

CREATE TABLE store.product (
    id        bigserial PRIMARY KEY,
    brand_id  bigint REFERENCES store.brand (id),
    name      text NOT NULL,
    category  text NOT NULL
              CHECK (category IN ('SKINCARE', 'MAKEUP', 'SUPPLEMENTS', 'HAIRCARE', 'FRAGRANCE'))
);

CREATE TABLE store.product_variant (
    id            bigserial PRIMARY KEY,
    product_id    bigint NOT NULL REFERENCES store.product (id),
    sku           text NOT NULL UNIQUE,
    variant_name  text,                       -- shade or size; NULL when there is only one
    public_price  numeric(12, 2) NOT NULL CHECK (public_price > 0)
);

-- The store tracks every physical unit, so the cost of a sale is the REAL landed
-- cost of the unit that left the shelf, never an average.
CREATE TABLE store.inventory_unit (
    id                     bigserial PRIMARY KEY,
    variant_id             bigint NOT NULL REFERENCES store.product_variant (id),
    status                 text NOT NULL
                           CHECK (status IN ('IN_TRANSIT', 'AVAILABLE', 'RESERVED', 'SOLD')),
    acquisition_unit_cost  numeric(12, 2) NOT NULL,
    import_cost            numeric(12, 2),    -- freight + duties; NULL until the shipment is costed
    received_at            timestamptz
);
CREATE INDEX ON store.inventory_unit (variant_id, status);

CREATE TABLE store.customer (
    id            bigserial PRIMARY KEY,
    full_name     text NOT NULL,
    email         text,
    phone         text,
    city          text,
    address_text  text,                       -- needed for deliveries; the MCP never returns it
    national_id   text,                       -- needed for invoices; the MCP never returns it
    lead_source   text NOT NULL
                  CHECK (lead_source IN ('META_ADS', 'WHATSAPP', 'INSTAGRAM', 'SELLER', 'REFERRAL', 'ORGANIC')),
    created_at    timestamptz NOT NULL
);

CREATE TABLE store.seller (
    username         text PRIMARY KEY,
    commission_rate  numeric(5, 4) NOT NULL   -- a FRACTION: 0.2000 means 20 %
);

-- One row per sale. The channel decides what Meta can see: only WEB sales send
-- a Purchase event, so comparing the store total against Ads Manager without
-- splitting by channel never reconciles.
CREATE TABLE store.sale (
    id               bigserial PRIMARY KEY,
    sold_at          timestamptz NOT NULL,
    channel          text NOT NULL CHECK (channel IN ('WEB', 'WHATSAPP', 'SELLER', 'CONSIGNMENT')),
    customer_id      bigint REFERENCES store.customer (id),
    seller_username  text REFERENCES store.seller (username),
    subtotal         numeric(12, 2) NOT NULL,
    discount_total   numeric(12, 2) NOT NULL DEFAULT 0,
    shipping_fee     numeric(12, 2) NOT NULL DEFAULT 0,
    total_amount     numeric(12, 2) NOT NULL,  -- subtotal - discount + shipping
    is_internal      boolean NOT NULL DEFAULT false
);
CREATE INDEX ON store.sale (sold_at);

CREATE TABLE store.sale_line (
    id                 bigserial PRIMARY KEY,
    sale_id            bigint NOT NULL REFERENCES store.sale (id),
    inventory_unit_id  bigint NOT NULL UNIQUE REFERENCES store.inventory_unit (id),
    unit_sale_price    numeric(12, 2) NOT NULL
);
CREATE INDEX ON store.sale_line (sale_id);

-- Money that actually came in. A credit sale is paid in installments, so
-- "sold in a range" and "collected in a range" are different numbers.
CREATE TABLE store.sale_payment (
    id           bigserial PRIMARY KEY,
    sale_id      bigint NOT NULL REFERENCES store.sale (id),
    paid_at      date NOT NULL,               -- a local calendar day, not an instant
    method       text NOT NULL CHECK (method IN ('TRANSFER', 'CARD', 'CASH')),
    amount       numeric(12, 2) NOT NULL,
    gateway_fee  numeric(12, 2) NOT NULL DEFAULT 0   -- what the card processor kept
);
CREATE INDEX ON store.sale_payment (paid_at);

-- Meta ads. A WEB ad links to the catalog with utm_content={{ad.id}}; a
-- WHATSAPP ad opens a chat with the bot (click-to-WhatsApp).
CREATE TABLE store.ad (
    id             text PRIMARY KEY,          -- the Meta ad id
    name           text NOT NULL,
    campaign_name  text NOT NULL,
    destination    text NOT NULL CHECK (destination IN ('WEB', 'WHATSAPP')),
    product_id     bigint REFERENCES store.product (id),
    status         text NOT NULL CHECK (status IN ('ACTIVE', 'PAUSED'))
);

-- Daily numbers exactly as Meta reports them.
CREATE TABLE store.ad_insight_daily (
    ad_id           text NOT NULL REFERENCES store.ad (id),
    day             date NOT NULL,
    spend           numeric(12, 2) NOT NULL,
    impressions     integer NOT NULL,
    clicks          integer NOT NULL,
    meta_purchases  integer NOT NULL DEFAULT 0,  -- only what Meta could see (cookie consent)
    PRIMARY KEY (ad_id, day)
);
CREATE INDEX ON store.ad_insight_daily (day);

-- WhatsApp bot conversations (case study 2 of the portfolio).
CREATE TABLE store.conversation (
    id            bigserial PRIMARY KEY,
    created_at    timestamptz NOT NULL,
    customer_id   bigint REFERENCES store.customer (id),
    lead_stage    text NOT NULL CHECK (lead_stage IN ('NEW', 'ENGAGED', 'CART', 'CUSTOMER', 'LOST')),
    escalated_at  timestamptz,                -- handed to a human seller
    from_ad_id    text REFERENCES store.ad (id)   -- a click-to-WhatsApp ad started it
);
CREATE INDEX ON store.conversation (created_at);

CREATE TABLE store.conversation_event (
    id               bigserial PRIMARY KEY,
    conversation_id  bigint NOT NULL REFERENCES store.conversation (id),
    created_at       timestamptz NOT NULL,
    event_type       text NOT NULL
                     CHECK (event_type IN ('BAG_ADDED', 'CHECKOUT_STARTED', 'ORDER_CREATED', 'ORDER_PAID', 'HANDOFF'))
);
CREATE INDEX ON store.conversation_event (conversation_id);
CREATE INDEX ON store.conversation_event (created_at);

-- Online orders from the web catalog and from the WhatsApp bot. An order
-- becomes a sale once it is paid; sale_id stays NULL until then.
CREATE TABLE store.shop_order (
    id                bigserial PRIMARY KEY,
    order_number      text NOT NULL UNIQUE,
    created_at        timestamptz NOT NULL,
    source            text NOT NULL CHECK (source IN ('WEB', 'BOT')),
    status            text NOT NULL CHECK (status IN ('PENDING_PAYMENT', 'PAID', 'CANCELLED', 'EXPIRED')),
    customer_id       bigint REFERENCES store.customer (id),
    conversation_id   bigint REFERENCES store.conversation (id),  -- set for BOT orders
    delivery_city     text,
    delivery_address  text,                   -- never returned by the MCP
    total             numeric(12, 2) NOT NULL,
    discount_amount   numeric(12, 2) NOT NULL DEFAULT 0,
    discount_code     text,
    sale_id           bigint REFERENCES store.sale (id),
    utm_source        text,
    utm_medium        text,
    utm_campaign      text,
    utm_content       text,                   -- the ad id, written into the ad's own link
    landing_path      text,
    is_internal       boolean NOT NULL DEFAULT false
);
CREATE INDEX ON store.shop_order (created_at);
CREATE INDEX ON store.shop_order (utm_content);

-- First-party storefront events. Measured by the store itself, so they do not
-- depend on anyone accepting cookies.
CREATE TABLE store.web_event (
    id           bigserial PRIMARY KEY,
    created_at   timestamptz NOT NULL,
    visitor_id   text NOT NULL,
    event_name   text NOT NULL
                 CHECK (event_name IN ('VIEW_PRODUCT', 'ADD_TO_CART', 'BEGIN_CHECKOUT', 'ORDER_CREATED')),
    variant_id   bigint REFERENCES store.product_variant (id),
    is_internal  boolean NOT NULL DEFAULT false
);
CREATE INDEX ON store.web_event (created_at);

-- Numbers the business decides, not the data: the CPA ceiling from the ads
-- playbook, the packing cost per sale, and so on.
CREATE TABLE store.business_setting (
    key    text PRIMARY KEY,
    value  numeric(12, 4) NOT NULL,
    note   text
);
