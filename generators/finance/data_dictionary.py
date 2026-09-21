"""Finance release data-dictionary generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.manifest import build_distribution_metadata
from generators.finance.config import load_finance_config
from generators.finance.config import settings_for_profile
from generators.finance.validators.config import validate_finance_config


TABLE_DESCRIPTIONS = {
    "accounts": (
        "Chart-of-accounts reference hierarchy used to classify ledger activity, "
        "normal balances, budgets, and parent-child account rollups."
    ),
    "transactions": (
        "Source-currency financial transactions, including intentionally unposted "
        "records for Transaction-to-Ledger LEFT JOIN analysis."
    ),
    "ledger_entries": (
        "Double-entry fact and junction table connecting transactions to accounts "
        "through balanced debit and credit lines."
    ),
    "budgets": (
        "USD account-period planning reference used for budget-versus-actual and "
        "variance analysis."
    ),
    "fx_rates": (
        "Synthetic daily source-to-USD exchange-rate reference used through a "
        "unique analytical composite lookup."
    ),
}


FIELD_DESCRIPTIONS = {
    "accounts.account_id": "Unique chart-of-accounts identifier.",
    "accounts.account_number": "Unique synthetic business-facing account number.",
    "accounts.account_name": "Synthetic chart-of-accounts display name.",
    "accounts.account_type": "Top-level financial classification of the account.",
    "accounts.account_subtype": "Detailed classification compatible with account type.",
    "accounts.currency_code": "Functional currency used by the account.",
    "accounts.parent_account_id": "Parent account; NULL identifies a hierarchy root.",
    "accounts.normal_balance": "Debit or credit orientation expected for the account type.",
    "accounts.is_active": "Whether the account is currently active.",
    "accounts.created_at": "Timestamp when the account record was created.",
    "transactions.transaction_id": "Unique financial transaction identifier.",
    "transactions.transaction_date": "Effective accounting and FX lookup date.",
    "transactions.description": "Optional synthetic transaction description.",
    "transactions.source_system": "Synthetic system from which the transaction originated.",
    "transactions.source_currency": "Currency in which the transaction amount is stated.",
    "transactions.target_currency": "Reporting currency for FX conversion; always USD.",
    "transactions.total_amount": "Positive source-currency transaction amount.",
    "transactions.reversed": "Whether debit and credit orientation is reversed.",
    "transactions.posted_at": "Timestamp when the transaction was posted.",
    "ledger_entries.entry_id": "Unique ledger-line identifier.",
    "ledger_entries.transaction_id": "Transaction represented by the ledger line.",
    "ledger_entries.account_id": "Chart-of-accounts member receiving the ledger line.",
    "ledger_entries.line_number": "Sequential line number within a transaction.",
    "ledger_entries.debit_amount": "Positive source-currency debit; mutually exclusive with credit.",
    "ledger_entries.credit_amount": "Positive source-currency credit; mutually exclusive with debit.",
    "ledger_entries.currency_code": "Currency inherited from the source transaction and account.",
    "ledger_entries.posting_type": "Standard, adjustment, accrual, or reversal posting classification.",
    "ledger_entries.created_at": "Timestamp when the ledger line was created after posting.",
    "budgets.budget_id": "Unique budget-row identifier.",
    "budgets.account_id": "Account to which the budget applies.",
    "budgets.fiscal_year": "Fiscal year derived from the budget period start.",
    "budgets.period_start": "Inclusive start date of the budget period.",
    "budgets.period_end": "Exclusive end date of the budget period.",
    "budgets.budget_amount": "Positive USD budget amount for the account period.",
    "budgets.currency_code": "Budget currency; always USD.",
    "budgets.scenario": "Planning scenario associated with the budget.",
    "budgets.created_at": "Timestamp when the budget was created before its period.",
    "fx_rates.rate_id": "Unique synthetic FX-rate identifier.",
    "fx_rates.from_currency": "Source currency converted by the rate.",
    "fx_rates.to_currency": "Reporting currency produced by the rate; always USD.",
    "fx_rates.rate_date": "Effective date of the daily rate.",
    "fx_rates.rate": "Source-to-USD multiplier; may be intentionally NULL.",
    "fx_rates.rate_source": "Synthetic source classification for the rate.",
    "fx_rates.is_estimated": "Whether the synthetic rate is marked as estimated.",
    "fx_rates.created_at": "Timestamp when the synthetic FX row was created.",
}


DOMAIN_VALUE_LABELS = {
    "account_types": "Account types",
    "source_currencies": "Source and functional currencies",
    "source_systems": "Transaction source systems",
    "posting_types": "Ledger posting types",
    "budget_scenarios": "Budget scenarios",
    "rate_sources": "FX-rate sources",
}


class FinanceDataDictionaryGenerator:
    """Generate the Finance release dictionary from validated configuration."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "finance":
            raise ValueError("FinanceDataDictionaryGenerator only supports finance")
        validate_finance_config()
        self.generator = generator
        self.settings = generator.settings
        self.finance_config = load_finance_config()
        self._validate_description_coverage()

    @classmethod
    def for_profile(cls, profile: str) -> "FinanceDataDictionaryGenerator":
        """Create a Finance dictionary generator from validated profile config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_markdown(self) -> str:
        """Return deterministic Markdown for the complete Finance contract."""

        lines = [
            "# Finance Data Dictionary",
            "",
            f"Dataset version: `{self.settings.dataset_version}`",
            "",
            f"Schema source: `{self.finance_config['schema_source']}`",
            "",
            f"Fixed reference date: `{self.settings.reference_today.isoformat()}`",
            "",
            (
                "Finance supports multiple source currencies and uses USD as the "
                "sole v1.0 reporting currency. All rates are frozen synthetic test "
                "data, not live or production exchange rates."
            ),
            "",
            "## Tables",
            "",
        ]
        for table_name in self.settings.table_order:
            lines.extend(
                self._table_section(
                    table_name,
                    self.finance_config["tables"][table_name],
                )
            )
        lines.extend(self._domain_values_section())
        lines.extend(self._generation_rules_section())
        lines.extend(self._distribution_section())
        lines.extend(self._relationship_section())
        lines.extend(self._accounting_section())
        lines.extend(self._derived_metrics_section())
        lines.extend(self._imperfection_section())
        lines.extend(self._date_semantics_section())
        return "\n".join(lines).rstrip() + "\n"

    def write_release_dictionary(self) -> Path:
        """Atomically write data_dictionary.md into an unsealed release."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Finance release data-dictionary generation requires the full "
                f"profile; got profile={self.settings.profile}"
            )
        manifest_path = self.settings.output_path / "manifest.json"
        if manifest_path.exists():
            raise FileExistsError(
                "Refusing to modify immutable release after manifest exists: "
                f"{manifest_path}"
            )
        output_path = self.settings.output_path / "data_dictionary.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".md.tmp")
        temporary_path.write_text(
            self.generate_markdown(),
            encoding="utf-8",
            newline="\n",
        )
        temporary_path.replace(output_path)
        return output_path

    def _validate_description_coverage(self) -> None:
        expected_fields = {
            f"{table_name}.{field['name']}"
            for table_name in self.settings.table_order
            for field in self.finance_config["tables"][table_name]["fields"]
        }
        if set(FIELD_DESCRIPTIONS) != expected_fields:
            raise ValueError(
                "Finance field-description coverage differs from config: "
                f"missing={sorted(expected_fields - set(FIELD_DESCRIPTIONS))}, "
                f"extra={sorted(set(FIELD_DESCRIPTIONS) - expected_fields)}"
            )
        if set(TABLE_DESCRIPTIONS) != set(self.settings.table_order):
            raise ValueError(
                "Finance table-description coverage differs from table_order"
            )

    def _table_section(
        self,
        table_name: str,
        table_config: dict[str, Any],
    ) -> list[str]:
        lines = [
            f"### `{table_name}`",
            "",
            TABLE_DESCRIPTIONS[table_name],
            "",
            f"Role: `{table_config['role']}`",
            "",
            (
                f"Base row targets: dev `{table_config['row_targets']['dev']}`, "
                f"full `{table_config['row_targets']['full']}`."
            ),
            "",
            "| Column | Type | Nullable | Key role | References | Default | Business meaning | Generation behavior | NULL / imperfection behavior |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        lines.extend(
            self._field_row(table_name, field) for field in table_config["fields"]
        )
        lines.append("")
        return lines

    def _field_row(self, table_name: str, field: dict[str, Any]) -> str:
        field_name = field["name"]
        reference = field.get("references")
        reference_text = (
            f"`{reference['table']}.{reference['field']}`" if reference else ""
        )
        default = field.get("default", "")
        if isinstance(default, bool):
            default = str(default).lower()
        default_text = f"`{default}`" if default != "" else ""
        return (
            f"| `{field_name}` | `{_type_text(field)}` | "
            f"`{str(field['nullable']).lower()}` | "
            f"{self._key_role(table_name, field)} | {reference_text} | "
            f"{default_text} | {FIELD_DESCRIPTIONS[f'{table_name}.{field_name}']} | "
            f"{_generation_text(field)} | {_imperfection_text(table_name, field)} |"
        )

    def _key_role(self, table_name: str, field: dict[str, Any]) -> str:
        if field.get("key"):
            return f"`{field['key']}`"
        for relationship in self.finance_config["relationships"]:
            if relationship["relationship_type"] != "analytical_composite":
                continue
            for condition in relationship["conditions"]:
                left = (relationship["left_table"], condition["left_field"])
                right = (relationship["right_table"], condition["right_field"])
                if (table_name, field["name"]) in {left, right}:
                    return "`analytical`"
        return ""

    def _domain_values_section(self) -> list[str]:
        lines = ["## Allowed Domain Values", ""]
        for config_name, label in DOMAIN_VALUE_LABELS.items():
            values = ", ".join(
                f"`{value}`"
                for value in self.finance_config["domain_values"][config_name]
            )
            lines.append(f"- {label}: {values}.")
        lines.append("")
        return lines

    def _generation_rules_section(self) -> list[str]:
        return [
            "## Base Generation Rules",
            "",
            "These deterministic settings control clean Finance entity generation.",
            "",
            "```json",
            json.dumps(self.finance_config["generation_rules"], indent=2),
            "```",
            "",
        ]

    def _distribution_section(self) -> list[str]:
        metadata = build_distribution_metadata(
            self.finance_config["distributions"],
            self.settings.distributions,
            self.finance_config["distribution_targets"],
        )
        lines = [
            "## Distribution Configuration",
            "",
            (
                "Finance selects shared presets from `config/generation/base.json` "
                "and declares domain overrides in "
                "`config/generation/finance/generation.json`."
            ),
            "",
            "| Setting | Shared preset | Finance overrides | Effective parameters | Targets |",
            "|---|---|---|---|---|",
        ]
        for settings_key, specification in metadata.items():
            overrides = (
                _compact_json(specification["overrides"])
                if specification["overrides"]
                else "None"
            )
            targets = ", ".join(
                _distribution_target_text(target)
                for target in specification["targets"].values()
            )
            lines.append(
                f"| `{settings_key}` | `{specification['preset']}` | {overrides} | "
                f"{_compact_json(specification['effective_parameters'])} | "
                f"{targets} |"
            )
        fx_target = self.finance_config["distribution_targets"]["fx_daily_movement"]
        lines.extend(
            [
                "",
                "### Synthetic FX Daily Movement",
                "",
                (
                    "`fx_rates.rate` uses the Finance-specific "
                    f"`{fx_target['distribution']}` algorithm with parameters "
                    f"{_compact_json(self.finance_config['generation_rules']['fx_daily_movement'])}."
                ),
                "",
            ]
        )
        return lines

    def _relationship_section(self) -> list[str]:
        lines = [
            "## Relationships",
            "",
            "| Type | Left / parent | Right / child | Cardinality | Join type | Conditions |",
            "|---|---|---|---|---|---|",
        ]
        for relationship in self.finance_config["relationships"]:
            if relationship["relationship_type"] == "foreign_key":
                left = (
                    f"`{relationship['parent_table']}."
                    f"{relationship['parent_field']}`"
                )
                right = (
                    f"`{relationship['child_table']}."
                    f"{relationship['child_field']}`"
                )
                conditions = "Physically enforced FK."
            else:
                left = f"`{relationship['left_table']}`"
                right = f"`{relationship['right_table']}`"
                conditions = "; ".join(
                    f"`{condition['left_field']} = {condition['right_field']}`"
                    for condition in relationship["conditions"]
                )
                conditions += "; analytical only, not a physical FK."
            lines.append(
                f"| `{relationship['relationship_type']}` | {left} | {right} | "
                f"`{relationship['cardinality']}` | "
                f"`{relationship['join_type']}` | {conditions} |"
            )
        lines.extend(
            [
                "",
                (
                    "`ledger_entries` is the explicit Transaction-to-Account "
                    "many-to-many bridge: one transaction may post to multiple "
                    "accounts and one account may receive multiple transactions."
                ),
                "",
                (
                    "`accounts.parent_account_id` is a nullable self-reference. "
                    "NULL identifies root accounts; populated values identify "
                    "type-compatible parents in an acyclic hierarchy."
                ),
                "",
            ]
        )
        return lines

    def _accounting_section(self) -> list[str]:
        policy = self.finance_config["decimal_policy"]
        return [
            "## Accounting and Currency Rules",
            "",
            "### Numeric Precision",
            "",
            (
                f"- Monetary values use `DECIMAL({policy['money_precision']}, "
                f"{policy['money_scale']})`; FX rates use "
                f"`DECIMAL({policy['fx_precision']}, {policy['fx_scale']})`."
            ),
            f"- Rounding mode: `{policy['rounding_mode']}`.",
            f"- Conversion order: `{policy['conversion_order']}`.",
            "- Binary floating-point arithmetic is prohibited for reference results.",
            "",
            "### Double Entry and Reversals",
            "",
            "- Exactly one of `debit_amount` and `credit_amount` is populated per line.",
            "- Debit and credit totals both equal `transactions.total_amount`.",
            "- Ledger amounts and currency remain in the transaction source currency.",
            (
                "- Reversed transactions swap debit and credit orientation; do not "
                "apply a second negative multiplier in rolling balances."
            ),
            "- Five percent of transactions intentionally have no ledger entries.",
            "",
            "### FX Lookup and Missing Rates",
            "",
            (
                "- Join transactions to FX by `source_currency = from_currency`, "
                "`target_currency = to_currency`, and "
                "`transaction_date = rate_date`."
            ),
            "- The composite FX lookup is unique but is not a physical foreign key.",
            "- USD/USD identity rates are always `1.000000`.",
            (
                "- A matching FX row whose `rate` is NULL is intentionally "
                "unconvertible; do not treat it as a missing join row or as zero."
            ),
            "",
        ]

    def _derived_metrics_section(self) -> list[str]:
        rolling_order = ", ".join(
            f"`{field}`"
            for field in self.finance_config["business_mappings"][
                "rolling_balance"
            ]["order_by"]
        )
        return [
            "## Derived Metrics and Query Guidance",
            "",
            "| Metric | Definition |",
            "|---|---|",
            (
                "| FX-adjusted transaction amount | Join the unique FX row, then "
                "calculate `ROUND(transactions.total_amount * fx_rates.rate, 4)` "
                "using `ROUND_HALF_UP`; exclude or explicitly report NULL rates. |"
            ),
            (
                "| Normal-balance signed ledger amount | Debit-normal account: "
                "`debit_amount - credit_amount`; credit-normal account: "
                "`credit_amount - debit_amount`. |"
            ),
            (
                "| Budget actual | Sum FX-adjusted normal-balance signed ledger "
                "amounts by `account_id` where `transaction_date >= period_start` "
                "and `transaction_date < period_end`. |"
            ),
            (
                "| Budget variance | `budget_amount - actual_amount`; retain budget "
                "periods without actuals through a LEFT JOIN. |"
            ),
            (
                "| Rolling balance | Cumulative normal-balance signed ledger amount "
                f"ordered by {rolling_order}. |"
            ),
            "",
        ]

    def _imperfection_section(self) -> list[str]:
        imperfections = self.settings.imperfections
        outlier = self.finance_config["imperfection_targets"][
            "transaction_amount_outliers"
        ]
        boundaries = ", ".join(
            f"`{value}`" for value in imperfections["boundary_dates"]
        )
        return [
            "## Controlled Imperfections",
            "",
            (
                "- Missing `fx_rates.rate`: "
                f"`{imperfections['null_pct']}%`; USD/USD and boundary-date rows "
                "remain populated."
            ),
            (
                "- Near-duplicate budgets: "
                f"`{imperfections['duplicate_pct']}%` appended rows retain the "
                "business key and vary only `budget_amount` and `created_at`."
            ),
            (
                "- `transactions.total_amount` outliers: "
                f"`{imperfections['outlier_pct']}%` from "
                f"`{outlier['minimum_value']}` through "
                f"`{outlier['maximum_value']}` at scale `{outlier['scale']}`; "
                "dependent ledger lines are rebalanced."
            ),
            f"- `transactions.transaction_date` boundary values: {boundaries}.",
            "",
            (
                "Business-semantic NULLs are separate from injected NULLs. Root "
                "accounts have NULL `parent_account_id`; ledger rows populate "
                "exactly one debit or credit; descriptions and subtypes are "
                "optional. Only `fx_rates.rate` is a controlled NULL-rate target."
            ),
            "",
        ]

    def _date_semantics_section(self) -> list[str]:
        return [
            "## Date Semantics",
            "",
            (
                f"All relative-date interpretation uses the fixed `reference_today` "
                f"value `{self.settings.reference_today.isoformat()}`, never the "
                "machine clock."
            ),
            "",
            (
                "Budget and actual periods are start-inclusive and end-exclusive. "
                "Configured transaction boundary dates are deliberate test values "
                "and may fall far outside ordinary accounting dates."
            ),
            "",
        ]


def _type_text(field: dict[str, Any]) -> str:
    field_type = field["type"]
    if field_type in {"varchar", "char"}:
        return f"{field_type}({field['max_length']})"
    if field_type == "decimal":
        return f"decimal({field['precision']},{field['scale']})"
    return str(field_type)


def _generation_text(field: dict[str, Any]) -> str:
    parts = [
        f"Source `{field['synthetic_source']}`"
        if "synthetic_source" in field
        else "Deterministic entity identifier"
    ]
    if "distribution" in field:
        parts.append(f"distribution `{field['distribution']}`")
    return "; ".join(parts) + "."


def _imperfection_text(table_name: str, field: dict[str, Any]) -> str:
    field_key = f"{table_name}.{field['name']}"
    behavior: list[str] = []
    if field_key == "budgets.budget_id":
        behavior.append("Appended near-duplicate rows receive fresh identifiers.")
    if field_key in {
        "budgets.account_id",
        "budgets.fiscal_year",
        "budgets.period_start",
        "budgets.period_end",
        "budgets.scenario",
    }:
        behavior.append("Repeated as part of the near-duplicate business key.")
    if field_key in {"budgets.budget_amount", "budgets.created_at"}:
        behavior.append("Permitted near-duplicate variation field.")
    if field_key in {
        "transactions.posted_at",
        "ledger_entries.created_at",
    }:
        behavior.append("Adjusted with boundary-date transaction chronology.")
    if field_key in {
        "ledger_entries.debit_amount",
        "ledger_entries.credit_amount",
    }:
        behavior.append("Reallocated when a transaction outlier is injected.")

    target = field.get("imperfection_target")
    if target == "controlled_null":
        behavior.append("Controlled injected NULL target.")
    elif target == "fixed_scale_outlier":
        behavior.append("Controlled fixed-scale high-value outlier target.")
    elif target == "boundary_date":
        behavior.append("Receives every configured boundary date.")

    if field_key == "accounts.parent_account_id":
        behavior.append("Business-state NULL for root accounts.")
    elif field_key in {
        "ledger_entries.debit_amount",
        "ledger_entries.credit_amount",
    }:
        behavior.append("Business-state NULL on the opposite posting side.")
    elif field["nullable"] and not behavior:
        behavior.append("Optional business attribute; not an injected NULL target.")
    elif not behavior:
        behavior.append("Not an imperfection target.")
    return " ".join(behavior)


def _compact_json(value: Any) -> str:
    return f"`{json.dumps(value, sort_keys=True, separators=(',', ':'))}`"


def _distribution_target_text(target: dict[str, Any]) -> str:
    if "targets" in target:
        return ", ".join(f"`{value}`" for value in target["targets"])
    if "field" in target:
        return f"`{target['table']}.{target['field']}`"
    if "group_by" in target:
        return f"`{target['table']}` grouped by `{target['group_by']}`"
    return f"`{target['table']}`"


__all__ = [
    "DOMAIN_VALUE_LABELS",
    "FIELD_DESCRIPTIONS",
    "FinanceDataDictionaryGenerator",
    "TABLE_DESCRIPTIONS",
]
