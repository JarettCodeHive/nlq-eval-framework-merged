from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.finance.config import load_finance_config
from generators.finance.data_dictionary import FIELD_DESCRIPTIONS
from generators.finance.data_dictionary import FinanceDataDictionaryGenerator
from generators.finance.data_dictionary import TABLE_DESCRIPTIONS
from generators.finance.data_dictionary import _type_text


def test_dictionary_includes_every_configured_table_and_field() -> None:
    generator = FinanceDataDictionaryGenerator.for_profile("full")
    config = load_finance_config()
    markdown = generator.generate_markdown()
    expected_fields = {
        f"{table_name}.{field['name']}"
        for table_name in config["table_order"]
        for field in config["tables"][table_name]["fields"]
    }

    assert list(TABLE_DESCRIPTIONS) == config["table_order"]
    assert set(FIELD_DESCRIPTIONS) == expected_fields
    for table_name in config["table_order"]:
        assert f"### `{table_name}`" in markdown
        assert TABLE_DESCRIPTIONS[table_name] in markdown
        for field in config["tables"][table_name]["fields"]:
            assert f"| `{field['name']}` | `{_type_text(field)}` |" in markdown


def test_dictionary_documents_values_distributions_and_generation_rules() -> None:
    markdown = FinanceDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "## Allowed Domain Values" in markdown
    assert "`USD`, `EUR`, `GBP`, `INR`, `JPY`, `CAD`, `AUD`, `CHF`, `SGD`, `AED`" in markdown
    assert "`Asset`, `Liability`, `Equity`, `Revenue`, `Expense`" in markdown
    assert "## Base Generation Rules" in markdown
    assert '"unposted_to_ledger_fraction": 0.05' in markdown
    assert '"maximum_daily_change_bps": 75' in markdown
    assert "## Distribution Configuration" in markdown
    assert "`transaction_amount` | `pareto_amount`" in markdown
    assert '"lambda":3.2' in markdown
    assert "`ledger_entries` grouped by `transaction_id`" in markdown
    assert "`budgets.budget_amount`" in markdown
    assert "### Synthetic FX Daily Movement" in markdown
    assert "`bounded_decimal_movement`" in markdown


def test_dictionary_documents_finance_relationships_and_hierarchy() -> None:
    markdown = FinanceDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "explicit Transaction-to-Account many-to-many bridge" in markdown
    assert "`accounts.parent_account_id` is a nullable self-reference" in markdown
    assert "type-compatible parents in an acyclic hierarchy" in markdown
    assert "analytical only, not a physical FK" in markdown
    assert "| `source_currency` | `char(3)` | `false` | `analytical` |" in markdown
    assert "| `rate_date` | `date` | `false` | `analytical` |" in markdown


def test_dictionary_documents_decimal_accounting_and_reversal_rules() -> None:
    markdown = FinanceDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "`DECIMAL(19, 4)`" in markdown
    assert "`DECIMAL(19, 6)`" in markdown
    assert "Rounding mode: `ROUND_HALF_UP`" in markdown
    assert "Binary floating-point arithmetic is prohibited" in markdown
    assert "Exactly one of `debit_amount` and `credit_amount` is populated" in markdown
    assert "Debit and credit totals both equal `transactions.total_amount`" in markdown
    assert "swap debit and credit orientation" in markdown
    assert "do not apply a second negative multiplier" in markdown
    assert "Five percent of transactions intentionally have no ledger entries" in markdown


def test_dictionary_documents_fx_and_derived_metric_semantics() -> None:
    markdown = FinanceDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "USD as the sole v1.0 reporting currency" in markdown
    assert "not live or production exchange rates" in markdown
    assert "USD/USD identity rates are always `1.000000`" in markdown
    assert "intentionally unconvertible" in markdown
    assert "`ROUND(transactions.total_amount * fx_rates.rate, 4)`" in markdown
    assert "Debit-normal account" in markdown
    assert "credit-normal account" in markdown
    assert "`budget_amount - actual_amount`" in markdown
    assert "`transaction_date`, `posted_at`, `transaction_id`, `line_number`, `entry_id`" in markdown


def test_dictionary_distinguishes_nulls_and_documents_imperfections() -> None:
    markdown = FinanceDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "## Controlled Imperfections" in markdown
    assert "Missing `fx_rates.rate`: `2.5%`" in markdown
    assert "Near-duplicate budgets: `1.0%`" in markdown
    assert "`transactions.total_amount` outliers: `0.5%`" in markdown
    assert "`1900-01-01`" in markdown
    assert "Business-semantic NULLs are separate from injected NULLs" in markdown
    assert "Only `fx_rates.rate` is a controlled NULL-rate target" in markdown
    assert "Business-state NULL for root accounts" in markdown


def test_dictionary_documents_fixed_reference_date_and_period_boundaries() -> None:
    markdown = FinanceDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "Fixed reference date: `2026-08-01`" in markdown
    assert "never the machine clock" in markdown
    assert "start-inclusive and end-exclusive" in markdown
    assert "deliberate test values" in markdown


def test_dictionary_is_deterministic() -> None:
    first = FinanceDataDictionaryGenerator.for_profile("full").generate_markdown()
    second = FinanceDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert first == second


def test_dictionary_refuses_non_release_profile() -> None:
    generator = FinanceDataDictionaryGenerator.for_profile("dev")

    with pytest.raises(ValueError, match="requires the full profile"):
        generator.write_release_dictionary()


def test_dictionary_writes_unsealed_release_atomically(tmp_path: Path) -> None:
    generator = FinanceDataDictionaryGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)

    output_path = generator.write_release_dictionary()

    assert output_path == tmp_path / "data_dictionary.md"
    assert output_path.read_text(encoding="utf-8") == generator.generate_markdown()
    assert not (tmp_path / "data_dictionary.md.tmp").exists()


def test_dictionary_refuses_sealed_release(tmp_path: Path) -> None:
    generator = FinanceDataDictionaryGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="immutable release"):
        generator.write_release_dictionary()


def test_dictionary_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "full")

    with pytest.raises(ValueError, match="only supports finance"):
        FinanceDataDictionaryGenerator(DeterministicGenerator(settings))


def test_type_text_formats_finance_types() -> None:
    assert _type_text({"type": "varchar", "max_length": 255}) == "varchar(255)"
    assert _type_text({"type": "char", "max_length": 3}) == "char(3)"
    assert (
        _type_text({"type": "decimal", "precision": 19, "scale": 4})
        == "decimal(19,4)"
    )
    assert _type_text({"type": "timestamp"}) == "timestamp"
