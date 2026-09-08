-- Finance DDL - Draft v0.1
--
-- Conformance: ANSI SQL, intended to lint clean under
-- `sqlfluff lint --dialect ansi`.
-- Runs unmodified in DuckDB 0.10+.
--
-- Source ERD: schemas/finance/finance_er.dbml
--
-- Numeric precision: monetary amounts use DECIMAL(19, 4), and FX rates use
-- DECIMAL(19, 6). Generator logic and reference SQL must use fixed-point
-- decimal semantics and one shared rounding convention.
--
-- NOT-NULL rules: never nullify a PK or INNER-JOIN FK.
-- INNER-JOIN FKs: ledger_entries.transaction_id,
-- ledger_entries.account_id, budgets.account_id.
-- Nullable analytical fields: accounts.parent_account_id,
-- ledger_entries.debit_amount, ledger_entries.credit_amount, fx_rates.rate.
--
-- Explicit many-to-many bridge: ledger_entries links transactions to accounts
-- while preserving double-entry line attributes.
--
-- FX-adjusted sums use a non-FK composite analytical lookup:
-- transactions.source_currency = fx_rates.from_currency
-- AND transactions.target_currency = fx_rates.to_currency
-- AND transactions.transaction_date = fx_rates.rate_date

CREATE TABLE accounts (
    account_id INTEGER NOT NULL,
    account_number VARCHAR(64) NOT NULL,
    account_name VARCHAR(255) NOT NULL,
    account_type VARCHAR(32) NOT NULL,
    account_subtype VARCHAR(64),
    currency_code CHAR(3) NOT NULL,
    parent_account_id INTEGER,
    normal_balance VARCHAR(16) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_accounts PRIMARY KEY (account_id),
    CONSTRAINT fk_accounts_parent_account
    FOREIGN KEY (parent_account_id) REFERENCES accounts (account_id)
);

CREATE TABLE transactions (
    transaction_id INTEGER NOT NULL,
    transaction_date DATE NOT NULL,
    description VARCHAR(1024),
    source_system VARCHAR(32) NOT NULL,
    source_currency CHAR(3) NOT NULL,
    target_currency CHAR(3) NOT NULL DEFAULT 'USD',
    total_amount DECIMAL(19, 4) NOT NULL,
    reversed BOOLEAN NOT NULL DEFAULT FALSE,
    posted_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_transactions PRIMARY KEY (transaction_id)
);

CREATE TABLE ledger_entries (
    entry_id INTEGER NOT NULL,
    transaction_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    line_number INTEGER NOT NULL,
    debit_amount DECIMAL(19, 4),
    credit_amount DECIMAL(19, 4),
    currency_code CHAR(3) NOT NULL,
    posting_type VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_ledger_entries PRIMARY KEY (entry_id),
    CONSTRAINT fk_ledger_entries_transaction
    FOREIGN KEY (transaction_id) REFERENCES transactions (transaction_id),
    CONSTRAINT fk_ledger_entries_account
    FOREIGN KEY (account_id) REFERENCES accounts (account_id)
);

CREATE TABLE budgets (
    budget_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    fiscal_year INTEGER NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    budget_amount DECIMAL(19, 4) NOT NULL,
    currency_code CHAR(3) NOT NULL,
    scenario VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_budgets PRIMARY KEY (budget_id),
    CONSTRAINT fk_budgets_account
    FOREIGN KEY (account_id) REFERENCES accounts (account_id)
);

CREATE TABLE fx_rates (
    rate_id INTEGER NOT NULL,
    from_currency CHAR(3) NOT NULL,
    to_currency CHAR(3) NOT NULL,
    rate_date DATE NOT NULL,
    rate DECIMAL(19, 6),
    rate_source VARCHAR(32) NOT NULL,
    is_estimated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_fx_rates PRIMARY KEY (rate_id)
);
