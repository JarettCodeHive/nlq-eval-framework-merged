-- Sales DDL - Contract v1.0
--
-- Conformance: ANSI SQL, intended to lint clean under
-- `sqlfluff lint --dialect ansi`.
-- Runs unmodified in DuckDB 0.10+.
--
-- Source ERD: schemas/sales/sales_er.dbml
-- CSV contract: schemas/sales/sales_csv_header_spec.md
--
-- Currency policy: Sales is USD-only. Mixed-currency aggregation and FX
-- conversion belong to the Finance domain.
--
-- NOT-NULL rules: never nullify a PK or INNER-JOIN FK.
-- INNER-JOIN FKs: deals.lead_id, quotations.deal_id,
-- quotations.product_id.
-- Nullable analytical fields: deals.close_date, products.list_price.
--
-- Explicit many-to-many bridge: quotations links deals to products while
-- preserving line-item attributes and duplicate deal-line imperfections.
--
-- Quota attainment uses an analytical string-key/date-range relationship:
-- deals.rep_name = targets.rep_name
-- AND deals.close_date >= targets.period_start
-- AND deals.close_date < targets.period_end
-- This is intentionally documented rather than enforced as a foreign key
-- because each rep has multiple target periods.

CREATE TABLE leads (
    lead_id INTEGER NOT NULL,
    lead_name VARCHAR(255) NOT NULL,
    company_name VARCHAR(255) NOT NULL,
    lead_source VARCHAR(32) NOT NULL,
    lead_status VARCHAR(32) NOT NULL,
    rep_name VARCHAR(255) NOT NULL,
    territory VARCHAR(64),
    score INTEGER,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_leads PRIMARY KEY (lead_id)
);

CREATE TABLE deals (
    deal_id INTEGER NOT NULL,
    lead_id INTEGER NOT NULL,
    deal_name VARCHAR(255) NOT NULL,
    rep_name VARCHAR(255) NOT NULL,
    stage VARCHAR(32) NOT NULL, -- noqa: RF04
    deal_amount DECIMAL(15, 2) NOT NULL,
    currency_code CHAR(3) NOT NULL DEFAULT 'USD' CHECK (currency_code = 'USD'),
    close_date DATE,
    expected_close_date DATE,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_deals PRIMARY KEY (deal_id),
    CONSTRAINT fk_deals_lead
    FOREIGN KEY (lead_id) REFERENCES leads (lead_id)
);

CREATE TABLE products (
    product_id INTEGER NOT NULL,
    sku VARCHAR(64) NOT NULL,
    product_name VARCHAR(255) NOT NULL,
    category VARCHAR(64),
    list_price DECIMAL(15, 2),
    currency_code CHAR(3) NOT NULL DEFAULT 'USD' CHECK (currency_code = 'USD'),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_products PRIMARY KEY (product_id)
);

CREATE TABLE quotations (
    quotation_id INTEGER NOT NULL,
    deal_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quote_number VARCHAR(64) NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price DECIMAL(15, 2) NOT NULL,
    discount_pct DECIMAL(5, 2) NOT NULL DEFAULT 0,
    quote_status VARCHAR(32) NOT NULL,
    quoted_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_quotations PRIMARY KEY (quotation_id),
    CONSTRAINT fk_quotations_deal
    FOREIGN KEY (deal_id) REFERENCES deals (deal_id),
    CONSTRAINT fk_quotations_product
    FOREIGN KEY (product_id) REFERENCES products (product_id)
);

CREATE TABLE targets (
    target_id INTEGER NOT NULL,
    rep_name VARCHAR(255) NOT NULL,
    territory VARCHAR(64),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    quota_amount DECIMAL(15, 2) NOT NULL,
    currency_code CHAR(3) NOT NULL DEFAULT 'USD' CHECK (currency_code = 'USD'),
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_targets PRIMARY KEY (target_id)
);
