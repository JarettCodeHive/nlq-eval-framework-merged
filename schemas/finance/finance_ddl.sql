-- Finance DDL - Contract v1.0
--
-- Conformance: ANSI SQL, intended to lint clean under
-- `sqlfluff lint --dialect ansi`.
-- Runs unmodified in DuckDB 0.10+.
--
-- Source ERD: schemas/finance/finance_er.dbml
-- CSV contract: schemas/finance/finance_csv_header_spec.md
--
-- Numeric precision: monetary amounts use DECIMAL(19, 4), and FX rates use
-- DECIMAL(19, 6). Generator logic and reference SQL must use fixed-point
-- decimal semantics and one shared rounding convention.
-- Rounding policy: ROUND_HALF_UP. Multiply first and round once to scale 4.
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
-- The composite lookup is UNIQUE but intentionally is not a physical FK.
-- Finance v1.0 uses multiple source currencies and USD as the sole reporting
-- / target currency. USD-to-USD rates are generated as 1.000000.
-- Supported source currencies:
-- USD, EUR, GBP, INR, JPY, CAD, AUD, CHF, SGD, AED.
-- Five percent of transactions intentionally have no ledger entries.

CREATE TABLE accounts (
    account_id INTEGER NOT NULL,
    account_number VARCHAR(64) NOT NULL,
    account_name VARCHAR(255) NOT NULL,
    account_type VARCHAR(32) NOT NULL,
    account_subtype VARCHAR(64),
    currency_code CHAR(3) NOT NULL
    CHECK (
        currency_code IN (
            'USD', 'EUR', 'GBP', 'INR', 'JPY',
            'CAD', 'AUD', 'CHF', 'SGD', 'AED'
        )
    ),
    parent_account_id INTEGER,
    normal_balance VARCHAR(16) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_accounts PRIMARY KEY (account_id),
    CONSTRAINT uq_accounts_account_number UNIQUE (account_number),
    CONSTRAINT fk_accounts_parent_account
    FOREIGN KEY (parent_account_id) REFERENCES accounts (account_id)
);

CREATE TABLE transactions (
    transaction_id INTEGER NOT NULL,
    transaction_date DATE NOT NULL,
    description VARCHAR(1024),
    source_system VARCHAR(32) NOT NULL,
    source_currency CHAR(3) NOT NULL
    CHECK (
        source_currency IN (
            'USD', 'EUR', 'GBP', 'INR', 'JPY',
            'CAD', 'AUD', 'CHF', 'SGD', 'AED'
        )
    ),
    target_currency CHAR(3) NOT NULL DEFAULT 'USD'
    CHECK (target_currency = 'USD'),
    total_amount DECIMAL(19, 4) NOT NULL CHECK (total_amount > 0),
    reversed BOOLEAN NOT NULL DEFAULT FALSE,
    posted_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_transactions PRIMARY KEY (transaction_id)
);

CREATE TABLE ledger_entries (
    entry_id INTEGER NOT NULL,
    transaction_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    line_number INTEGER NOT NULL,
    debit_amount DECIMAL(19, 4)
    CHECK (debit_amount IS NULL OR debit_amount > 0)
    CHECK ((debit_amount IS NULL) <> (credit_amount IS NULL)),
    credit_amount DECIMAL(19, 4)
    CHECK (credit_amount IS NULL OR credit_amount > 0),
    currency_code CHAR(3) NOT NULL
    CHECK (
        currency_code IN (
            'USD', 'EUR', 'GBP', 'INR', 'JPY',
            'CAD', 'AUD', 'CHF', 'SGD', 'AED'
        )
    ),
    posting_type VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_ledger_entries PRIMARY KEY (entry_id),
    CONSTRAINT uq_ledger_entries_transaction_line
    UNIQUE (transaction_id, line_number),
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
    budget_amount DECIMAL(19, 4) NOT NULL CHECK (budget_amount > 0),
    currency_code CHAR(3) NOT NULL DEFAULT 'USD'
    CHECK (currency_code = 'USD'),
    scenario VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_budgets PRIMARY KEY (budget_id),
    CONSTRAINT fk_budgets_account
    FOREIGN KEY (account_id) REFERENCES accounts (account_id)
);

CREATE TABLE fx_rates (
    rate_id INTEGER NOT NULL,
    from_currency CHAR(3) NOT NULL
    CHECK (
        from_currency IN (
            'USD', 'EUR', 'GBP', 'INR', 'JPY',
            'CAD', 'AUD', 'CHF', 'SGD', 'AED'
        )
    ),
    to_currency CHAR(3) NOT NULL DEFAULT 'USD' CHECK (to_currency = 'USD'),
    rate_date DATE NOT NULL,
    rate DECIMAL(19, 6) CHECK (rate IS NULL OR rate > 0),
    rate_source VARCHAR(32) NOT NULL,
    is_estimated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_fx_rates PRIMARY KEY (rate_id),
    CONSTRAINT uq_fx_rates_currency_date
    UNIQUE (from_currency, to_currency, rate_date)
);
