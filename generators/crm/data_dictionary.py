"""CRM data dictionary generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.manifest import build_distribution_metadata
from generators.crm.config import load_crm_config
from generators.crm.config import settings_for_profile
from generators.crm.validators.config import validate_crm_config


TABLE_DESCRIPTIONS = {
    "accounts": (
        "Customer-organization dimension used for segmentation and account-level "
        "engagement and support analysis."
    ),
    "contacts": (
        "Customer-contact dimension. Contacts may be unassigned to an account and "
        "may participate in multiple campaigns."
    ),
    "campaigns": (
        "USD-only campaign dimension used for participation, channel, budget, and "
        "attributed-engagement analysis."
    ),
    "contact_campaigns": (
        "Explicit many-to-many bridge between contacts and campaigns, including "
        "membership and multi-touch attribution attributes."
    ),
    "interactions": (
        "Engagement-event fact table used to derive contact, account, channel, and "
        "campaign engagement scores."
    ),
    "support_cases": (
        "Customer-support fact table used for case volume, backlog, resolution "
        "duration, and SLA compliance."
    ),
}


FIELD_DESCRIPTIONS = {
    "accounts.account_id": "Unique customer-account identifier.",
    "accounts.account_name": "Synthetic customer-organization name.",
    "accounts.account_size": "Synthetic numeric company-size measure.",
    "accounts.industry": "Synthetic customer industry classification.",
    "accounts.region": "Synthetic geographic or operational region.",
    "accounts.customer_tier": "Customer relationship tier.",
    "accounts.is_active": "Whether the customer account is active.",
    "accounts.created_at": "Timestamp when the account record was created.",
    "contacts.contact_id": "Unique customer-contact identifier.",
    "contacts.account_id": "Optional account to which the contact belongs.",
    "contacts.first_name": "Synthetic contact first name.",
    "contacts.last_name": "Synthetic contact last name.",
    "contacts.email": "Synthetic contact email using a non-production test domain.",
    "contacts.address": "Synthetic single-line contact address.",
    "contacts.title": "Synthetic contact job title.",
    "contacts.is_active": "Whether the contact is active.",
    "contacts.created_at": "Timestamp when the contact record was created.",
    "campaigns.campaign_id": "Unique campaign identifier.",
    "campaigns.campaign_name": "Synthetic campaign name.",
    "campaigns.campaign_type": "Campaign business objective or program type.",
    "campaigns.primary_channel": "Primary delivery channel for the campaign.",
    "campaigns.status": "Current lifecycle state of the campaign.",
    "campaigns.start_date": "Calendar date on which the campaign starts.",
    "campaigns.end_date": "Optional campaign completion date.",
    "campaigns.budget_amount": "Optional campaign budget denominated in USD.",
    "campaigns.currency_code": "Fixed campaign currency code; always USD.",
    "campaigns.created_at": "Timestamp when the campaign record was created.",
    "contact_campaigns.contact_id": (
        "Contact reference and first field of the composite primary key."
    ),
    "contact_campaigns.campaign_id": (
        "Campaign reference and second field of the composite primary key."
    ),
    "contact_campaigns.member_status": "Contact status within the campaign.",
    "contact_campaigns.first_touch_at": (
        "Timestamp of the contact's first campaign touch."
    ),
    "contact_campaigns.last_touch_at": (
        "Timestamp of the contact's most recent campaign touch."
    ),
    "contact_campaigns.attribution_weight": (
        "Contact-level multi-touch attribution weight from 0.0000 through 1.0000."
    ),
    "contact_campaigns.is_primary_attribution": (
        "Whether this is the contact's primary attributed campaign."
    ),
    "contact_campaigns.created_at": (
        "Timestamp when the campaign-membership record was created."
    ),
    "interactions.interaction_id": "Unique engagement-event identifier.",
    "interactions.contact_id": "Contact associated with the engagement event.",
    "interactions.account_id": (
        "Optional direct account reference; it must agree with the contact account."
    ),
    "interactions.campaign_id": (
        "Optional attributed campaign; NULL means organic or unattributed."
    ),
    "interactions.engagement_type": "Business type of engagement event.",
    "interactions.channel": "Channel derived from the engagement type.",
    "interactions.direction": "Inbound or outbound interaction direction.",
    "interactions.engagement_points": (
        "Non-negative event points used to derive engagement scores."
    ),
    "interactions.interaction_at": "Timestamp when the engagement occurred.",
    "interactions.created_at": "Timestamp when the interaction record was created.",
    "support_cases.case_id": "Unique support-case identifier.",
    "support_cases.account_id": "Customer account associated with the case.",
    "support_cases.contact_id": "Optional contact who requested support.",
    "support_cases.case_number": "Unique synthetic business-facing case reference.",
    "support_cases.subject": "Synthetic short summary of the support request.",
    "support_cases.category": "Business category of the support request.",
    "support_cases.priority": "Priority used to derive the SLA deadline.",
    "support_cases.status": "Current support-case lifecycle state.",
    "support_cases.opened_at": "Timestamp when the support case was opened.",
    "support_cases.sla_due_at": "Priority-based case-resolution deadline.",
    "support_cases.resolved_at": (
        "Resolution timestamp; populated only for Resolved or Closed cases."
    ),
    "support_cases.created_at": "Timestamp when the case record was created.",
}


DOMAIN_VALUE_LABELS = {
    "industries": "Account industries",
    "regions": "Account regions",
    "customer_tiers": "Customer tiers",
    "contact_titles": "Contact titles",
    "campaign_types": "Campaign types",
    "campaign_channels": "Campaign channels",
    "campaign_statuses": "Campaign statuses",
    "campaign_member_statuses": "Campaign member statuses",
    "engagement_types": "Engagement types",
    "interaction_directions": "Interaction directions",
    "support_case_categories": "Support-case categories",
    "support_case_priorities": "Support-case priorities",
    "support_case_statuses": "Support-case statuses",
}


class CRMDataDictionaryGenerator:
    """Generate CRM release data dictionary from DDL-aligned config metadata."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMDataDictionaryGenerator only supports the crm domain")
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings
        self.crm_config = load_crm_config()
        self._validate_description_coverage()

    @classmethod
    def for_profile(cls, profile: str) -> "CRMDataDictionaryGenerator":
        """Create a CRM data dictionary generator from config files."""

        settings = settings_for_profile(profile)
        return cls(DeterministicGenerator(settings))

    def generate_markdown(self) -> str:
        """Return the CRM data dictionary as deterministic Markdown."""

        lines = [
            "# CRM Data Dictionary",
            "",
            f"Dataset version: `{self.settings.dataset_version}`",
            "",
            f"Schema source: `{self.crm_config['schema_source']}`",
            "",
            (
                "This dictionary documents the engagement-focused CRM contract. "
                "Sales pipeline and revenue concepts are outside this domain."
            ),
            "",
            "CRM campaign monetary values are USD-only. Mixed-currency and FX "
            "conversion belong to Finance.",
            "",
            "## Tables",
            "",
        ]
        for table_name in self.settings.table_order:
            lines.extend(
                self._table_section(
                    table_name,
                    self.crm_config["tables"][table_name],
                )
            )

        lines.extend(self._domain_values_section())
        lines.extend(self._generation_rules_section())
        lines.extend(self._distribution_configuration_section())
        lines.extend(self._business_rules_section())
        lines.extend(self._derived_metrics_section())
        lines.extend(self._relationship_section())
        lines.extend(self._imperfection_section())
        return "\n".join(lines).rstrip() + "\n"

    def write_release_dictionary(self) -> Path:
        """Write data_dictionary.md into the unsealed release directory."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Step 17 generates the release data dictionary only for the full "
                f"profile; got profile={self.settings.profile}"
            )
        output_path = self.settings.output_path / "data_dictionary.md"
        manifest_path = self.settings.output_path / "manifest.json"
        if manifest_path.exists():
            raise FileExistsError(
                "Refusing to modify immutable release after manifest exists: "
                f"{manifest_path}"
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_suffix(".md.tmp")
        tmp_path.write_text(self.generate_markdown(), encoding="utf-8", newline="\n")
        tmp_path.replace(output_path)
        return output_path

    def _validate_description_coverage(self) -> None:
        expected = {
            f"{table_name}.{field['name']}"
            for table_name in self.settings.table_order
            for field in self.crm_config["tables"][table_name]["fields"]
        }
        actual = set(FIELD_DESCRIPTIONS)
        if actual != expected:
            raise ValueError(
                "CRM field-description coverage differs from config: "
                f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
        if set(TABLE_DESCRIPTIONS) != set(self.settings.table_order):
            raise ValueError("CRM table-description coverage differs from table_order")

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
                f"Row targets: dev `{table_config['row_targets']['dev']}`, "
                f"full `{table_config['row_targets']['full']}`"
            ),
            "",
            "| Column | Type | Nullable | Key | References | Business meaning | Generation behavior | NULL / imperfection behavior |",
            "|---|---|---|---|---|---|---|---|",
        ]
        lines.extend(_field_row(table_name, field) for field in table_config["fields"])
        lines.append("")
        return lines

    def _domain_values_section(self) -> list[str]:
        lines = ["## Domain Values", ""]
        for config_name, label in DOMAIN_VALUE_LABELS.items():
            values = ", ".join(
                f"`{value}`" for value in self.crm_config["domain_values"][config_name]
            )
            lines.append(f"- {label}: {values}.")
        lines.append("")
        return lines

    def _business_rules_section(self) -> list[str]:
        mappings = self.crm_config["business_mappings"]
        lines = [
            "## Business Rules",
            "",
            "### Engagement Points",
            "",
            "| Engagement type | Points | Channel |",
            "|---|---:|---|",
        ]
        for engagement_type, points in mappings["engagement_points"].items():
            channel = mappings["engagement_channels"][engagement_type]
            lines.append(f"| `{engagement_type}` | {points} | `{channel}` |")

        lines.extend(
            [
                "",
                "### Support-Case SLA",
                "",
                "SLA deadlines use calendar hours from `opened_at`.",
                "",
                "| Priority | Calendar hours |",
                "|---|---:|",
            ]
        )
        for priority, hours in mappings["sla_calendar_hours"].items():
            lines.append(f"| `{priority}` | {hours} |")

        attribution = mappings["attribution"]
        lines.extend(
            [
                "",
                "### Campaign Attribution",
                "",
                "- Attribution is scoped per contact through `contact_campaigns`.",
                (
                    "- Complete pre-imperfection weights sum to "
                    f"`{attribution['expected_sum_before_imperfections']}` per contact."
                ),
                (
                    "- Individual weights range from "
                    f"`{attribution['minimum_weight']}` through "
                    f"`{attribution['maximum_weight']}` at scale "
                    f"`{attribution['weight_scale']}`."
                ),
                (
                    "- A contact has at most "
                    f"`{attribution['maximum_primary_campaigns_per_contact']}` "
                    "primary attributed campaign."
                ),
                "- An attributed interaction must match an existing contact/campaign membership.",
                "- A NULL interaction campaign identifies organic or unattributed engagement.",
                "",
            ]
        )
        return lines

    def _generation_rules_section(self) -> list[str]:
        return [
            "## Base Generation Rules",
            "",
            (
                "These deterministic parameters control clean base generation and "
                "are recorded in the release manifest."
            ),
            "",
            "```json",
            json.dumps(self.crm_config["generation_rules"], indent=2),
            "```",
            "",
        ]

    def _distribution_configuration_section(self) -> list[str]:
        metadata = build_distribution_metadata(
            self.crm_config["distributions"],
            self.settings.distributions,
            self.crm_config["distribution_targets"],
        )
        lines = [
            "## Distribution Configuration",
            "",
            (
                "Shared presets in `config/generation/base.json` are reusable "
                "defaults, not mandatory values for every domain. CRM selects a "
                "preset and may override its parameters in "
                "`config/generation/crm/generation.json`, assembled through "
                "`config/generation/crm.json`."
            ),
            "",
            "| Setting | Shared preset | CRM overrides | Effective parameters | Targets |",
            "|---|---|---|---|---|",
        ]
        for settings_key, spec in metadata.items():
            overrides = (
                _compact_json(spec["overrides"]) if spec["overrides"] else "None"
            )
            effective = _compact_json(spec["effective_parameters"])
            targets = ", ".join(
                _distribution_target_text(target) for target in spec["targets"].values()
            )
            lines.append(
                f"| `{settings_key}` | `{spec['preset']}` | {overrides} | "
                f"{effective} | {targets} |"
            )
        lines.append("")
        return lines

    def _derived_metrics_section(self) -> list[str]:
        mappings = self.crm_config["business_mappings"]
        unresolved = _sql_string_list(mappings["unresolved_case_statuses"])
        resolved = _sql_string_list(mappings["resolved_case_statuses"])
        return [
            "## Derived Metrics",
            "",
            "| Metric | Definition |",
            "|---|---|",
            "| Contact engagement score | `SUM(interactions.engagement_points)` grouped by contact. |",
            "| Account engagement score | Sum interaction points through the populated account path or contact/account join. |",
            "| Campaign engagement score | Sum interaction points grouped by non-NULL `campaign_id`. |",
            f"| Open case | `status IN ({unresolved})` and `resolved_at IS NULL`. |",
            f"| Resolved case | `status IN ({resolved})` and `resolved_at IS NOT NULL`. |",
            "| Resolved within SLA | `resolved_at <= sla_due_at`. |",
            "| Resolved SLA breach | `resolved_at > sla_due_at`. |",
            "| Open SLA breach | `resolved_at IS NULL` and fixed `reference_today > sla_due_at`. |",
            "| Resolution duration | `resolved_at - opened_at`. |",
            "",
        ]

    def _relationship_section(self) -> list[str]:
        lines = [
            "## Relationships",
            "",
            "| Parent | Child | Cardinality | Join type |",
            "|---|---|---|---|",
        ]
        for relationship in self.crm_config["relationships"]:
            parent = f"`{relationship['parent_table']}.{relationship['parent_field']}`"
            child = f"`{relationship['child_table']}.{relationship['child_field']}`"
            lines.append(
                f"| {parent} | {child} | `{relationship['cardinality']}` | "
                f"`{relationship['join_type']}` |"
            )
        lines.extend(
            [
                "",
                "`contact_campaigns` is the explicit many-to-many bridge: one contact "
                "may participate in multiple campaigns and one campaign may contain "
                "multiple contacts.",
                "",
            ]
        )
        return lines

    def _imperfection_section(self) -> list[str]:
        imperfections = self.settings.imperfections
        boundary_dates = ", ".join(
            f"`{value}`" for value in imperfections["boundary_dates"]
        )
        return [
            "## Controlled Imperfections",
            "",
            (
                "- Typo-based near-duplicate contacts: "
                f"`{imperfections['duplicate_pct']}%` appended rows."
            ),
            (
                "- Missing `contact_campaigns.attribution_weight`: "
                f"`{imperfections['null_pct']}%`."
            ),
            (
                "- `interactions.engagement_points` outliers from 50 through 100: "
                f"`{imperfections['outlier_pct']}%`."
            ),
            f"- `support_cases.opened_at` boundary dates at midnight: {boundary_dates}.",
            "",
            (
                "Business-state NULLs are separate from controlled imperfections. "
                "They represent optional attributes, open-ended campaigns, organic "
                "interactions, account-level cases, or unresolved cases."
            ),
            "",
        ]


def _field_row(table_name: str, field: dict[str, Any]) -> str:
    field_name = field["name"]
    references = field.get("references")
    reference_text = (
        f"`{references['table']}.{references['field']}`" if references else ""
    )
    key_text = f"`{field['key']}`" if field.get("key") else ""
    return (
        f"| `{field_name}` | `{_type_text(field)}` | "
        f"`{str(field['nullable']).lower()}` | {key_text} | {reference_text} | "
        f"{FIELD_DESCRIPTIONS[f'{table_name}.{field_name}']} | "
        f"{_generation_text(field)} | {_imperfection_text(table_name, field)} |"
    )


def _sql_string_list(values: list[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _compact_json(value: Any) -> str:
    return f"`{json.dumps(value, sort_keys=True, separators=(',', ':'))}`"


def _distribution_target_text(target: dict[str, Any]) -> str:
    if "targets" in target:
        return ", ".join(f"`{value}`" for value in target["targets"])
    table_name = target["table"]
    if "field" in target:
        return f"`{table_name}.{target['field']}`"
    if "group_by" in target:
        return f"`{table_name}` grouped by `{target['group_by']}`"
    return f"`{table_name}`"


def _type_text(field: dict[str, Any]) -> str:
    field_type = field["type"]
    if field_type in {"varchar", "char"}:
        return f"{field_type}({field['max_length']})"
    if field_type == "decimal":
        return f"decimal({field['precision']},{field['scale']})"
    return str(field_type)


def _generation_text(field: dict[str, Any]) -> str:
    parts: list[str] = []
    if "synthetic_source" in field:
        parts.append(f"Source `{field['synthetic_source']}`")
    else:
        parts.append("Deterministic entity identifier")
    if "distribution" in field:
        parts.append(f"distribution `{field['distribution']}`")
    if "default" in field:
        default = field["default"]
        if isinstance(default, bool):
            default = str(default).lower()
        parts.append(f"default `{default}`")
    return "; ".join(parts) + "."


def _imperfection_text(table_name: str, field: dict[str, Any]) -> str:
    field_key = f"{table_name}.{field['name']}"
    behaviors: list[str] = []
    if field_key in {"contacts.first_name", "contacts.email"}:
        behaviors.append("May contain a typo variation in appended duplicate rows.")
    if field.get("imperfection_target") == "controlled_null":
        behaviors.append("Controlled NULL target.")
    if field.get("imperfection_target") == "high_value_outlier":
        behaviors.append("Controlled high-value integer outlier target.")
    if field_key == "support_cases.opened_at":
        behaviors.append("Receives each configured boundary timestamp.")
    if field_key in {"support_cases.sla_due_at", "support_cases.resolved_at"}:
        behaviors.append("Rederived when a boundary opening is injected.")

    null_semantics = field.get("null_semantics")
    if null_semantics:
        behaviors.append(f"Business-state NULL: {null_semantics}")
    elif field["nullable"] and not behaviors:
        behaviors.append("Optional business attribute; not a controlled NULL target.")
    elif not behaviors:
        behaviors.append("Not an imperfection target.")
    return " ".join(behaviors)
