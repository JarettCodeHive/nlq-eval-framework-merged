"""CLI entry point for NLQ evaluation framework generation checks."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from generators.core.progress import ProgressReporter

from generators.crm.config import settings_for_profile
from generators.crm.validators.config import validate_crm_config
from generators.crm.data_dictionary import CRMDataDictionaryGenerator
from generators.crm.distributions import CRMDistributionApplier
from generators.crm.export import CRMCSVExporter
from generators.crm.generator import CRMBaseEntityGenerator
from generators.crm.hashes import CRMHashComputer
from generators.crm.imperfections import CRMImperfectionInjector
from generators.crm.manifest import CRMManifestGenerator
from generators.crm.schema_sql import CRMSchemaSQLGenerator
from generators.crm.validators.fk_integrity import CRMDuckDBFKValidator
from generators.crm.validators.imperfection_rates import CRMImperfectionRateValidator
from generators.crm.validators.join_paths import CRMJoinPathValidator
from generators.crm.validators.relational import CRMRelationalValidator
from generators.crm.validators.reproducibility import CRMReproducibilityValidator
from generators.crm.validators.row_caps import CRMRowCapValidator
from qa_pairs.generator.author_qa_pairs import generate_seed_fixtures
from qa_pairs.generator.generate_crm import build as stage_qa_dataset
from qa_pairs.generator.rephrase import generate_rephrases
from qa_pairs.generator.scale_pairs import generate_pairs
from qa_pairs.generator.validate_crm import validate as validate_qa_dataset

# from judge.cli import build_argparser as build_judge_argparser
# from judge.cli import run_from_args as run_judge_from_args


CommandHandler = Callable[[argparse.Namespace], None]


def run_validate_config(args: argparse.Namespace) -> None:
    """Validate config and schema alignment for a domain/profile."""

    _ensure_crm(args.domain)
    validate_crm_config()
    settings = settings_for_profile(args.profile)

    print("Config/schema validation passed")
    print(f"domain: {settings.domain}")
    print(f"profile: {settings.profile}")
    print(f"dataset_version: {settings.dataset_version}")
    print(f"schema_source: {settings.metadata()['schema_source']}")
    print(f"reference_today: {settings.reference_today.isoformat()}")
    print(f"seed: {settings.seed}")
    print("row_counts:")
    for table_name in settings.table_order:
        print(f"  {table_name}: {settings.row_counts[table_name]}")


def run_show_config(args: argparse.Namespace) -> None:
    """Print deterministic generation metadata for a domain/profile."""

    _ensure_crm(args.domain)
    settings = settings_for_profile(args.profile)

    print("Generation metadata")
    for key, value in settings.metadata().items():
        print(f"{key}: {value}")


def run_generate_base(args: argparse.Namespace) -> None:
    """Generate clean CRM base tables and print a validation summary."""

    _ensure_crm(args.domain)
    progress = ProgressReporter()
    progress.report("Validating CRM configuration and schema")
    validate_crm_config()
    generator = CRMBaseEntityGenerator.for_profile(args.profile, progress=progress)
    tables = generator.generate_tables()

    print("Generated clean CRM base entities")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print("tables:")
    for table_name in generator.settings.table_order:
        table = tables[table_name]
        print(f"  {table_name}: rows={len(table)} columns={len(table.columns)}")

    _print_relationship_summary(tables)

    if args.write_preview:
        _write_preview_tables(args.profile, tables, stage="base")


def run_apply_distributions(args: argparse.Namespace) -> None:
    """Generate CRM base tables, apply distributions, and print checks."""

    _ensure_crm(args.domain)
    progress = ProgressReporter()
    progress.report("Validating CRM configuration and schema")
    validate_crm_config()
    applier = CRMDistributionApplier.for_profile(args.profile, progress=progress)
    tables = applier.generate_distributed_tables()

    print("Applied CRM statistical distributions")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print("tables:")
    for table_name in applier.settings.table_order:
        table = tables[table_name]
        print(f"  {table_name}: rows={len(table)} columns={len(table.columns)}")

    _print_distribution_summary(tables)
    _print_relationship_summary(tables)

    if args.write_preview:
        _write_preview_tables(args.profile, tables, stage="distributed")


def run_apply_imperfections(args: argparse.Namespace) -> None:
    """Generate CRM distributed tables, inject imperfections, and print checks."""

    _ensure_crm(args.domain)
    progress = ProgressReporter()
    progress.report("Validating CRM configuration and schema")
    validate_crm_config()
    injector = CRMImperfectionInjector.for_profile(args.profile, progress=progress)
    tables = injector.generate_imperfect_tables()

    print("Injected CRM controlled imperfections")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print("tables:")
    for table_name in injector.settings.table_order:
        table = tables[table_name]
        print(f"  {table_name}: rows={len(table)} columns={len(table.columns)}")

    _print_imperfection_summary(tables, injector)
    _print_relationship_summary(tables)

    if args.write_preview:
        _write_preview_tables(args.profile, tables, stage="imperfect")


def run_validate_relations(args: argparse.Namespace) -> None:
    """Validate CRM relational integrity after imperfections."""

    _ensure_crm(args.domain)
    validate_crm_config()
    validator = CRMRelationalValidator.for_profile(args.profile)
    results = validator.generate_and_validate()
    failures = [result for result in results if not result.passed]

    if failures:
        print("Relational validation failed")
        for result in failures:
            print(f"  FAIL {result.check_name}: {result.message}")
        raise ValueError(f"{len(failures)} relational check(s) failed")

    print("Relational validation passed")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"checks_passed: {len(results)}")
    for result in results:
        print(f"  PASS {result.check_name}: {result.message}")


def run_export_csvs(args: argparse.Namespace) -> None:
    """Export full-profile CRM release CSVs."""

    _ensure_crm(args.domain)
    progress = ProgressReporter()
    progress.report("Validating CRM configuration and schema")
    validate_crm_config()
    exporter = CRMCSVExporter.for_profile(args.profile, progress=progress)
    results = exporter.export_full_profile_csvs()

    print("Exported CRM full-profile CSVs")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"output_dir: {exporter.output_dir}")
    for result in results:
        print(
            f"  {result.table_name}: path={result.path} "
            f"rows={result.rows} columns={result.columns}"
        )


def run_validate_row_caps(args: argparse.Namespace) -> None:
    """Validate CRM row counts against the per-table hard cap."""

    _ensure_crm(args.domain)
    validate_crm_config()
    validator = CRMRowCapValidator.for_profile(args.profile)
    if args.generated:
        results = validator.generate_and_validate()
        source = "generated"
    elif args.exported:
        results = validator.validate_exported_csvs()
        source = "exported CSVs"
    else:
        results = validator.validate_expected_counts()
        source = "configured expected"

    failures = [result for result in results if not result.passed]
    if failures:
        print("Row-cap validation failed")
        for result in failures:
            print(f"  FAIL {result.check_name}: {result.message}")
        raise ValueError(f"{len(failures)} row-cap check(s) failed")

    print("Row-cap validation passed")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"source: {source}")
    print(f"max_rows_per_table: {validator.settings.max_rows_per_table}")
    for result in results:
        print(f"  PASS {result.check_name}: {result.message}")


def run_validate_fk(args: argparse.Namespace) -> None:
    """Validate CRM CSV foreign-key integrity through DuckDB constraints."""

    _ensure_crm(args.domain)
    validate_crm_config()
    validator = CRMDuckDBFKValidator.for_profile(args.profile)
    if args.generated:
        results = validator.generate_and_validate()
        source = "generated temporary CSVs"
    else:
        results = validator.validate_exported_csvs()
        source = "exported CSVs"

    failures = [result for result in results if not result.passed]
    if failures:
        print("FK integrity validation failed")
        for result in failures:
            print(f"  FAIL {result.check_name}: {result.message}")
        raise ValueError(f"{len(failures)} FK integrity check(s) failed")

    print("FK integrity validation passed")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"source: {source}")
    print(f"checks_passed: {len(results)}")
    for result in results:
        print(f"  PASS {result.check_name}: {result.message}")


def run_validate_join_paths(args: argparse.Namespace) -> None:
    """Validate CRM join paths required by the golden dataset plan."""

    _ensure_crm(args.domain)
    validate_crm_config()
    validator = CRMJoinPathValidator.for_profile(args.profile)
    if args.generated:
        results = validator.generate_and_validate()
        source = "generated temporary CSVs"
    else:
        results = validator.validate_exported_csvs()
        source = "exported CSVs"

    failures = [result for result in results if not result.passed]
    if failures:
        print("Join-path validation failed")
        for result in failures:
            print(f"  FAIL {result.check_name}: {result.message}")
        raise ValueError(f"{len(failures)} join-path check(s) failed")

    print("Join-path validation passed")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"source: {source}")
    print(f"checks_passed: {len(results)}")
    for result in results:
        print(f"  PASS {result.check_name}: {result.message}")


def run_validate_imperfection_rates(args: argparse.Namespace) -> None:
    """Validate CRM controlled imperfection rates."""

    _ensure_crm(args.domain)
    validate_crm_config()
    validator = CRMImperfectionRateValidator.for_profile(args.profile)
    if args.generated:
        results = validator.generate_and_validate()
        source = "generated"
    else:
        results = validator.validate_exported_csvs()
        source = "exported CSVs"

    failures = [result for result in results if not result.passed]
    if failures:
        print("Imperfection-rate validation failed")
        for result in failures:
            print(f"  FAIL {result.check_name}: {result.message}")
        raise ValueError(f"{len(failures)} imperfection-rate check(s) failed")

    print("Imperfection-rate validation passed")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"source: {source}")
    print(f"checks_passed: {len(results)}")
    for result in results:
        print(f"  PASS {result.check_name}: {result.message}")


def run_compute_sha256(args: argparse.Namespace) -> None:
    """Compute SHA-256 hashes for exported CRM release CSVs."""

    _ensure_crm(args.domain)
    validate_crm_config()
    computer = CRMHashComputer.for_profile(args.profile)
    hashes = computer.compute_exported_csv_hashes()

    print("Computed CRM release CSV SHA-256 hashes")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"output_dir: {computer.settings.output_path}")
    for file_hash in hashes:
        print(
            f"  {file_hash.path.name}: "
            f"sha256={file_hash.sha256} bytes={file_hash.bytes}"
        )


def run_generate_manifest(args: argparse.Namespace) -> None:
    """Generate CRM release manifest.json."""

    _ensure_crm(args.domain)
    validate_crm_config()
    generator = CRMManifestGenerator.for_profile(args.profile)
    manifest_path = generator.write_manifest()

    print("Generated CRM release manifest")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"path: {manifest_path}")


def run_generate_data_dictionary(args: argparse.Namespace) -> None:
    """Generate CRM release data dictionary."""

    _ensure_crm(args.domain)
    validate_crm_config()
    generator = CRMDataDictionaryGenerator.for_profile(args.profile)
    output_path = generator.write_release_dictionary()

    print("Generated CRM release data dictionary")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"path: {output_path}")


def run_generate_schema_sql(args: argparse.Namespace) -> None:
    """Generate CRM release schema.sql."""

    _ensure_crm(args.domain)
    validate_crm_config()
    generator = CRMSchemaSQLGenerator.for_profile(args.profile)
    output_path = generator.write_release_schema()

    print("Generated CRM release schema SQL")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"path: {output_path}")


def run_validate_reproducibility(args: argparse.Namespace) -> None:
    """Validate CRM release CSV reproducibility."""

    _ensure_crm(args.domain)
    validate_crm_config()
    validator = CRMReproducibilityValidator.for_profile(args.profile)
    results = validator.validate_release()

    failures = [result for result in results if not result.passed]
    if failures:
        print("Reproducibility validation failed")
        for result in failures:
            print(f"  FAIL {result.check_name}: {result.message}")
        raise ValueError(f"{len(failures)} reproducibility check(s) failed")

    print("Reproducibility validation passed")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"checks_passed: {len(results)}")
    for result in results:
        print(f"  PASS {result.check_name}: {result.message}")


def run_qa_stage_dataset(args: argparse.Namespace) -> None:
    """Stage generated CRM CSVs in DuckDB for Q&A authoring."""

    _ensure_crm(args.domain)
    stage_qa_dataset(args.profile)


def run_qa_validate_dataset(args: argparse.Namespace) -> None:
    """Validate the staged CRM dataset and required Q&A join behavior."""

    _ensure_crm(args.domain)
    validate_qa_dataset(args.profile)


def run_qa_author_fixtures(args: argparse.Namespace) -> None:
    """Generate the review-only CRM seed Q&A fixtures."""

    _ensure_crm(args.domain)
    generate_seed_fixtures(args.profile)


def run_qa_generate_pairs(args: argparse.Namespace) -> None:
    """Generate the complete verified CRM Q&A pair set."""

    _ensure_crm(args.domain)
    generate_pairs(args.profile)


def run_qa_generate_rephrases(args: argparse.Namespace) -> None:
    """Generate verified rephrase groups for the CRM Q&A pair set."""

    _ensure_crm(args.domain)
    generate_rephrases(args.profile)


def _write_preview_tables(profile: str, tables: dict[str, Any], stage: str) -> None:
    if profile == "full":
        raise ValueError("--write-preview is only allowed for non-release profiles")

    output_dir = Path("tmp") / "generated" / "crm" / profile / stage
    output_dir.mkdir(parents=True, exist_ok=True)
    for table_name, table in tables.items():
        output_path = output_dir / f"{table_name}.csv"
        table.to_csv(output_path, index=False, lineterminator="\n")
        print(f"preview_written: {output_path}")


def _print_relationship_summary(tables: dict[str, Any]) -> None:
    accounts = tables["accounts"]
    contacts = tables["contacts"]
    campaigns = tables["campaigns"]
    contact_campaigns = tables["contact_campaigns"]
    interactions = tables["interactions"]
    support_cases = tables["support_cases"]

    account_ids = set(accounts["account_id"].tolist())
    contact_ids = set(contacts["contact_id"].tolist())
    campaign_ids = set(campaigns["campaign_id"].tolist())
    membership_pairs = set(
        zip(
            contact_campaigns["contact_id"],
            contact_campaigns["campaign_id"],
            strict=True,
        )
    )
    attributed_interactions = interactions[interactions["campaign_id"] != ""]
    contact_accounts = dict(
        zip(contacts["contact_id"], contacts["account_id"], strict=True)
    )
    contacts_with_multiple_campaigns = int(
        contact_campaigns.groupby("contact_id")["campaign_id"].nunique().ge(2).sum()
    )
    campaigns_with_multiple_contacts = int(
        contact_campaigns.groupby("campaign_id")["contact_id"].nunique().ge(2).sum()
    )
    interaction_accounts_consistent = all(
        row.account_id == "" or row.account_id == contact_accounts[row.contact_id]
        for row in interactions.itertuples(index=False)
    )
    case_contacts_consistent = all(
        row.contact_id == "" or contact_accounts[row.contact_id] == row.account_id
        for row in support_cases.itertuples(index=False)
    )

    print("relationship_checks:")
    print(
        "  contacts.account_id valid: "
        f"{_non_empty_values(contacts['account_id']).issubset(account_ids)}"
    )
    print(
        "  contact_campaigns.contact_id valid: "
        f"{set(contact_campaigns['contact_id']).issubset(contact_ids)}"
    )
    print(
        "  contact_campaigns.campaign_id valid: "
        f"{set(contact_campaigns['campaign_id']).issubset(campaign_ids)}"
    )
    print(f"  contacts_with_multiple_campaigns: {contacts_with_multiple_campaigns}")
    print(f"  campaigns_with_multiple_contacts: {campaigns_with_multiple_contacts}")
    print(
        "  interactions.contact_id valid: "
        f"{set(interactions['contact_id']).issubset(contact_ids)}"
    )
    print(
        "  interactions.campaign_id valid: "
        f"{_non_empty_values(interactions['campaign_id']).issubset(campaign_ids)}"
    )
    print(
        "  interactions.account_id valid: "
        f"{_non_empty_values(interactions['account_id']).issubset(account_ids)}"
    )
    print(
        f"  interaction contact/account consistent: {interaction_accounts_consistent}"
    )
    print(
        "  attributed interactions have membership: "
        f"{all((row.contact_id, row.campaign_id) in membership_pairs for row in attributed_interactions.itertuples(index=False))}"
    )
    print(
        "  support_cases.account_id valid: "
        f"{set(support_cases['account_id']).issubset(account_ids)}"
    )
    print(
        "  support_cases.contact_id valid: "
        f"{_non_empty_values(support_cases['contact_id']).issubset(contact_ids)}"
    )
    print(f"  support-case contact/account consistent: {case_contacts_consistent}")


def _print_distribution_summary(tables: dict[str, Any]) -> None:
    campaigns = tables["campaigns"]
    interactions = tables["interactions"]
    support_cases = tables["support_cases"]

    campaign_budgets = campaigns.loc[
        campaigns["budget_amount"].astype(str).ne(""), "budget_amount"
    ].astype(float)
    print("distribution_checks:")
    print(f"  campaign_budget_min: {campaign_budgets.min():.2f}")
    print(f"  campaign_budget_max: {campaign_budgets.max():.2f}")
    print(f"  campaign_budget_mean: {campaign_budgets.mean():.2f}")
    print(f"  interaction_contacts: {interactions['contact_id'].nunique()}")
    engagement_points = interactions["engagement_points"].astype(int)
    print(f"  engagement_points_min: {engagement_points.min()}")
    print(f"  engagement_points_max: {engagement_points.max()}")
    print(f"  engagement_points_mean: {engagement_points.mean():.2f}")
    print(
        "  attributed_interaction_campaigns: "
        f"{interactions.loc[interactions['campaign_id'].astype(str).ne(''), 'campaign_id'].nunique()}"
    )
    print("  organic_interactions: " f"{_empty_count(interactions['campaign_id'])}")
    print(f"  support_case_open_dates: {support_cases['opened_at'].nunique()}")
    unresolved_cases = support_cases[support_cases["resolved_at"].astype(str).eq("")]
    resolved_cases = support_cases[support_cases["resolved_at"].astype(str).ne("")]
    sla_met = int(
        resolved_cases["resolved_at"]
        .astype(str)
        .le(resolved_cases["sla_due_at"].astype(str))
        .sum()
    )
    print(f"  support_cases_total: {len(support_cases)}")
    print(f"  support_cases_unresolved: {len(unresolved_cases)}")
    print(f"  resolved_cases_within_sla: {sla_met}/{len(resolved_cases)}")


def _print_imperfection_summary(
    tables: dict[str, Any],
    injector: CRMImperfectionInjector,
) -> None:
    contacts = tables["contacts"]
    contact_campaigns = tables["contact_campaigns"]
    interactions = tables["interactions"]
    support_cases = tables["support_cases"]

    print("imperfection_checks:")
    duplicate_rows = len(contacts) - injector.generator.row_count("contacts")
    outlier_target = injector.crm_config["imperfection_targets"][
        "engagement_point_outliers"
    ]
    outlier_minimum = int(outlier_target["minimum_value"])
    expected_boundaries = {
        f"{value}T00:00:00" for value in injector.config["boundary_dates"]
    }

    print(f"  contact_near_duplicates: {duplicate_rows}")
    print(
        "  contact_campaigns.attribution_weight_empty: "
        f"{_empty_count(contact_campaigns['attribution_weight'])}"
    )
    print(
        f"  engagement_point_outliers_ge_{outlier_minimum}: "
        f"{int((interactions['engagement_points'].astype(int) >= outlier_minimum).sum())}"
    )
    print(
        "  support_case_boundary_openings: "
        f"{int(support_cases['opened_at'].astype(str).isin(expected_boundaries).sum())}"
    )


def _empty_count(values: Any) -> int:
    return int((values.astype(str) == "").sum())


def _non_empty_values(values: Any) -> set[Any]:
    return {value for value in values.tolist() if str(value) != ""}


def _ensure_crm(domain: str) -> None:
    if domain != "crm":
        raise NotImplementedError(
            f"Only crm is implemented for generation; got {domain}"
        )


# def run_score(args: argparse.Namespace) -> None:
#     """LLM-as-Judge scoring pass — see judge/README.md for the full flag surface.

#     All flags after the `score` subcommand are consumed by the judge subparser;
#     the top-level --domain wins over any --domain in the leftover argv so a
#     single invocation is unambiguous.
#     """

#     judge_parser = build_judge_argparser(prog="main.py score")
#     judge_args = judge_parser.parse_args(getattr(args, "command_argv", []))
#     judge_args.domain = args.domain  # top-level --domain is authoritative
#     exit_code = run_judge_from_args(judge_args)
#     if exit_code != 0:
#         raise SystemExit(exit_code)


COMMANDS: dict[str, CommandHandler] = {
    "validate-config": run_validate_config,
    "show-config": run_show_config,
    "generate-base": run_generate_base,
    "apply-distributions": run_apply_distributions,
    "apply-imperfections": run_apply_imperfections,
    "compute-sha256": run_compute_sha256,
    "export-csvs": run_export_csvs,
    "export-final": run_export_csvs,
    "generate-data-dictionary": run_generate_data_dictionary,
    "generate-manifest": run_generate_manifest,
    "generate-schema-sql": run_generate_schema_sql,
    "qa-author-fixtures": run_qa_author_fixtures,
    "qa-generate-pairs": run_qa_generate_pairs,
    "qa-generate-rephrases": run_qa_generate_rephrases,
    "qa-stage-dataset": run_qa_stage_dataset,
    "qa-validate-dataset": run_qa_validate_dataset,
    "validate-fk": run_validate_fk,
    "validate-imperfection-rates": run_validate_imperfection_rates,
    "validate-join-paths": run_validate_join_paths,
    # "score": run_score,
    "validate-relations": run_validate_relations,
    "validate-reproducibility": run_validate_reproducibility,
    "validate-row-caps": run_validate_row_caps,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NLQ evaluation framework command runner."
    )
    parser.add_argument(
        "command",
        choices=sorted(COMMANDS),
        help="Command to execute.",
    )
    parser.add_argument(
        "--domain",
        default="crm",
        help="Domain to use. Defaults to crm.",
    )
    parser.add_argument(
        "--profile",
        default="dev",
        choices=("dev", "full"),
        help="Generation profile to use. Defaults to dev.",
    )
    parser.add_argument(
        "--write-preview",
        action="store_true",
        help="Write generated base CSV previews for non-release profiles.",
    )
    validation_source = parser.add_mutually_exclusive_group()
    validation_source.add_argument(
        "--generated",
        action="store_true",
        help="Validate generated tables where the selected command supports it.",
    )
    validation_source.add_argument(
        "--exported",
        action="store_true",
        help="Validate exported CSVs where the selected command supports it.",
    )
    return parser


# def main() -> None:
#     parser = build_parser()
#     # Commands that need their own flag surface (e.g. `score`) consume the leftover
#     # argv; other commands see no leftover and behave exactly as before.
#     args, command_argv = parser.parse_known_args()
#     args.command_argv = command_argv
#     try:
#         COMMANDS[args.command](args)
#     except Exception as exc:
#         print(f"ERROR: {exc}", file=sys.stderr)
#         raise SystemExit(1) from exc
def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        COMMANDS[args.command](args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
