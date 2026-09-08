from __future__ import annotations

import pytest

from generators.crm.schema_sql import CRMSchemaSQLGenerator


def test_schema_sql_matches_canonical_ddl() -> None:
    generator = CRMSchemaSQLGenerator.for_profile("full")

    assert generator.generate_sql() == generator.settings.schema_source.read_text(
        encoding="utf-8"
    )
    assert "CREATE TABLE accounts" in generator.generate_sql()
    assert "account_size INTEGER" in generator.generate_sql()
    assert "address VARCHAR(255)" in generator.generate_sql()
    assert "CREATE TABLE campaigns" in generator.generate_sql()
    assert "budget_amount DECIMAL(15, 2)" in generator.generate_sql()
    assert "CHECK (currency_code = 'USD')" in generator.generate_sql()
    assert "CREATE TABLE contact_campaigns" in generator.generate_sql()
    assert "attribution_weight DECIMAL(5, 4)" in generator.generate_sql()
    assert (
        "CONSTRAINT pk_contact_campaigns PRIMARY KEY (\n"
        "        contact_id,\n"
        "        campaign_id\n"
        "    )"
    ) in generator.generate_sql()
    assert "CREATE TABLE interactions" in generator.generate_sql()
    assert "engagement_points INTEGER NOT NULL" in generator.generate_sql()
    assert "CONSTRAINT fk_interactions_account" in generator.generate_sql()
    assert "CREATE TABLE support_cases" in generator.generate_sql()
    assert "subject VARCHAR(255) NOT NULL" in generator.generate_sql()
    assert "CONSTRAINT uq_support_cases_case_number" in generator.generate_sql()
    for retired_name in ("opportunities", "contact_opportunities", "activities"):
        assert f"CREATE TABLE {retired_name}" not in generator.generate_sql()


def test_schema_sql_refuses_non_release_profile() -> None:
    generator = CRMSchemaSQLGenerator.for_profile("dev")

    with pytest.raises(ValueError, match="full profile"):
        generator.write_release_schema()
