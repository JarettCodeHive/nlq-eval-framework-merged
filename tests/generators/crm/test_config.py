from __future__ import annotations

from copy import deepcopy
import inspect

import pytest

import generators.crm.config as config_module
import generators.crm.distributions as distributions_module
import generators.crm.generator as generator_module
import generators.crm.imperfections as imperfections_module
from generators.crm.config import load_crm_config
from generators.crm.config import settings_for_profile
from generators.crm.config import validate_crm_config
from generators.crm.generator import CRM_COLUMN_CONTRACTS


EXPECTED_COLUMNS = {
    "accounts": [
        "account_id",
        "account_name",
        "account_size",
        "industry",
        "region",
        "customer_tier",
        "is_active",
        "created_at",
    ],
    "contacts": [
        "contact_id",
        "account_id",
        "first_name",
        "last_name",
        "email",
        "address",
        "title",
        "is_active",
        "created_at",
    ],
    "campaigns": [
        "campaign_id",
        "campaign_name",
        "campaign_type",
        "primary_channel",
        "status",
        "start_date",
        "end_date",
        "budget_amount",
        "currency_code",
        "created_at",
    ],
    "contact_campaigns": [
        "contact_id",
        "campaign_id",
        "member_status",
        "first_touch_at",
        "last_touch_at",
        "attribution_weight",
        "is_primary_attribution",
        "created_at",
    ],
    "interactions": [
        "interaction_id",
        "contact_id",
        "account_id",
        "campaign_id",
        "engagement_type",
        "channel",
        "direction",
        "engagement_points",
        "interaction_at",
        "created_at",
    ],
    "support_cases": [
        "case_id",
        "account_id",
        "contact_id",
        "case_number",
        "subject",
        "category",
        "priority",
        "status",
        "opened_at",
        "sla_due_at",
        "resolved_at",
        "created_at",
    ],
}


def test_crm_config_validates_against_schema() -> None:
    validate_crm_config()


def test_config_validator_does_not_mirror_json_contract_values() -> None:
    source = inspect.getsource(config_module)

    for removed_constant in (
        "EXPECTED_TABLE_ORDER",
        "EXPECTED_JOIN_PATHS",
        "EXPECTED_JOIN_PATH_RULES",
        "EXPECTED_DOMAIN_VALUES",
        "EXPECTED_CHECK_FRAGMENTS",
    ):
        assert removed_constant not in source


def test_config_accepts_domain_value_extension_without_python_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm_config = deepcopy(load_crm_config())
    crm_config["domain_values"]["campaign_types"].append("Advocacy")
    monkeypatch.setattr(config_module, "load_crm_config", lambda: crm_config)

    validate_crm_config()


def test_schema_alignment_still_rejects_field_drift() -> None:
    crm_config = deepcopy(load_crm_config())
    crm_config["tables"]["accounts"]["fields"].pop()

    with pytest.raises(ValueError, match="accounts fields differ"):
        config_module._validate_schema_alignment(crm_config)


def test_generation_target_still_rejects_unknown_fields() -> None:
    base_config = config_module.load_base_config()
    crm_config = deepcopy(load_crm_config())
    crm_config["distribution_targets"]["campaign_budget"]["field"] = "missing"

    with pytest.raises(ValueError, match="references unknown field"):
        config_module._validate_generation_targets(base_config, crm_config)


def test_crm_settings_use_engagement_table_order() -> None:
    settings = settings_for_profile("dev")

    assert settings.table_order == tuple(EXPECTED_COLUMNS)


def test_crm_config_columns_match_engagement_schema() -> None:
    crm_config = load_crm_config()

    configured_columns = {
        table_name: [
            field["name"] for field in crm_config["tables"][table_name]["fields"]
        ]
        for table_name in crm_config["table_order"]
    }

    assert configured_columns == EXPECTED_COLUMNS


def test_crm_generator_column_contracts_match_config() -> None:
    assert CRM_COLUMN_CONTRACTS == EXPECTED_COLUMNS


def test_crm_config_contains_contact_campaign_bridge() -> None:
    crm_config = load_crm_config()
    table_config = crm_config["tables"]["contact_campaigns"]
    fields = {field["name"]: field for field in table_config["fields"]}

    assert table_config["primary_key"] == ["contact_id", "campaign_id"]
    assert table_config["role"] == "junction"
    assert fields["is_primary_attribution"]["default"] is False
    assert table_config["row_targets"] == {"dev": 750, "full": 180000}
    assert crm_config["join_path_requirements"][3]["tables"] == [
        "contacts",
        "contact_campaigns",
        "campaigns",
    ]


def test_crm_config_defines_engagement_and_sla_mappings() -> None:
    crm_config = load_crm_config()
    mappings = crm_config["business_mappings"]

    assert mappings["currency_code"] == "USD"
    assert mappings["engagement_points"] == {
        "EmailOpen": 1,
        "LinkClick": 3,
        "PhoneCall": 5,
        "WebinarAttendance": 7,
        "FormSubmission": 8,
        "EventAttendance": 8,
        "Meeting": 10,
    }
    assert mappings["sla_calendar_hours"] == {
        "Low": 72,
        "Medium": 48,
        "High": 24,
        "Critical": 4,
    }
    assert set(mappings["support_case_subject_by_category"]) == set(
        crm_config["domain_values"]["support_case_categories"]
    )


def test_crm_config_rejects_incomplete_support_case_subject_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm_config = deepcopy(load_crm_config())
    subject_mapping = crm_config["business_mappings"][
        "support_case_subject_by_category"
    ]
    subject_mapping.pop(next(iter(subject_mapping)))
    monkeypatch.setattr(config_module, "load_crm_config", lambda: crm_config)

    with pytest.raises(ValueError, match="must match support_case_categories"):
        validate_crm_config()


def test_crm_config_rejects_generation_weights_that_do_not_sum_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm_config = deepcopy(load_crm_config())
    crm_config["generation_rules"]["accounts"]["customer_tier_weights"]["Standard"] = (
        0.5
    )
    monkeypatch.setattr(config_module, "load_crm_config", lambda: crm_config)

    with pytest.raises(ValueError, match="weights must sum to 1"):
        validate_crm_config()


def test_crm_config_rejects_unknown_campaign_status_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm_config = deepcopy(load_crm_config())
    crm_config["business_mappings"]["campaign_status_rules"]["future_start"].append(
        "Unknown"
    )
    monkeypatch.setattr(config_module, "load_crm_config", lambda: crm_config)

    with pytest.raises(ValueError, match="contains unknown statuses"):
        validate_crm_config()


def test_crm_config_defines_revised_distribution_targets() -> None:
    settings = settings_for_profile("dev")
    targets = load_crm_config()["distribution_targets"]

    assert set(settings.distributions) == {
        "campaign_budget",
        "interaction_frequency",
        "date_clustering",
    }
    assert targets["campaign_budget"]["field"] == "budget_amount"
    assert targets["interaction_frequency"]["group_by"] == "contact_id"
    assert targets["support_case_duration"]["anchor_field"] == "opened_at"


def test_crm_config_has_no_sales_pipeline_tables() -> None:
    crm_config = load_crm_config()

    assert not {
        "opportunities",
        "contact_opportunities",
        "activities",
    }.intersection(crm_config["tables"])


def test_generation_uses_no_wall_clock_or_external_dataset_inputs() -> None:
    source = "\n".join(
        inspect.getsource(module)
        for module in (
            generator_module,
            distributions_module,
            imperfections_module,
        )
    )

    for forbidden_call in (
        "date.today(",
        "datetime.now(",
        "datetime.utcnow(",
        "pd.read_csv(",
        "pd.read_sql(",
    ):
        assert forbidden_call not in source
