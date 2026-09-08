-- CRM DDL - Draft v0.2
--
-- Conformance: ANSI SQL, linted with `sqlfluff lint --dialect ansi`.
-- Target runtime: DuckDB 0.10.3.
--
-- Domain boundary: CRM covers customer engagement, campaign attribution,
-- support-case resolution, and SLA behavior. Sales pipeline and revenue
-- concepts are intentionally excluded.
--
-- Currency policy: CRM is USD-only. Mixed-currency and FX behavior belongs
-- to the Finance domain.
--
-- NOT-NULL rules: PKs and INNER-JOIN FKs are never nullable. Optional
-- account, contact, and campaign relationships remain nullable to support
-- the declared LEFT JOIN paths.

CREATE TABLE accounts (
    account_id INTEGER NOT NULL,
    account_name VARCHAR(255) NOT NULL,
    account_size INTEGER,
    industry VARCHAR(64),
    region VARCHAR(64),
    customer_tier VARCHAR(32),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_accounts PRIMARY KEY (account_id)
);

CREATE TABLE contacts (
    contact_id INTEGER NOT NULL,
    account_id INTEGER,
    first_name VARCHAR(128) NOT NULL,
    last_name VARCHAR(128) NOT NULL,
    email VARCHAR(255) NOT NULL,
    address VARCHAR(255),
    title VARCHAR(128),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_contacts PRIMARY KEY (contact_id),
    CONSTRAINT fk_contacts_account
    FOREIGN KEY (account_id) REFERENCES accounts (account_id)
);

CREATE TABLE campaigns (
    campaign_id INTEGER NOT NULL,
    campaign_name VARCHAR(255) NOT NULL,
    campaign_type VARCHAR(64) NOT NULL,
    primary_channel VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE CHECK (end_date >= start_date),
    budget_amount DECIMAL(15, 2),
    currency_code CHAR(3) NOT NULL DEFAULT 'USD' CHECK (currency_code = 'USD'),
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_campaigns PRIMARY KEY (campaign_id)
);

CREATE TABLE contact_campaigns (
    contact_id INTEGER NOT NULL,
    campaign_id INTEGER NOT NULL,
    member_status VARCHAR(32) NOT NULL,
    first_touch_at TIMESTAMP,
    last_touch_at TIMESTAMP CHECK (last_touch_at >= first_touch_at),
    attribution_weight DECIMAL(5, 4) CHECK (attribution_weight BETWEEN 0.0000 AND 1.0000), -- noqa: LT05
    is_primary_attribution BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_contact_campaigns PRIMARY KEY (
        contact_id,
        campaign_id
    ),
    CONSTRAINT fk_contact_campaigns_contact
    FOREIGN KEY (contact_id) REFERENCES contacts (contact_id),
    CONSTRAINT fk_contact_campaigns_campaign
    FOREIGN KEY (campaign_id) REFERENCES campaigns (campaign_id)
);

CREATE TABLE interactions (
    interaction_id INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    account_id INTEGER,
    campaign_id INTEGER,
    engagement_type VARCHAR(64) NOT NULL,
    channel VARCHAR(32) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    engagement_points INTEGER NOT NULL CHECK (engagement_points >= 0),
    interaction_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_interactions PRIMARY KEY (interaction_id),
    CONSTRAINT fk_interactions_contact
    FOREIGN KEY (contact_id) REFERENCES contacts (contact_id),
    CONSTRAINT fk_interactions_account
    FOREIGN KEY (account_id) REFERENCES accounts (account_id),
    CONSTRAINT fk_interactions_campaign
    FOREIGN KEY (campaign_id) REFERENCES campaigns (campaign_id)
);

CREATE TABLE support_cases (
    case_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    contact_id INTEGER,
    case_number VARCHAR(64) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    category VARCHAR(64) NOT NULL,
    priority VARCHAR(16) NOT NULL,
    status VARCHAR(32) NOT NULL,
    opened_at TIMESTAMP NOT NULL,
    sla_due_at TIMESTAMP NOT NULL CHECK (sla_due_at >= opened_at),
    resolved_at TIMESTAMP CHECK (resolved_at >= opened_at),
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_support_cases PRIMARY KEY (case_id),
    CONSTRAINT uq_support_cases_case_number UNIQUE (case_number),
    CONSTRAINT fk_support_cases_account
    FOREIGN KEY (account_id) REFERENCES accounts (account_id),
    CONSTRAINT fk_support_cases_contact
    FOREIGN KEY (contact_id) REFERENCES contacts (contact_id)
);
