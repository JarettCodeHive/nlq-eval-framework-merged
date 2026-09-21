"""Finance configuration, schema, and generation-rule validation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

from generators.core.base import GenerationSettings
from generators.core.base import PROJECT_ROOT
from generators.core.base import resolve_distribution_settings
from generators.core.imperfections import count_from_pct
from generators.core.schema_contract import configured_foreign_keys
from generators.core.schema_contract import configured_sql_type
from generators.core.schema_contract import configured_unique_constraints
from generators.core.schema_contract import ddl_constraints
from generators.core.schema_contract import ddl_table_specs
from generators.core.schema_contract import header_table_specs
from generators.core.schema_contract import normalized_default
from generators.core.schema_contract import primary_key_fields
from generators.finance import config as finance_config_module


REQUIRED_SECTIONS = {
    "domain_values",
    "generation_rules",
    "business_mappings",
    "decimal_policy",
    "distributions",
    "distribution_targets",
    "imperfection_targets",
    "relationships",
    "join_path_requirements",
    "accounting_rules",
    "conversion_rules",
    "consistency_rules",
}
EXPECTED_TABLE_ORDER = [
    "accounts",
    "transactions",
    "ledger_entries",
    "budgets",
    "fx_rates",
]
EXPECTED_JOIN_PATH_IDS = [f"finance_jp_{index:03d}" for index in range(1, 11)]
EXPECTED_JOIN_TABLES = {
    "finance_jp_001": ["transactions", "ledger_entries"],
    "finance_jp_002": ["ledger_entries", "transactions"],
    "finance_jp_003": ["ledger_entries", "accounts"],
    "finance_jp_004": ["budgets", "accounts"],
    "finance_jp_005": ["accounts", "accounts"],
    "finance_jp_006": ["transactions", "fx_rates"],
    "finance_jp_007": ["transactions", "fx_rates"],
    "finance_jp_008": ["transactions", "fx_rates"],
    "finance_jp_009": ["transactions", "ledger_entries", "accounts"],
    "finance_jp_010": [
        "budgets",
        "accounts",
        "ledger_entries",
        "transactions",
        "fx_rates",
    ],
}
EXPECTED_JOIN_FIELDS = {
    "finance_jp_001": {"transactions.transaction_id", "ledger_entries.transaction_id"},
    "finance_jp_002": {"ledger_entries.transaction_id", "transactions.transaction_id"},
    "finance_jp_003": {"ledger_entries.account_id", "accounts.account_id"},
    "finance_jp_004": {"budgets.account_id", "accounts.account_id"},
    "finance_jp_005": {"accounts.parent_account_id", "accounts.account_id"},
    "finance_jp_006": {
        "transactions.source_currency",
        "transactions.target_currency",
        "transactions.transaction_date",
        "fx_rates.from_currency",
        "fx_rates.to_currency",
        "fx_rates.rate_date",
    },
    "finance_jp_007": {"fx_rates.rate"},
    "finance_jp_008": {"fx_rates.rate"},
    "finance_jp_009": {
        "transactions.transaction_id",
        "ledger_entries.transaction_id",
        "ledger_entries.account_id",
        "accounts.account_id",
    },
    "finance_jp_010": {
        "budgets.account_id",
        "budgets.period_start",
        "budgets.period_end",
        "transactions.transaction_date",
    },
}
EXPECTED_CHECK_FRAGMENTS = {
    "transactions": {"target_currency='usd'", "total_amount>0"},
    "ledger_entries": {
        "debit_amountisnullordebit_amount>0",
        "credit_amountisnullorcredit_amount>0",
        "debit_amountisnull!=credit_amountisnull",
    },
    "budgets": {"budget_amount>0", "currency_code='usd'"},
    "fx_rates": {"to_currency='usd'", "rateisnullorrate>0"},
}


def validate_finance_config() -> None:
    """Validate the complete Finance contract before generation starts."""

    base_config = finance_config_module.load_base_config()
    finance_config = finance_config_module.load_finance_config()
    _validate_required_sections(finance_config)
    _validate_fixed_references(base_config, finance_config)
    _validate_schema_alignment(finance_config)
    _validate_primary_keys(finance_config)
    _validate_relationships(finance_config)
    _validate_domain_values(finance_config)
    _validate_business_mappings(finance_config)
    _validate_generation_rules(base_config, finance_config)
    _validate_generation_targets(base_config, finance_config)
    _validate_join_paths(finance_config)
    _validate_decimal_policy(finance_config)
    _validate_executable_checks(finance_config)
    for profile in base_config["profiles"]:
        GenerationSettings.from_configs(base_config, finance_config, profile)


def _validate_required_sections(config: dict[str, Any]) -> None:
    """Validate required sections and basic Finance table/field shape."""

    missing = REQUIRED_SECTIONS - set(config)
    if missing:
        raise ValueError(f"Finance config is missing sections: {sorted(missing)}")
    if config.get("domain") != "finance":
        raise ValueError("Finance config domain must equal 'finance'")
    if config.get("table_order") != EXPECTED_TABLE_ORDER:
        raise ValueError("Finance table_order differs from the signed contract")

    tables = config.get("tables")
    if not isinstance(tables, dict) or list(tables) != EXPECTED_TABLE_ORDER:
        raise ValueError("Finance tables must match table_order exactly")
    for table_name, table in tables.items():
        missing_table = {"primary_key", "role", "row_targets", "fields"} - set(table)
        if missing_table:
            raise ValueError(
                f"{table_name} is missing config keys: {sorted(missing_table)}"
            )
        fields = table["fields"]
        if not isinstance(fields, list) or not fields:
            raise ValueError(f"{table_name}.fields must be a non-empty list")
        names = [field.get("name") for field in fields]
        if any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError(f"{table_name}.fields has missing or duplicate names")
        for field in fields:
            missing_field = {"name", "type", "nullable"} - set(field)
            if missing_field:
                raise ValueError(
                    f"{table_name}.{field.get('name', '<unnamed>')} is missing "
                    f"config keys: {sorted(missing_field)}"
                )


def _validate_fixed_references(
    base_config: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Validate shared-value references and release-profile agreement."""

    expected = {
        "seed_source": "config/generation/base.json:seed",
        "reference_today_source": "config/generation/base.json:reference_today",
    }
    for name, reference in expected.items():
        if config["fixed_values"].get(name) != reference:
            raise ValueError(f"Finance fixed_values.{name} must reference {reference}")
        _resolve_reference(reference, base_config, config, name)
    if not config["fixed_values"].get("manifest_generated_at"):
        raise ValueError("Finance manifest_generated_at is required")
    if config["generation_notes"].get("row_cap_source") != (
        "config/generation/base.json:max_rows_per_table"
    ):
        raise ValueError("Finance row cap must reference shared base config")
    if base_config["release_profile"] != config["release_rules"]["release_profile"]:
        raise ValueError("release_profile differs between base and Finance config")


def _validate_schema_alignment(config: dict[str, Any]) -> None:
    """Compare Finance config with canonical DDL and signed CSV headers."""

    schema_path = PROJECT_ROOT / config["schema_source"]
    header_path = schema_path.with_name("finance_csv_header_spec.md")
    ddl = ddl_table_specs(schema_path)
    headers = header_table_specs(header_path)
    if list(ddl) != EXPECTED_TABLE_ORDER or list(headers) != EXPECTED_TABLE_ORDER:
        raise ValueError(
            "Finance table order differs across DDL, header spec, and config"
        )

    for table_name in EXPECTED_TABLE_ORDER:
        configured_fields = config["tables"][table_name]["fields"]
        configured_names = [field["name"] for field in configured_fields]
        if list(ddl[table_name]) != configured_names:
            raise ValueError(f"{table_name} fields differ between DDL and config")
        if [field["name"] for field in headers[table_name]] != configured_names:
            raise ValueError(f"{table_name} fields differ between header and config")
        header_by_name = {field["name"]: field for field in headers[table_name]}
        for field in configured_fields:
            field_name = field["name"]
            expected_type = configured_sql_type(field)
            expected_nullable = bool(field["nullable"])
            if ddl[table_name][field_name] != {
                "type": expected_type,
                "nullable": expected_nullable,
                "default": normalized_default(field.get("default")),
            }:
                raise ValueError(
                    f"{table_name}.{field_name} differs between DDL and config"
                )
            if header_by_name[field_name] != {
                "name": field_name,
                "type": expected_type,
                "nullable": expected_nullable,
            }:
                raise ValueError(
                    f"{table_name}.{field_name} differs between header and config"
                )

    constraints = ddl_constraints(schema_path)
    configured_primary = {
        table_name: tuple(primary_key_fields(table))
        for table_name, table in config["tables"].items()
    }
    if constraints["primary_keys"] != configured_primary:
        raise ValueError("Finance DDL primary keys differ from config")
    if constraints["foreign_keys"] != configured_foreign_keys(config):
        raise ValueError("Finance DDL foreign keys differ from config")
    if constraints["unique_constraints"] != configured_unique_constraints(config):
        raise ValueError("Finance DDL unique constraints differ from config")


def _validate_primary_keys(config: dict[str, Any]) -> None:
    """Validate primary-key references, markers, and nullability."""

    for table_name, table in config["tables"].items():
        fields = {field["name"]: field for field in table["fields"]}
        for field_name in primary_key_fields(table):
            if field_name not in fields:
                raise ValueError(f"{table_name} primary key references unknown field")
            if fields[field_name]["nullable"]:
                raise ValueError(f"{table_name}.{field_name} primary key is nullable")
            if fields[field_name].get("key") not in {"primary", "primary_foreign"}:
                raise ValueError(f"{table_name}.{field_name} lacks primary key marker")


def _validate_relationships(config: dict[str, Any]) -> None:
    """Validate physical FK coverage and the composite analytical FX lookup."""

    physical: set[tuple[str, str, str, str]] = set()
    analytical = []
    for relationship in config["relationships"]:
        relationship_type = relationship.get("relationship_type")
        if relationship_type == "foreign_key":
            parent_table = relationship.get("parent_table")
            child_table = relationship.get("child_table")
            parent_field = relationship.get("parent_field")
            child_field = relationship.get("child_field")
            _require_field(config, parent_table, parent_field)
            _require_field(config, child_table, child_field)
            physical.add((child_table, child_field, parent_table, parent_field))
        elif relationship_type == "analytical_composite":
            analytical.append(relationship)
            _validate_analytical_fx_relationship(config, relationship)
        else:
            raise ValueError(
                f"Unsupported Finance relationship type: {relationship_type}"
            )
    if physical != configured_foreign_keys(config):
        raise ValueError("Finance physical relationships must cover configured FKs")
    if len(analytical) != 1:
        raise ValueError("Finance must define one analytical FX relationship")


def _validate_analytical_fx_relationship(
    config: dict[str, Any], relationship: dict[str, Any]
) -> None:
    """Validate the non-FK Transaction-to-FX composite lookup."""

    if (
        relationship.get("left_table") != "transactions"
        or relationship.get("right_table") != "fx_rates"
    ):
        raise ValueError("Finance analytical relationship must join transactions to FX")
    if relationship.get("enforced_as_foreign_key") is not False:
        raise ValueError("Finance analytical FX relationship cannot be a physical FK")
    actual = []
    for condition in relationship.get("conditions", []):
        left = condition.get("left_field")
        right = condition.get("right_field")
        _require_field(config, "transactions", left)
        _require_field(config, "fx_rates", right)
        actual.append((left, right))
    expected = [
        ("source_currency", "from_currency"),
        ("target_currency", "to_currency"),
        ("transaction_date", "rate_date"),
    ]
    if actual != expected:
        raise ValueError("Finance analytical FX conditions differ from contract")


def _validate_domain_values(config: dict[str, Any]) -> None:
    """Require Finance domain-value lists to be non-empty and unique."""

    values = config["domain_values"]
    expected = {
        "account_types",
        "source_currencies",
        "source_systems",
        "posting_types",
        "budget_scenarios",
        "rate_sources",
    }
    if not isinstance(values, dict) or set(values) != expected:
        raise ValueError("Finance domain_values groups differ from contract")
    for name, options in values.items():
        if not isinstance(options, list) or not options:
            raise ValueError(f"Finance domain_values.{name} must be non-empty")
        if any(not isinstance(option, str) or not option for option in options):
            raise ValueError(f"Finance domain_values.{name} contains invalid values")
        if len(options) != len(set(options)):
            raise ValueError(f"Finance domain_values.{name} contains duplicates")


def _validate_business_mappings(config: dict[str, Any]) -> None:
    """Validate account, currency, ledger, conversion, and reporting mappings."""

    mappings = config["business_mappings"]
    values = config["domain_values"]
    if mappings.get("reporting_currency") != "USD":
        raise ValueError("Finance reporting currency must be USD")

    account_types = values["account_types"]
    subtypes = mappings.get("account_subtypes")
    balances = mappings.get("normal_balance_by_account_type")
    if not isinstance(subtypes, dict) or set(subtypes) != set(account_types):
        raise ValueError("Finance account subtype mapping is incomplete")
    flattened: list[str] = []
    for account_type, options in subtypes.items():
        if not isinstance(options, list) or not options:
            raise ValueError(f"Finance account subtypes are empty for {account_type}")
        flattened.extend(options)
    if len(flattened) != len(set(flattened)):
        raise ValueError("Finance account subtypes must be unique across types")
    if not isinstance(balances, dict) or set(balances) != set(account_types):
        raise ValueError("Finance normal-balance mapping is incomplete")
    if set(balances.values()) - {"Debit", "Credit"}:
        raise ValueError("Finance normal balances must be Debit or Credit")

    pairs = config["generation_rules"]["fx_calendar"]["currency_pairs"]
    anchors = mappings.get("fx_anchor_rates")
    if not isinstance(anchors, dict) or set(anchors) != set(pairs):
        raise ValueError("Finance FX anchors must cover every configured pair")
    for pair, value in anchors.items():
        rate = _decimal(value, f"fx_anchor_rates.{pair}")
        if rate <= 0 or _decimal_places(rate) > 6:
            raise ValueError(f"Finance FX anchor is invalid for {pair}")
    if anchors.get("USD/USD") != "1.000000":
        raise ValueError("Finance USD/USD anchor must equal 1.000000")

    ledger = mappings.get("ledger_balance", {})
    if ledger.get("one_populated_side_per_line") is not True:
        raise ValueError("Finance ledger exclusivity mapping is required")
    reversal = mappings.get("reversal_semantics", {})
    if reversal.get("behavior") != "swap_debit_credit_orientation" or (
        reversal.get("rolling_balance_multiplier") != 1
    ):
        raise ValueError("Finance reversal semantics differ from contract")
    conversion = mappings.get("fx_conversion", {})
    if conversion.get("rounding") != "multiply_first_round_once" or (
        conversion.get("null_rate_result") != "unconvertible"
    ):
        raise ValueError("Finance FX conversion semantics are incomplete")
    budget = mappings.get("budget_actual", {})
    if budget.get("budget_currency") != "USD" or (
        budget.get("period_boundary") != "start_inclusive_end_exclusive"
    ):
        raise ValueError("Finance budget-versus-actual mapping is invalid")
    required_order = [
        "transaction_date",
        "posted_at",
        "transaction_id",
        "line_number",
        "entry_id",
    ]
    if mappings.get("rolling_balance", {}).get("order_by") != required_order:
        raise ValueError("Finance rolling-balance order differs from contract")


def _validate_generation_rules(
    base_config: dict[str, Any], config: dict[str, Any]
) -> None:
    """Validate Finance ranges, weights, dates, profiles, and FX capacity."""

    rules = config["generation_rules"]
    expected = {
        "date_windows",
        "accounts",
        "transactions",
        "ledger_entries",
        "budgets",
        "fx_calendar",
        "fx_daily_movement",
        "fx_rates",
    }
    if set(rules) != expected:
        raise ValueError("Finance generation_rules groups differ from contract")

    reference_today = date.fromisoformat(base_config["reference_today"])
    for name, value in rules["date_windows"].items():
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid Finance date window: {name}") from exc
        if parsed > reference_today:
            raise ValueError(f"Finance date window {name} is after reference_today")

    accounts = rules["accounts"]
    _validate_probability(accounts["active_probability"], "accounts.active_probability")
    _validate_probability(
        accounts["hierarchy"]["root_fraction"], "accounts.hierarchy.root_fraction"
    )
    if accounts["hierarchy"].get("acyclic_assignment") is not True:
        raise ValueError("Finance account hierarchy must be acyclic")
    _validate_weights(
        accounts["type_weights"],
        config["domain_values"]["account_types"],
        "accounts.type_weights",
    )
    _validate_weights(
        accounts["currency_weights"],
        config["domain_values"]["source_currencies"],
        "accounts.currency_weights",
    )

    transactions = rules["transactions"]
    _validate_probability(
        transactions["unposted_to_ledger_fraction"],
        "transactions.unposted_to_ledger_fraction",
    )
    _validate_probability(
        transactions["reversed_probability"], "transactions.reversed_probability"
    )
    _validate_integer_range(
        transactions["posted_lag_days"], "transactions.posted_lag_days", minimum=0
    )
    _validate_weights(
        transactions["source_system_weights"],
        config["domain_values"]["source_systems"],
        "transactions.source_system_weights",
    )

    ledger = rules["ledger_entries"]
    if not _positive_integer(ledger.get("minimum_lines_per_posted_transaction")):
        raise ValueError("Finance minimum ledger lines must be positive")
    if (
        ledger.get("require_even_line_count") is not True
        or ledger.get("reconcile_to_row_target") is not True
    ):
        raise ValueError("Finance ledger reconciliation rules are incomplete")
    _validate_integer_range(
        ledger["created_lag_hours"], "ledger_entries.created_lag_hours", minimum=0
    )

    budgets = rules["budgets"]
    _validate_weights(
        budgets["scenario_weights"],
        config["domain_values"]["budget_scenarios"],
        "budgets.scenario_weights",
    )
    _validate_integer_range(
        budgets["creation_lead_days"], "budgets.creation_lead_days", minimum=0
    )
    if not _positive_integer(budgets.get("period_months")):
        raise ValueError("Finance budget period_months must be positive")
    if (
        budgets.get("start_inclusive") is not True
        or budgets.get("end_exclusive") is not True
    ):
        raise ValueError("Finance budget periods must be start-inclusive/end-exclusive")
    profiles = set(base_config["profiles"])
    period_offsets = budgets.get("period_offsets", {})
    if set(period_offsets) != profiles:
        raise ValueError("Finance budget period offsets are incomplete")
    for profile in profiles:
        offsets = period_offsets[profile]
        if (
            not isinstance(offsets, list)
            or len(offsets) != budgets["periods_per_account"].get(profile)
            or len(offsets) != len(set(offsets))
            or offsets != sorted(offsets)
            or any(
                not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
                for offset in offsets
            )
        ):
            raise ValueError(f"Finance budget period offsets are invalid for {profile}")

    _validate_fx_calendar(base_config, config)
    movement = rules["fx_daily_movement"]
    if movement.get("algorithm") != "bounded_decimal_movement":
        raise ValueError("Finance FX movement algorithm differs from contract")
    if movement.get("scale") != 6 or movement.get("identity_rate") != "1.000000":
        raise ValueError("Finance FX movement precision or identity rate is invalid")
    if not _positive_integer(movement.get("maximum_daily_change_bps")):
        raise ValueError("Finance maximum daily FX movement must be positive")
    mean_reversion = _decimal(
        movement.get("mean_reversion_fraction"), "fx_daily_movement.mean_reversion"
    )
    if not Decimal("0") <= mean_reversion <= Decimal("1"):
        raise ValueError("Finance FX mean reversion must be between zero and one")
    _validate_probability(
        rules["fx_rates"]["estimated_probability"],
        "fx_rates.estimated_probability",
    )


def _validate_fx_calendar(base_config: dict[str, Any], config: dict[str, Any]) -> None:
    """Validate currency pairs, profile calendars, and exact FX-grid capacity."""

    calendar = config["generation_rules"]["fx_calendar"]
    currencies = config["domain_values"]["source_currencies"]
    if calendar.get("target_currency") != "USD":
        raise ValueError("Finance FX target currency must be USD")
    pairs = calendar.get("currency_pairs")
    expected_pairs = [f"{currency}/USD" for currency in currencies]
    if pairs != expected_pairs or len(pairs) != len(set(pairs)):
        raise ValueError("Finance FX pairs must cover each source currency once")
    boundary_dates = _resolve_reference(
        calendar.get("boundary_dates_source"), base_config, config, "FX boundaries"
    )
    if calendar.get("boundary_date_count") != len(boundary_dates):
        raise ValueError("Finance FX boundary-date count differs from base config")

    profiles = set(base_config["profiles"])
    regular_counts = calendar.get("regular_date_counts", {})
    total_counts = calendar.get("expected_total_dates", {})
    if set(regular_counts) != profiles or set(total_counts) != profiles:
        raise ValueError("Finance FX calendar profiles are incomplete")
    for profile in profiles:
        regular = regular_counts[profile]
        total = total_counts[profile]
        if not _positive_integer(regular) or not _positive_integer(total):
            raise ValueError(f"Finance FX date counts are invalid for {profile}")
        if regular + len(boundary_dates) != total:
            raise ValueError(f"Finance FX date counts do not reconcile for {profile}")
        target = config["tables"]["fx_rates"]["row_targets"][profile]
        if len(pairs) * total != target:
            raise ValueError(f"Finance FX grid does not match row target for {profile}")


def _validate_generation_targets(
    base_config: dict[str, Any], config: dict[str, Any]
) -> None:
    """Validate rows, distribution targets, imperfections, and capacity."""

    tables = config["tables"]
    profiles = set(base_config["profiles"])
    for table_name, table in tables.items():
        if set(table["row_targets"]) != profiles:
            raise ValueError(f"{table_name} row targets differ from profiles")
        for profile, count in table["row_targets"].items():
            if not _positive_integer(count):
                raise ValueError(f"{table_name}.{profile} row target is invalid")
            if count > base_config["max_rows_per_table"]:
                raise ValueError(f"{table_name}.{profile} exceeds the row cap")

    resolved = resolve_distribution_settings(
        base_config["distribution_defaults"], config["distributions"]
    )
    custom_targets = {"fx_daily_movement"}
    if set(config["distribution_targets"]) != set(resolved) | custom_targets:
        raise ValueError("Finance distribution targets differ from settings")
    for target_name, target in config["distribution_targets"].items():
        if target_name == "fx_daily_movement":
            _require_field(config, target.get("table"), target.get("field"))
            if target.get("distribution") != "bounded_decimal_movement":
                raise ValueError("Finance FX movement target is invalid")
            continue
        settings_key = target.get("settings_key")
        if settings_key not in resolved:
            raise ValueError(f"{target_name} references unknown distribution settings")
        if target.get("distribution") != resolved[settings_key]["name"]:
            raise ValueError(f"{target_name} distribution algorithm differs")
        table_name = target.get("table")
        if table_name is not None:
            _require_field(
                config, table_name, target.get("field") or target.get("group_by")
            )
        for qualified in target.get("targets", []):
            _require_qualified_field(config, qualified)

    _validate_imperfection_targets(base_config, config, resolved)
    _validate_profile_capacities(base_config, config)


def _validate_imperfection_targets(
    base_config: dict[str, Any],
    config: dict[str, Any],
    resolved: dict[str, dict[str, Any]],
) -> None:
    """Validate legal Finance imperfection targets and protected FX capacity."""

    targets = config["imperfection_targets"]
    expected = {
        "missing_fx_rates",
        "near_duplicate_budgets",
        "transaction_amount_outliers",
        "transaction_boundary_dates",
    }
    if set(targets) != expected:
        raise ValueError("Finance imperfection targets differ from contract")

    missing = targets["missing_fx_rates"]
    missing_field = _require_field(config, missing["table"], missing["field"])
    if not missing_field["nullable"] or missing.get("protected_pair") != "USD/USD":
        raise ValueError("Finance missing FX-rate target or protection is invalid")
    if missing.get("protect_boundary_dates") is not True:
        raise ValueError("Finance boundary FX rows must be protected")

    duplicate = targets["near_duplicate_budgets"]
    for name in duplicate["business_key_fields"] + duplicate["variation_fields"]:
        _require_field(config, duplicate["table"], name)
    if "budget_id" in duplicate["business_key_fields"]:
        raise ValueError("Finance budget duplicate key cannot include the primary key")

    outlier = targets["transaction_amount_outliers"]
    outlier_field = _require_field(config, outlier["table"], outlier["field"])
    if outlier_field["type"] != "decimal" or outlier_field["nullable"]:
        raise ValueError("Finance outlier target must be a required decimal")
    if outlier.get("scale") != outlier_field.get("scale"):
        raise ValueError("Finance outlier scale differs from transaction amount")
    if outlier["minimum_value"] <= resolved["transaction_amount"]["max_amount"] or (
        outlier["maximum_value"] < outlier["minimum_value"]
    ):
        raise ValueError("Finance transaction outlier range is invalid")
    if outlier.get("dependent_rebuild") != "ledger_entries":
        raise ValueError("Finance outliers must rebuild dependent ledger entries")

    boundary = targets["transaction_boundary_dates"]
    boundary_field = _require_field(config, boundary["table"], boundary["field"])
    if boundary_field["type"] != "date" or set(
        boundary.get("dependent_updates", [])
    ) != {"transactions.posted_at", "fx_rates"}:
        raise ValueError("Finance transaction boundary target is invalid")

    for name, target in targets.items():
        reference = target.get("rate_source") or target.get("values_source")
        _resolve_reference(reference, base_config, config, name)

    null_pct = float(
        _resolve_reference(
            missing["rate_source"], base_config, config, "missing FX rates"
        )
    )
    for profile in base_config["profiles"]:
        fx_rows = config["tables"]["fx_rates"]["row_targets"][profile]
        date_count = config["generation_rules"]["fx_calendar"]["expected_total_dates"][
            profile
        ]
        boundary_count = config["generation_rules"]["fx_calendar"][
            "boundary_date_count"
        ]
        protected = date_count + (
            (len(config["domain_values"]["source_currencies"]) - 1) * boundary_count
        )
        if count_from_pct(fx_rows, null_pct) > fx_rows - protected:
            raise ValueError(f"Finance FX NULL target exceeds capacity for {profile}")


def _validate_profile_capacities(
    base_config: dict[str, Any], config: dict[str, Any]
) -> None:
    """Validate budget counts and exact even ledger capacity per profile."""

    rules = config["generation_rules"]
    duplicate_pct = float(
        _resolve_reference(
            config["imperfection_targets"]["near_duplicate_budgets"]["rate_source"],
            base_config,
            config,
            "budget duplicates",
        )
    )
    for profile in base_config["profiles"]:
        account_rows = config["tables"]["accounts"]["row_targets"][profile]
        budget_rows = config["tables"]["budgets"]["row_targets"][profile]
        periods = rules["budgets"]["periods_per_account"].get(profile)
        if not _positive_integer(periods) or account_rows * periods != budget_rows:
            raise ValueError(
                f"Finance budget rows do not equal account-period capacity for {profile}"
            )
        final_budgets = budget_rows + count_from_pct(budget_rows, duplicate_pct)
        if final_budgets > base_config["max_rows_per_table"]:
            raise ValueError(f"Finance final budgets exceed row cap for {profile}")

        transaction_rows = config["tables"]["transactions"]["row_targets"][profile]
        ledger_rows = config["tables"]["ledger_entries"]["row_targets"][profile]
        if ledger_rows % 2:
            raise ValueError(f"Finance ledger row target must be even for {profile}")
        unposted_fraction = rules["transactions"]["unposted_to_ledger_fraction"]
        posted_rows = transaction_rows - round(transaction_rows * unposted_fraction)
        minimum_lines = rules["ledger_entries"]["minimum_lines_per_posted_transaction"]
        if ledger_rows < posted_rows * minimum_lines:
            raise ValueError(f"Finance ledger target lacks capacity for {profile}")


def _validate_join_paths(config: dict[str, Any]) -> None:
    """Validate Finance join IDs, tables, required fields, and result semantics."""

    paths = config["join_path_requirements"]
    ids = [path.get("id") for path in paths]
    if ids != EXPECTED_JOIN_PATH_IDS:
        raise ValueError(
            f"Finance join path IDs differ: expected={EXPECTED_JOIN_PATH_IDS}, "
            f"actual={ids}"
        )
    valid_results = {
        "non_empty",
        "non_empty_with_unmatched_parent_rows",
        "non_empty_with_matched_and_unmatched_rows",
        "non_empty_with_matched_and_unmatched_parent_rows",
        "complete_unique_lookup_coverage",
    }
    for path in paths:
        path_id = path["id"]
        if path.get("tables") != EXPECTED_JOIN_TABLES[path_id]:
            raise ValueError(f"{path_id} tables differ from the Finance contract")
        if path.get("required_result") not in valid_results:
            raise ValueError(f"{path_id} has unsupported result requirement")
        if "unmatched" in path["required_result"] and not path.get(
            "unmatched_condition"
        ):
            raise ValueError(f"{path_id} lacks an unmatched condition")
        if (
            not isinstance(path.get("join_condition"), str)
            or not path["join_condition"].strip()
        ):
            raise ValueError(f"{path_id} has an invalid join condition")
        for qualified in EXPECTED_JOIN_FIELDS[path_id]:
            _require_qualified_field(config, qualified)
        if path_id in {"finance_jp_006", "finance_jp_007", "finance_jp_008"}:
            condition = path["join_condition"]
            for field in EXPECTED_JOIN_FIELDS["finance_jp_006"]:
                if field not in condition:
                    raise ValueError(f"{path_id} omits composite FX field {field}")
        if (
            path_id == "finance_jp_007"
            and "rate IS NOT NULL" not in path["join_condition"]
        ):
            raise ValueError("finance_jp_007 must require a populated FX rate")
        if path_id == "finance_jp_008" and "rate IS NULL" not in path["join_condition"]:
            raise ValueError("finance_jp_008 must require a NULL FX rate")


def _validate_decimal_policy(config: dict[str, Any]) -> None:
    """Validate one consistent fixed-point policy across release and generation."""

    expected = {
        "money_precision": 19,
        "money_scale": 4,
        "fx_precision": 19,
        "fx_scale": 6,
        "rounding_mode": "ROUND_HALF_UP",
        "conversion_order": "multiply_first_round_once",
    }
    release = config["precision_metadata"]
    policy = config["decimal_policy"]
    if release != expected:
        raise ValueError("Finance release precision metadata differs from contract")
    if {name: policy.get(name) for name in expected} != expected or policy.get(
        "binary_float_prohibited"
    ) is not True:
        raise ValueError("Finance decimal policy differs from contract")
    for table_name, field_name in (
        ("transactions", "total_amount"),
        ("ledger_entries", "debit_amount"),
        ("ledger_entries", "credit_amount"),
        ("budgets", "budget_amount"),
    ):
        field = _require_field(config, table_name, field_name)
        if (field.get("precision"), field.get("scale")) != (19, 4):
            raise ValueError(f"{table_name}.{field_name} must be DECIMAL(19,4)")
    rate = _require_field(config, "fx_rates", "rate")
    if (rate.get("precision"), rate.get("scale")) != (19, 6):
        raise ValueError("fx_rates.rate must be DECIMAL(19,6)")


def _validate_executable_checks(config: dict[str, Any]) -> None:
    """Require critical currency, positivity, and ledger-side DDL checks."""

    constraints = ddl_constraints(PROJECT_ROOT / config["schema_source"])
    for table_name, fragments in EXPECTED_CHECK_FRAGMENTS.items():
        checks = constraints["checks"].get(table_name, set())
        combined = "|".join(checks)
        for fragment in fragments:
            if fragment not in combined:
                raise ValueError(
                    f"Finance DDL lacks required {table_name} check: {fragment}"
                )

    currency_values = ",".join(
        f"'{currency.lower()}'"
        for currency in config["domain_values"]["source_currencies"]
    )
    for table_name, field_name in (
        ("accounts", "currency_code"),
        ("transactions", "source_currency"),
        ("ledger_entries", "currency_code"),
        ("fx_rates", "from_currency"),
    ):
        expected = f"check{field_name}in{currency_values}"
        if expected not in constraints["checks"].get(table_name, set()):
            raise ValueError(
                f"Finance DDL {table_name}.{field_name} currency check differs "
                "from configured source currencies"
            )


def _resolve_reference(
    reference: Any,
    base_config: dict[str, Any],
    finance_config: dict[str, Any],
    context: str,
) -> Any:
    """Resolve a dotted reference against shared or assembled Finance config."""

    if not isinstance(reference, str) or not reference:
        raise ValueError(f"{context} has an invalid config reference")
    if ":" in reference:
        source, dotted_path = reference.split(":", 1)
        source_name = Path(source).name
        if source_name == "base.json":
            current: Any = base_config
        elif source_name == "finance.json":
            current = finance_config
        else:
            raise ValueError(f"{context} references unsupported config: {source}")
    else:
        dotted_path = reference
        current = finance_config
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ValueError(f"{context} references missing config value: {reference}")
        current = current[part]
    return current


def _require_field(
    config: dict[str, Any], table_name: Any, field_name: Any
) -> dict[str, Any]:
    """Return a configured Finance field or raise a contextual error."""

    if table_name not in config["tables"]:
        raise ValueError(f"Unknown Finance table: {table_name}")
    for field in config["tables"][table_name]["fields"]:
        if field["name"] == field_name:
            return field
    raise ValueError(f"Unknown Finance field: {table_name}.{field_name}")


def _require_qualified_field(config: dict[str, Any], value: Any) -> None:
    """Validate a Finance `table.field` reference."""

    if not isinstance(value, str) or value.count(".") != 1:
        raise ValueError(f"Invalid Finance field reference: {value}")
    _require_field(config, *value.split("."))


def _validate_weights(mapping: Any, values: list[str], path: str) -> None:
    """Validate complete non-negative weights that sum to one."""

    if not isinstance(mapping, dict) or set(mapping) != set(values):
        raise ValueError(f"{path} must cover its configured domain values")
    if any(
        not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight < 0
        for weight in mapping.values()
    ):
        raise ValueError(f"{path} contains invalid weights")
    if abs(sum(mapping.values()) - 1.0) > 1e-9:
        raise ValueError(f"{path} weights must sum to 1")


def _validate_integer_range(value: Any, path: str, minimum: int = 1) -> None:
    """Validate an inclusive integer range with a configured lower bound."""

    if not isinstance(value, dict) or set(value) != {"minimum", "maximum"}:
        raise ValueError(f"{path} must define minimum and maximum")
    if (
        not all(
            isinstance(value[name], int) and not isinstance(value[name], bool)
            for name in ("minimum", "maximum")
        )
        or value["minimum"] < minimum
        or value["maximum"] < value["minimum"]
    ):
        raise ValueError(f"{path} has an invalid integer range")


def _validate_probability(value: Any, path: str) -> None:
    """Validate a probability in the inclusive range zero to one."""

    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{path} must be between 0 and 1")


def _decimal(value: Any, path: str) -> Decimal:
    """Parse one config value as a finite Decimal."""

    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{path} must be a decimal value") from exc
    if not parsed.is_finite():
        raise ValueError(f"{path} must be finite")
    return parsed


def _decimal_places(value: Decimal) -> int:
    """Return the number of decimal places represented by a Decimal."""

    return max(0, -value.as_tuple().exponent)


def _positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


__all__ = ["validate_finance_config"]
