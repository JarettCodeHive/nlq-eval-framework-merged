"""CLI entry point for NLQ evaluation framework generation checks."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from generators.core.progress import ProgressReporter
from generators.crm.pipeline import CRMDatasetPipeline
from generators.domain_registry import DATASET_DOMAINS
from generators.domain_registry import dataset_domain
from generators.finance.pipeline import FinanceDatasetPipeline
from generators.sales.pipeline import SalesDatasetPipeline
from qa_pairs.generator.author_qa_pairs import generate_seed_fixtures
from qa_pairs.generator.generate_crm import build as stage_qa_dataset
from qa_pairs.generator.rephrase import generate_rephrases
from qa_pairs.generator.scale_pairs import generate_pairs
from qa_pairs.generator.validate_crm import validate as validate_qa_dataset

from judge.build_input import build as build_judge_input
from judge.cli import build_argparser as build_judge_argparser
from judge.cli import run_calibrate_from_domain
from judge.cli import run_check_auth as run_check_auth_for_domain
from judge.cli import run_from_args as run_judge_from_args
from judge.resolve import qa_release_dir

CommandHandler = Callable[[argparse.Namespace], None]

REPO_ROOT = Path(__file__).resolve().parent


def run_validate_config(args: argparse.Namespace) -> None:
    """Validate config and schema alignment for a domain/profile."""

    runtime = dataset_domain(args.domain)
    runtime.validate_config()
    settings = runtime.settings_for_profile(args.profile)

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

    runtime = dataset_domain(args.domain)
    settings = runtime.settings_for_profile(args.profile)

    print("Generation metadata")
    for key, value in settings.metadata().items():
        print(f"{key}: {value}")


def run_build_dataset(args: argparse.Namespace) -> None:
    """Build and validate one complete persisted dataset profile."""

    runtime = dataset_domain(args.domain)
    pipelines = {
        "crm": CRMDatasetPipeline,
        "finance": FinanceDatasetPipeline,
        "sales": SalesDatasetPipeline,
    }
    try:
        pipeline = pipelines[args.domain]
    except KeyError as exc:
        raise NotImplementedError(
            "build-dataset currently supports only crm, finance, and sales; "
            f"got {args.domain}"
        ) from exc

    progress = ProgressReporter()
    output_path = pipeline.for_profile(
        args.profile,
        progress=progress,
    ).run()

    print(f"{runtime.display_name} dataset build passed")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"output: {output_path}")


def run_generate_base(args: argparse.Namespace) -> None:
    """Generate clean domain base tables and print a validation summary."""

    runtime = dataset_domain(args.domain)
    progress = ProgressReporter()
    progress.report(f"Validating {runtime.display_name} configuration and schema")
    runtime.validate_config()
    generator = runtime.base_generator.for_profile(args.profile, progress=progress)
    tables = generator.generate_tables()

    print(f"Generated clean {runtime.display_name} base entities")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print("tables:")
    for table_name in generator.settings.table_order:
        table = tables[table_name]
        print(f"  {table_name}: rows={len(table)} columns={len(table.columns)}")

    runtime.print_relationship_summary(tables)

    if args.write_preview:
        _write_preview_tables(args.domain, args.profile, tables, stage="base")


def run_apply_distributions(args: argparse.Namespace) -> None:
    """Generate domain base tables, apply distributions, and print checks."""

    runtime = dataset_domain(args.domain)
    progress = ProgressReporter()
    progress.report(f"Validating {runtime.display_name} configuration and schema")
    runtime.validate_config()
    applier = runtime.distribution_applier.for_profile(
        args.profile,
        progress=progress,
    )
    tables = applier.generate_distributed_tables()

    print(f"Applied {runtime.display_name} statistical distributions")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print("tables:")
    for table_name in applier.settings.table_order:
        table = tables[table_name]
        print(f"  {table_name}: rows={len(table)} columns={len(table.columns)}")

    runtime.print_distribution_summary(tables)
    runtime.print_relationship_summary(tables)

    if args.write_preview:
        _write_preview_tables(args.domain, args.profile, tables, stage="distributed")


def run_apply_imperfections(args: argparse.Namespace) -> None:
    """Generate distributed tables, inject imperfections, and print checks."""

    runtime = dataset_domain(args.domain)
    progress = ProgressReporter()
    progress.report(f"Validating {runtime.display_name} configuration and schema")
    runtime.validate_config()
    injector = runtime.imperfection_injector.for_profile(
        args.profile,
        progress=progress,
    )
    tables = injector.generate_imperfect_tables()

    print(f"Injected {runtime.display_name} controlled imperfections")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print("tables:")
    for table_name in injector.settings.table_order:
        table = tables[table_name]
        print(f"  {table_name}: rows={len(table)} columns={len(table.columns)}")

    runtime.print_imperfection_summary(tables, injector)
    runtime.print_relationship_summary(tables)

    if args.write_preview:
        _write_preview_tables(args.domain, args.profile, tables, stage="imperfect")


def run_validate_relations(args: argparse.Namespace) -> None:
    """Validate domain relational integrity after imperfections."""

    runtime = dataset_domain(args.domain)
    runtime.validate_config()
    validator = runtime.relational_validator.for_profile(args.profile)
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
    """Export release CSVs for a dataset domain."""

    runtime = dataset_domain(args.domain)
    progress = ProgressReporter()
    progress.report(f"Validating {runtime.display_name} configuration and schema")
    runtime.validate_config()
    exporter = runtime.csv_exporter.for_profile(args.profile, progress=progress)
    results = exporter.export_full_profile_csvs()

    print(f"Exported {runtime.display_name} release CSVs")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"output_dir: {exporter.output_dir}")
    for result in results:
        print(
            f"  {result.table_name}: path={result.path} "
            f"rows={result.rows} columns={result.columns}"
        )


def run_validate_row_caps(args: argparse.Namespace) -> None:
    """Validate domain row counts against the per-table hard cap."""

    runtime = dataset_domain(args.domain)
    runtime.validate_config()
    validator = runtime.row_cap_validator.for_profile(args.profile)
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
    """Validate domain CSV foreign-key integrity through DuckDB."""

    runtime = dataset_domain(args.domain)
    runtime.validate_config()
    validator = runtime.fk_validator.for_profile(args.profile)
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
    """Validate domain join paths required by the golden dataset plan."""

    runtime = dataset_domain(args.domain)
    runtime.validate_config()
    validator = runtime.join_path_validator.for_profile(args.profile)
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
    """Validate domain controlled imperfection rates."""

    runtime = dataset_domain(args.domain)
    runtime.validate_config()
    validator = runtime.imperfection_rate_validator.for_profile(args.profile)
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
    """Compute SHA-256 hashes for exported domain release CSVs."""

    runtime = dataset_domain(args.domain)
    runtime.validate_config()
    computer = runtime.hash_computer.for_profile(args.profile)
    hashes = computer.compute_exported_csv_hashes()

    print(f"Computed {runtime.display_name} release CSV SHA-256 hashes")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"output_dir: {computer.settings.output_path}")
    for file_hash in hashes:
        print(
            f"  {file_hash.path.name}: "
            f"sha256={file_hash.sha256} bytes={file_hash.bytes}"
        )


def run_generate_manifest(args: argparse.Namespace) -> None:
    """Generate a domain release manifest.json."""

    runtime = dataset_domain(args.domain)
    runtime.validate_config()
    generator = runtime.manifest_generator.for_profile(args.profile)
    manifest_path = generator.write_manifest()

    print(f"Generated {runtime.display_name} release manifest")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"path: {manifest_path}")


def run_generate_data_dictionary(args: argparse.Namespace) -> None:
    """Generate a domain release data dictionary."""

    runtime = dataset_domain(args.domain)
    runtime.validate_config()
    generator = runtime.data_dictionary_generator.for_profile(args.profile)
    output_path = generator.write_release_dictionary()

    print(f"Generated {runtime.display_name} release data dictionary")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"path: {output_path}")


def run_generate_schema_sql(args: argparse.Namespace) -> None:
    """Generate a domain release schema.sql."""

    runtime = dataset_domain(args.domain)
    runtime.validate_config()
    generator = runtime.schema_sql_generator.for_profile(args.profile)
    output_path = generator.write_release_schema()

    print(f"Generated {runtime.display_name} release schema SQL")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"path: {output_path}")


def run_validate_reproducibility(args: argparse.Namespace) -> None:
    """Validate domain release CSV reproducibility."""

    runtime = dataset_domain(args.domain)
    runtime.validate_config()
    validator = runtime.reproducibility_validator.for_profile(args.profile)
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


def run_qa_build(args: argparse.Namespace) -> None:
    """Stage, validate, and generate the production CRM Q&A pair set."""

    _ensure_crm(args.domain)
    progress = ProgressReporter()

    progress.report("Step 1/3: Stage the generated CRM dataset for Q&A authoring")
    stage_qa_dataset(args.profile)

    progress.report("Step 2/3: Validate the staged CRM dataset")
    validate_qa_dataset(args.profile)

    progress.report("Step 3/3: Generate and verify the CRM Q&A pair set")
    generate_pairs(args.profile)

    print("CRM Q&A pair build passed")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")


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


def _write_preview_tables(
    domain: str,
    profile: str,
    tables: dict[str, Any],
    stage: str,
) -> None:
    if profile == "full":
        raise ValueError("--write-preview is only allowed for non-release profiles")

    settings = dataset_domain(domain).settings_for_profile(profile)
    output_dir = settings.output_path / stage
    output_dir.mkdir(parents=True, exist_ok=True)
    for table_name, table in tables.items():
        output_path = output_dir / f"{table_name}.csv"
        table.to_csv(output_path, index=False, lineterminator="\n")
        print(f"preview_written: {output_path}")


def _ensure_crm(domain: str) -> None:
    if domain != "crm":
        raise NotImplementedError(
            f"Q&A commands currently support only crm; got {domain}"
        )


def run_judge_build_input(args: argparse.Namespace) -> None:
    """Join the Q&A release contract + companion into one judge input CSV.

    The §9.3 contract file carries no identifiers and the companion carries no
    answers, so the judge needs them joined. Reads whichever Q&A release the
    selected profile points at, so it stays in step with `qa-generate-pairs`.

    `judge` performs the same join on demand — this command exists to do it
    deliberately, and to rebuild the file after the pairs change.
    """

    _ensure_crm(args.domain)
    qa_release = qa_release_dir(args.domain, args.profile)
    output = qa_release / f"{args.domain}_judge_input.csv"
    build_judge_input(qa_release, args.domain, output)


def run_dataset_upload(args: argparse.Namespace) -> None:
    """Upload a generated dataset to the Claris Studio platform.

    Same two arguments as `build-dataset`: the CSVs come from whatever
    --domain/--profile already resolves to, so what lands on the platform is by
    construction the dataset the Q&A pairs were authored against.

    Every batch is waited for and every table's row count is checked against
    the CSV, because a bulk load is asynchronous and a job can report success
    while having dropped rows. The entity ids are recorded so a later delete —
    or a cleanup after a crash — does not have to rediscover them.
    """

    from studio.client import StudioClient
    from studio.config import load_studio_settings
    from studio.upload import describe_plan, summarise, upload_domain, write_manifest

    settings = load_studio_settings()
    print(f"platform: {settings.redacted}")
    progress = ProgressReporter()

    with StudioClient(settings, dry_run=args.dry_run) as client:
        outcome = upload_domain(
            args.domain,
            args.profile,
            client=client,
            replace=args.replace,
            progress=progress,
        )
        if args.dry_run:
            print(describe_plan(client))
            print("\n(dry run — nothing was sent)")
            return

    print()
    print(summarise(outcome, action="Uploaded"))
    manifest = write_manifest(
        outcome,
        REPO_ROOT / "release" / args.domain / "platform" / f"{args.profile}.json",
    )
    print(f"entity ids recorded: {manifest}")

    # Exit non-zero when the rows are not provably there: a pipeline step that
    # evaluates against a short table produces a number worse than no number.
    unverified = [t.table for t in outcome.tables if t.rows and not t.loads_confirmed]
    if unverified:
        raise SystemExit(
            f"upload finished but these tables are not verified: "
            f"{', '.join(unverified)}. Do not evaluate against them."
        )


def run_dataset_delete(args: argparse.Namespace) -> None:
    """Delete this domain/profile's tables from the Claris Studio platform.

    Destructive and outward-facing — it removes real tables from a shared org
    that other people query. So it prints the exact targets first and refuses
    without --yes: the confirmation should be informed, not ritual.

    Targets are resolved from the local CSVs, never from a listing of the org,
    so this can only ever remove tables this pipeline is responsible for.
    """

    from studio.client import StudioClient
    from studio.config import load_studio_settings
    from studio.upload import (
        _resolve_csv_dir,
        delete_domain,
        describe_plan,
        summarise,
        table_order,
    )

    settings = load_studio_settings()
    targets = table_order(args.domain, _resolve_csv_dir(args.domain, args.profile))

    print(f"platform: {settings.redacted}")
    print(f"about to DELETE {len(targets)} table(s) from org {settings.org_id}:")
    for table in targets:
        print(f"  {table}")

    if not (args.yes or args.dry_run):
        raise SystemExit(
            "refusing to delete without --yes. Re-run with --yes once the list "
            "above is what you meant."
        )

    progress = ProgressReporter()
    with StudioClient(settings, dry_run=args.dry_run) as client:
        outcome = delete_domain(
            args.domain, args.profile, client=client, progress=progress
        )
        if args.dry_run:
            print(describe_plan(client))
            print("\n(dry run — nothing was sent)")
            return

    print()
    print(summarise(outcome, action="Deleted"))


def run_judge(args: argparse.Namespace) -> None:
    """LLM-as-Judge scoring pass + regression scorecard — see judge/README.md
    for the full flag surface.

    Takes the same two arguments as `build-dataset` and `qa-build`: everything
    --domain and --profile already determine (the pair CSV, the dataset version
    tag, the `--pulse sql` table directory) is resolved in `judge/resolve.py`
    rather than retyped as a path.

    All other flags after the `judge` subcommand are consumed by the judge
    subparser; the top-level --domain and --profile win over any copy in the
    leftover argv so a single invocation is unambiguous.
    """

    judge_parser = build_judge_argparser(prog="main.py judge")
    judge_args = judge_parser.parse_args(getattr(args, "command_argv", []))
    # Top-level values are authoritative — they are what the rest of the
    # pipeline was invoked with.
    judge_args.domain = args.domain
    judge_args.profile = args.profile
    exit_code = run_judge_from_args(judge_args)
    if exit_code != 0:
        raise SystemExit(exit_code)


def run_score(args: argparse.Namespace) -> None:
    """Combine per-domain `judge` runs into one regression scorecard (§14.1).

    Separate from `judge` on purpose. `establish_baseline` is keyed on
    `platform_version` and is write-once, so if each domain's run established
    its own baseline the first one to finish would lock out the rest. A `judge`
    run therefore stays PREVIEW, and this is the only step that may establish or
    compare a baseline — which §14.1 requires to span all five domains anyway.

    Owns its own flag surface; everything after `score` goes to its parser.
    """

    from scorecard.combine import build_argparser, run_from_args

    parser = build_argparser(prog="main.py score")
    exit_code = run_from_args(parser.parse_args(getattr(args, "command_argv", [])))
    if exit_code != 0:
        raise SystemExit(exit_code)


def run_check_auth(args: argparse.Namespace) -> None:
    """Preflight both credentials — judge provider and platform — in seconds.

    Worth running first on a new machine: a scoring run authenticates twice, and
    without this the first thing that proves either works is a real run.
    """

    exit_code = run_check_auth_for_domain(args.domain)
    if exit_code != 0:
        raise SystemExit(exit_code)


def run_calibrate(args: argparse.Namespace) -> None:
    """§10.3 calibration protocol — score all anchors for --domain with the
    live LLM judge and write the .calibration marker if agreement passes.
    """

    exit_code = run_calibrate_from_domain(args.domain)
    if exit_code != 0:
        raise SystemExit(exit_code)


def run_rubric(args: argparse.Namespace) -> None:
    """Render the LLM-as-Judge rubric PDF deliverable (§14.1)."""

    from judge.rubric import main as rubric_main

    exit_code = rubric_main(getattr(args, "command_argv", []))
    if exit_code != 0:
        raise SystemExit(exit_code)


COMMANDS: dict[str, CommandHandler] = {
    "validate-config": run_validate_config,
    "show-config": run_show_config,
    "build-dataset": run_build_dataset,
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
    "qa-build": run_qa_build,
    "qa-generate-pairs": run_qa_generate_pairs,
    "qa-generate-rephrases": run_qa_generate_rephrases,
    "qa-stage-dataset": run_qa_stage_dataset,
    "qa-validate-dataset": run_qa_validate_dataset,
    "validate-fk": run_validate_fk,
    "validate-imperfection-rates": run_validate_imperfection_rates,
    "validate-join-paths": run_validate_join_paths,
    "check-auth": run_check_auth,
    "dataset-upload": run_dataset_upload,
    "dataset-delete": run_dataset_delete,
    "judge-build-input": run_judge_build_input,
    "judge": run_judge,
    "score": run_score,
    "calibrate": run_calibrate,
    "rubric": run_rubric,
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
        help=(
            "Dataset domain to use. Supported dataset domains: "
            f"{', '.join(sorted(DATASET_DOMAINS))}. Defaults to crm."
        ),
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
        help="Write generated stage CSV previews for non-release profiles.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="dataset-upload: delete an existing table and re-upload it. Without "
        "this an existing table is left alone, so a re-run cannot double-load.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="dataset-delete: confirm deletion. Required — it removes real tables "
        "from a shared org.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="dataset-upload / dataset-delete: print the requests that would be "
        "sent and send none of them.",
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


# Commands that own their own flag surface and consume the leftover argv.
# Everything else stays strict: an unrecognised flag on a generation command is
# a typo, and silently ignoring it would hide a mis-run release step.
PASSTHROUGH_COMMANDS: frozenset[str] = frozenset({"judge", "score", "rubric"})

_HELP_FLAGS: frozenset[str] = frozenset({"-h", "--help"})


def _forward_help(argv: list[str]) -> tuple[list[str], bool]:
    """Route `main.py judge --help` to the judge's parser, not this one.

    argparse handles `-h` eagerly at the top level, so a passthrough command's
    own flags — ~20 of them for `judge` — would otherwise be undiscoverable
    from the entry point we tell people to use. Pull the help flag out of the
    top-level parse and hand it to the subparser instead.
    """

    command = next((arg for arg in argv if arg in COMMANDS), None)
    if command not in PASSTHROUGH_COMMANDS:
        return argv, False
    after = argv[argv.index(command) + 1 :]
    if not _HELP_FLAGS.intersection(after):
        return argv, False
    return [arg for arg in argv if arg not in _HELP_FLAGS], True


def main() -> None:
    argv, forward_help = _forward_help(sys.argv[1:])
    parser = build_parser()
    args, command_argv = parser.parse_known_args(argv)
    if command_argv and args.command not in PASSTHROUGH_COMMANDS:
        parser.error(
            f"unrecognized arguments for {args.command!r}: {' '.join(command_argv)}"
        )
    if forward_help:
        command_argv.append("--help")
    args.command_argv = command_argv
    try:
        COMMANDS[args.command](args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
