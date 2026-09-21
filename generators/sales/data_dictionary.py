"""Sales release data-dictionary generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.manifest import build_distribution_metadata
from generators.sales.config import load_sales_config
from generators.sales.config import settings_for_profile
from generators.sales.validators.config import validate_sales_config


TABLE_DESCRIPTIONS = {
    "leads": (
        "Prospect dimension used for source, status, ownership, territory, score, "
        "and conversion analysis."
    ),
    "deals": (
        "Sales-pipeline transaction dimension. Every deal originates from one "
        "converted lead and retains that lead's representative ownership."
    ),
    "products": (
        "Synthetic product catalog used for category, availability, and current "
        "catalog-price analysis."
    ),
    "quotations": (
        "Quotation-line fact and junction table connecting deals to products, with "
        "captured prices, quantities, discounts, and quote lifecycle status."
    ),
    "targets": (
        "Representative-period quota reference table used for attained revenue and "
        "quota-attainment analysis."
    ),
}


FIELD_DESCRIPTIONS = {
    "leads.lead_id": "Unique lead identifier.",
    "leads.lead_name": "Synthetic prospect or lead name.",
    "leads.company_name": "Synthetic prospect company name.",
    "leads.lead_source": "Channel through which the lead entered the pipeline.",
    "leads.lead_status": "Current lead qualification or conversion state.",
    "leads.rep_name": "Synthetic sales representative who owns the lead.",
    "leads.territory": "Sales territory assigned through the lead representative.",
    "leads.score": "Synthetic lead score from 0 through 100.",
    "leads.created_at": "Timestamp when the lead record was created.",
    "deals.deal_id": "Unique deal identifier.",
    "deals.lead_id": "Converted lead from which the deal originated.",
    "deals.deal_name": "Synthetic business-facing deal name.",
    "deals.rep_name": "Representative copied from the source lead.",
    "deals.stage": "Current pipeline stage of the deal.",
    "deals.deal_amount": "USD pipeline amount associated with the deal.",
    "deals.currency_code": "Fixed deal currency code; always USD.",
    "deals.close_date": "Actual close date for a won or lost deal.",
    "deals.expected_close_date": "Expected calendar date for deal closure.",
    "deals.created_at": "Timestamp when the deal record was created.",
    "products.product_id": "Unique product identifier.",
    "products.sku": "Unique synthetic stock-keeping unit.",
    "products.product_name": "Synthetic product name.",
    "products.category": "Commercial category assigned to the product.",
    "products.list_price": "Current USD catalog price, when available.",
    "products.currency_code": "Fixed product currency code; always USD.",
    "products.is_active": "Whether the product is currently active.",
    "products.created_at": "Timestamp when the product record was created.",
    "quotations.quotation_id": "Unique quotation-line identifier.",
    "quotations.deal_id": "Deal receiving the quoted line item.",
    "quotations.product_id": "Product represented by the quoted line item.",
    "quotations.quote_number": "Synthetic business-facing quotation reference.",
    "quotations.quantity": "Number of product units on the quoted line.",
    "quotations.unit_price": "USD unit price captured when the quotation was made.",
    "quotations.discount_pct": "Percentage discount applied to the quoted line.",
    "quotations.quote_status": "Current quotation lifecycle state.",
    "quotations.quoted_at": "Timestamp when the quotation was issued.",
    "quotations.created_at": "Timestamp when the quotation line was created.",
    "targets.target_id": "Unique representative-period target identifier.",
    "targets.rep_name": "Representative to whom the quota applies.",
    "targets.territory": "Representative's fixed sales territory.",
    "targets.period_start": "Inclusive start date of the quota period.",
    "targets.period_end": "Exclusive end date of the quota period.",
    "targets.quota_amount": "USD quota assigned for the representative period.",
    "targets.currency_code": "Fixed target currency code; always USD.",
    "targets.created_at": "Timestamp when the target record was created.",
}


DOMAIN_VALUE_LABELS = {
    "lead_sources": "Lead sources",
    "lead_statuses": "Lead statuses",
    "territories": "Sales territories",
    "deal_stages": "Deal stages",
    "product_categories": "Product categories",
    "quote_statuses": "Quotation statuses",
}


class SalesDataDictionaryGenerator:
    """Generate the Sales release dictionary from validated configuration."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "sales":
            raise ValueError("SalesDataDictionaryGenerator only supports sales")
        validate_sales_config()
        self.generator = generator
        self.settings = generator.settings
        self.sales_config = load_sales_config()
        self._validate_description_coverage()

    @classmethod
    def for_profile(cls, profile: str) -> "SalesDataDictionaryGenerator":
        """Create a Sales dictionary generator from validated profile config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_markdown(self) -> str:
        """Return deterministic Markdown for the complete Sales contract."""

        lines = [
            "# Sales Data Dictionary",
            "",
            f"Dataset version: `{self.settings.dataset_version}`",
            "",
            f"Schema source: `{self.sales_config['schema_source']}`",
            "",
            f"Fixed reference date: `{self.settings.reference_today.isoformat()}`",
            "",
            (
                "Sales is USD-only. Mixed-currency conversion and FX-rate analysis "
                "belong to the Finance domain."
            ),
            "",
            "## Tables",
            "",
        ]
        for table_name in self.settings.table_order:
            lines.extend(
                self._table_section(
                    table_name,
                    self.sales_config["tables"][table_name],
                )
            )
        lines.extend(self._domain_values_section())
        lines.extend(self._generation_rules_section())
        lines.extend(self._distribution_section())
        lines.extend(self._business_rules_section())
        lines.extend(self._derived_metrics_section())
        lines.extend(self._relationship_section())
        lines.extend(self._imperfection_section())
        lines.extend(self._date_semantics_section())
        return "\n".join(lines).rstrip() + "\n"

    def write_release_dictionary(self) -> Path:
        """Atomically write data_dictionary.md into an unsealed release."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Sales release data-dictionary generation requires the full "
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
            for field in self.sales_config["tables"][table_name]["fields"]
        }
        if set(FIELD_DESCRIPTIONS) != expected_fields:
            raise ValueError(
                "Sales field-description coverage differs from config: "
                f"missing={sorted(expected_fields - set(FIELD_DESCRIPTIONS))}, "
                f"extra={sorted(set(FIELD_DESCRIPTIONS) - expected_fields)}"
            )
        if set(TABLE_DESCRIPTIONS) != set(self.settings.table_order):
            raise ValueError(
                "Sales table-description coverage differs from table_order"
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
        for relationship in self.sales_config["relationships"]:
            if relationship.get("relationship_type") != "analytical":
                continue
            parent = (
                relationship["parent_table"],
                relationship["parent_field"],
            )
            child = (
                relationship["child_table"],
                relationship["child_field"],
            )
            if (table_name, field["name"]) in {parent, child}:
                return "`analytical`"
        return ""

    def _domain_values_section(self) -> list[str]:
        lines = ["## Allowed Domain Values", ""]
        for config_name, label in DOMAIN_VALUE_LABELS.items():
            values = ", ".join(
                f"`{value}`"
                for value in self.sales_config["domain_values"][config_name]
            )
            lines.append(f"- {label}: {values}.")
        lines.append("")
        return lines

    def _generation_rules_section(self) -> list[str]:
        return [
            "## Base Generation Rules",
            "",
            "These deterministic settings control clean Sales entity generation.",
            "",
            "```json",
            json.dumps(self.sales_config["generation_rules"], indent=2),
            "```",
            "",
        ]

    def _distribution_section(self) -> list[str]:
        metadata = build_distribution_metadata(
            self.sales_config["distributions"],
            self.settings.distributions,
            self.sales_config["distribution_targets"],
        )
        lines = [
            "## Distribution Configuration",
            "",
            (
                "Sales selects shared presets from `config/generation/base.json` "
                "and declares domain overrides in "
                "`config/generation/sales/generation.json`."
            ),
            "",
            "| Setting | Shared preset | Sales overrides | Effective parameters | Targets |",
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
        lines.append("")
        return lines

    def _business_rules_section(self) -> list[str]:
        return [
            "## Business Rules",
            "",
            "### Currency and Pricing",
            "",
            "- `deals`, `products`, `quotations`, and `targets` use USD only.",
            (
                "- `products.list_price` is the current catalog price and may be "
                "NULL through controlled imperfection injection."
            ),
            (
                "- `quotations.unit_price` is the captured price at quote time; it "
                "remains populated even when the product's current list price is NULL."
            ),
            "- FX conversion is intentionally outside the Sales domain.",
            "",
            "### Ownership and Conversion",
            "",
            "- Every deal references a lead whose status is `Converted`.",
            "- A deal copies `rep_name` from its source lead.",
            "- Leads without deals remain available for LEFT JOIN questions.",
            "",
            "### Quotation Semantics",
            "",
            "- Every quotation line references one existing deal and one product.",
            "- `created_at` is on or before `quoted_at`.",
            "- Base Deal/Product pairs are unique before duplicate injection.",
            "",
        ]

    def _derived_metrics_section(self) -> list[str]:
        attained_stage = self.sales_config["business_mappings"]["quota_attainment"][
            "attained_stage"
        ]
        return [
            "## Derived Metrics and Query Guidance",
            "",
            "| Metric | Definition |",
            "|---|---|",
            (
                "| Quoted line value | `quantity * unit_price * "
                "(1 - discount_pct / 100)`. |"
            ),
            (
                f"| Attained revenue | `SUM(deals.deal_amount)` where "
                f"`deals.stage = '{attained_stage}'`, representative names match, "
                "and `close_date >= period_start AND close_date < period_end`. |"
            ),
            (
                "| Quota attainment percentage | "
                "`100 * attained_revenue / targets.quota_amount`. |"
            ),
            (
                "| Lead conversion count | Count distinct converted leads with a "
                "matching deal; retain unmatched leads when calculating rates. |"
            ),
            "",
            (
                "Near-duplicate quotation lines make aggregation intent important. "
                "Use `COUNT(*)` to count physical quote lines, "
                "`COUNT(DISTINCT product_id)` for distinct products, or deduplicate "
                "by (`deal_id`, `product_id`, `quote_number`) when the business "
                "question asks for logical quote lines."
            ),
            "",
        ]

    def _relationship_section(self) -> list[str]:
        lines = [
            "## Relationships",
            "",
            "| Type | Parent | Child | Cardinality | Join type | Additional condition |",
            "|---|---|---|---|---|---|",
        ]
        for relationship in self.sales_config["relationships"]:
            additional = relationship.get("additional_condition", "")
            lines.append(
                f"| `{relationship['relationship_type']}` | "
                f"`{relationship['parent_table']}.{relationship['parent_field']}` | "
                f"`{relationship['child_table']}.{relationship['child_field']}` | "
                f"`{relationship['cardinality']}` | "
                f"`{relationship['join_type']}` | {additional} |"
            )
        lines.extend(
            [
                "",
                (
                    "`quotations` is the explicit Deal-to-Product many-to-many "
                    "bridge: one deal may contain multiple products and one product "
                    "may appear in multiple deals."
                ),
                "",
                (
                    "The `targets` to `deals` relationship is analytical rather than "
                    "a physical foreign key. It joins by `rep_name` and the "
                    "start-inclusive, end-exclusive target period."
                ),
                "",
            ]
        )
        return lines

    def _imperfection_section(self) -> list[str]:
        imperfections = self.settings.imperfections
        target = self.sales_config["imperfection_targets"]["deal_amount_outliers"]
        boundaries = ", ".join(
            f"`{value}T00:00:00`" for value in imperfections["boundary_dates"]
        )
        return [
            "## Controlled Imperfections",
            "",
            (
                "- Near-duplicate quotation lines: "
                f"`{imperfections['duplicate_pct']}%` appended rows, identified by "
                "(`deal_id`, `product_id`, `quote_number`) with permitted quantity, "
                "discount, or quotation-time variation."
            ),
            ("- Missing `products.list_price`: " f"`{imperfections['null_pct']}%`."),
            (
                "- `deals.deal_amount` outliers: "
                f"`{imperfections['outlier_pct']}%` from "
                f"`{target['minimum_value']}` through `{target['maximum_value']}` "
                f"at scale `{target['scale']}`."
            ),
            f"- `products.created_at` boundary timestamps: {boundaries}.",
            "",
            (
                "Business-semantic NULLs are separate from injected NULLs. Open "
                "deals have no actual `close_date`; other nullable descriptive "
                "fields are optional. Only `products.list_price` is a controlled "
                "NULL-rate target."
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
                "Configured boundary timestamps are deliberate test values and may "
                "fall far outside ordinary operating dates. Target periods are "
                "start-inclusive and end-exclusive."
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
    if field_key == "quotations.quotation_id":
        behavior.append("Appended near-duplicate rows receive fresh identifiers.")
    if field_key in {
        "quotations.deal_id",
        "quotations.product_id",
        "quotations.quote_number",
    }:
        behavior.append("Repeated as the near-duplicate business key.")
    if field_key in {
        "quotations.quantity",
        "quotations.discount_pct",
        "quotations.quoted_at",
    }:
        behavior.append("Permitted near-duplicate variation field.")
    target = field.get("imperfection_target")
    if target == "controlled_null":
        behavior.append("Controlled injected NULL target.")
    elif target == "fixed_scale_outlier":
        behavior.append("Controlled fixed-scale high-value outlier target.")
    elif target == "boundary_timestamp":
        behavior.append("Receives every configured boundary timestamp.")
    if field.get("null_semantics"):
        behavior.append(f"Business-state NULL: {field['null_semantics']}")
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
    "SalesDataDictionaryGenerator",
    "TABLE_DESCRIPTIONS",
]
