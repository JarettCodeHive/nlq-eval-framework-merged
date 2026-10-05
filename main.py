"""CLI entry point for NLQ evaluation framework generation checks."""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from generators.core.progress import PipelineReporter
from generators.core.progress import PipelineStage
from generators.core.progress import ProgressReporter
from generators.crm.pipeline import CRMDatasetPipeline
from generators.domain_registry import DATASET_DOMAINS
from generators.domain_registry import dataset_domain
from generators.finance.pipeline import FinanceDatasetPipeline
from generators.logistics.pipeline import LogisticsDatasetPipeline
from generators.project_management.pipeline import ProjectManagementDatasetPipeline
from generators.sales.pipeline import SalesDatasetPipeline
from qa_pairs.generator.crm.author_qa_pairs import generate_seed_fixtures
from qa_pairs.generator.crm.generate_crm import build as stage_crm_qa_dataset
from qa_pairs.generator.crm.rephrase import generate_rephrases
from qa_pairs.generator.crm.scale_pairs import generate_pairs as generate_crm_pairs
from qa_pairs.generator.crm.validate_crm import validate as validate_crm_qa_dataset
from qa_pairs.generator.finance.generate_finance import build as stage_finance_qa_dataset
from qa_pairs.generator.finance.scale_pairs_finance import (
    generate_pairs as generate_finance_pairs,
)
from qa_pairs.generator.finance.validate_finance import validate as validate_finance_qa_dataset
from qa_pairs.generator.logistics.generate_logistics import build as stage_logistics_qa_dataset
from qa_pairs.generator.logistics.scale_pairs_logistics import (
    generate_pairs as generate_logistics_pairs,
)
from qa_pairs.generator.logistics.validate_logistics import validate as validate_logistics_qa_dataset
from qa_pairs.generator.project_management.generate_project_management import (
    build as stage_project_management_qa_dataset,
)
from qa_pairs.generator.project_management.scale_pairs_project_management import (
    generate_pairs as generate_project_management_pairs,
)
from qa_pairs.generator.project_management.validate_project_management import (
    validate as validate_project_management_qa_dataset,
)
from qa_pairs.generator.sales.generate_sales import build as stage_sales_qa_dataset
from qa_pairs.generator.sales.scale_pairs_sales import generate_pairs as generate_sales_pairs
from qa_pairs.generator.sales.validate_sales import validate as validate_sales_qa_dataset
from qa_pairs.utils.release_bundle import default_release_version
from qa_pairs.utils.release_bundle import component_dir
from qa_pairs.utils.release_bundle import use_release_version

from judge.cli import build_argparser as build_judge_argparser
from judge.cli import run_calibrate_from_domain
from judge.cli import run_check_auth as run_check_auth_for_domain
from judge.cli import run_from_args as run_judge_from_args
from judge.resolve import qa_release_dir

CommandHandler = Callable[[argparse.Namespace], Any]

REPO_ROOT = Path(__file__).resolve().parent


def _platform_manifest_path(
    domain: str, profile: str, version: str | None = None
) -> Path:
    """Audit record shared by upload and post-run cleanup."""

    return component_dir(
        domain,
        "platform",
        version or default_release_version(domain),
        repo_root=REPO_ROOT,
    ) / f"{profile}.json"


# Commands that are not scoped to one domain/release (score aggregates
# several domains; rubric just renders a template), so they log to a stable
# local path instead of a release component directory.
_UNSCOPED_LOG_COMMANDS: frozenset[str] = frozenset({"score", "rubric"})


def _resolve_command_log_path(args: argparse.Namespace) -> Path:
    """Return the on-disk log file target for one CLI invocation.

    A `full`-profile command logs beside the release artefacts it produces,
    under release/<version>/<domain>/logs/, so the console record of a run
    lives with everything that run wrote. Commands without a resolvable
    release version (dev profile, or a domain whose release config is
    missing) fall back to a local tmp/ path so logging never blocks the
    command itself.
    """

    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    filename = f"{args.command}_{timestamp}.log"

    if args.command in _UNSCOPED_LOG_COMMANDS:
        return REPO_ROOT / "logs" / filename

    domain = getattr(args, "domain", None) or "shared"
    profile = getattr(args, "profile", None)

    if profile == "full":
        try:
            version = args.release_version or default_release_version(domain)
            return (
                component_dir(domain, "logs", version, repo_root=REPO_ROOT)
                / filename
            )
        except ValueError:
            pass  # unknown domain or missing release config - fall back below

    return REPO_ROOT / "tmp" / "generated" / domain / (profile or "shared") / "logs" / filename


class _TeeTextStream(io.TextIOBase):
    """Mirror every write to a live stream and an open log file."""

    def __init__(self, primary: Any, secondary: Any) -> None:
        self._primary = primary
        self._secondary = secondary

    @property
    def encoding(self) -> str:
        return "utf-8"

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return bool(getattr(self._primary, "isatty", lambda: False)())

    def write(self, text: str) -> int:
        self._primary.write(text)
        self._secondary.write(text)
        return len(text)

    def flush(self) -> None:
        self._primary.flush()
        self._secondary.flush()


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
        "logistics": LogisticsDatasetPipeline,
        "project_management": ProjectManagementDatasetPipeline,
    }
    try:
        pipeline = pipelines[args.domain]
    except KeyError as exc:
        raise NotImplementedError(
            "build-dataset currently supports only crm, finance, sales, logistics, and project_management; "
            f"got {args.domain}"
        ) from exc

    progress = ProgressReporter()
    with use_release_version(getattr(args, "release_version", None)):
        configured_pipeline = pipeline.for_profile(
            args.profile,
            progress=progress,
        )
        output_path = configured_pipeline.run()
        release_version = configured_pipeline.settings.release_version

    print(f"{runtime.display_name} dataset build passed")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")
    print(f"release_version: {release_version}")
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
    if args.profile == "full":
        print(
            "release_version: "
            f"{getattr(args, 'release_version', None) or default_release_version(args.domain)}"
        )
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


QA_STAGE: dict[str, CommandHandler] = {
    "crm": stage_crm_qa_dataset,
    "sales": stage_sales_qa_dataset,
    "finance": stage_finance_qa_dataset,
    "logistics": stage_logistics_qa_dataset,
    "project_management": stage_project_management_qa_dataset,
}
QA_VALIDATE: dict[str, CommandHandler] = {
    "crm": validate_crm_qa_dataset,
    "sales": validate_sales_qa_dataset,
    "finance": validate_finance_qa_dataset,
    "logistics": validate_logistics_qa_dataset,
    "project_management": validate_project_management_qa_dataset,
}
QA_GENERATE: dict[str, CommandHandler] = {
    "crm": generate_crm_pairs,
    "sales": generate_sales_pairs,
    "finance": generate_finance_pairs,
    "logistics": generate_logistics_pairs,
    "project_management": generate_project_management_pairs,
}


def _qa_domain_fn(mapping: dict[str, CommandHandler], domain: str) -> CommandHandler:
    try:
        return mapping[domain]
    except KeyError as exc:
        raise NotImplementedError(
            f"Q&A commands currently support {sorted(mapping)}; got {domain!r}"
        ) from exc


def run_qa_stage_dataset(args: argparse.Namespace) -> None:
    """Stage the generated dataset in DuckDB for Q&A authoring."""

    _qa_domain_fn(QA_STAGE, args.domain)(args.profile)


def run_qa_build(args: argparse.Namespace) -> None:
    """Stage, validate, and generate the complete production Q&A package."""

    stage = _qa_domain_fn(QA_STAGE, args.domain)
    validate = _qa_domain_fn(QA_VALIDATE, args.domain)
    generate = _qa_domain_fn(QA_GENERATE, args.domain)
    progress = ProgressReporter()
    total = 4 if args.domain == "crm" else 3

    with use_release_version(getattr(args, "release_version", None)):
        progress.report(
            f"Step 1/{total}: Stage the generated {args.domain} dataset for Q&A authoring"
        )
        stage(args.profile)

        progress.report(f"Step 2/{total}: Validate the staged {args.domain} dataset")
        validate(args.profile)

        progress.report(
            f"Step 3/{total}: Generate and verify the {args.domain} Q&A pair set"
        )
        generate(args.profile)
        if args.domain == "crm":
            progress.report(
                "Step 4/4: Generate and verify CRM rephrase-group variants"
            )
            generate_rephrases(args.profile)

    print(f"{args.domain} Q&A pair build passed")
    print(f"domain: {args.domain}")
    print(f"profile: {args.profile}")


def run_qa_validate_dataset(args: argparse.Namespace) -> None:
    """Validate the staged dataset and required Q&A join behavior."""

    _qa_domain_fn(QA_VALIDATE, args.domain)(args.profile)


def run_qa_author_fixtures(args: argparse.Namespace) -> None:
    """Generate the review-only CRM seed Q&A fixtures."""

    _ensure_crm(args.domain)
    generate_seed_fixtures(args.profile)


def run_qa_generate_pairs(args: argparse.Namespace) -> None:
    """Generate the complete verified Q&A pair set."""

    _qa_domain_fn(QA_GENERATE, args.domain)(args.profile)


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


def run_dataset_upload(args: argparse.Namespace) -> Any:
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

    def remember_outcome(outcome) -> None:
        cleanup_planned = getattr(args, "cleanup_after_upload", False)
        outcome.cleanup_status = "pending" if cleanup_planned else "retained"
        for table in getattr(outcome, "created_tables", ()):
            table.cleanup_status = "pending" if cleanup_planned else "retained"
        args.upload_outcome = outcome

    try:
        with StudioClient(settings, dry_run=args.dry_run) as client:
            outcome = upload_domain(
                args.domain,
                args.profile,
                client=client,
                replace=args.replace,
                progress=progress,
                on_outcome=remember_outcome,
            )
            # Keep compatibility with test doubles that return an outcome but
            # do not invoke the early callback themselves.
            remember_outcome(outcome)
            if args.dry_run:
                print(describe_plan(client))
                print("\n(dry run — nothing was sent)")
                return outcome
    except BaseException:
        partial = getattr(args, "upload_outcome", None)
        if partial is not None and not args.dry_run:
            remember_outcome(partial)
            write_manifest(
                partial,
                _platform_manifest_path(
                    args.domain,
                    args.profile,
                    getattr(args, "release_version", None),
                ),
            )
        raise

    print()
    print(summarise(outcome, action="Uploaded"))
    manifest = write_manifest(
        outcome,
        _platform_manifest_path(
            args.domain, args.profile, getattr(args, "release_version", None)
        ),
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
    return outcome


def run_uploaded_data_cleanup(args: argparse.Namespace) -> Any:
    """Delete only entity IDs created by this pipeline's upload invocation."""

    from datetime import datetime, timezone

    from studio.client import StudioClient
    from studio.config import load_studio_settings
    from studio.upload import (
        cleanup_uploaded_entities,
        summarise_cleanup,
        write_manifest,
    )

    outcome = getattr(args, "upload_outcome", None)
    if outcome is None:
        print("no platform entities were created by this run")
        return None

    settings = load_studio_settings()
    print(f"platform: {settings.redacted}")
    progress = ProgressReporter()
    try:
        with StudioClient(settings) as client:
            cleanup = cleanup_uploaded_entities(
                outcome, client=client, progress=progress
            )
    except BaseException as exc:
        outcome.cleanup_status = "failed"
        outcome.cleanup_completed_at = datetime.now(timezone.utc).isoformat()
        error = f"{type(exc).__name__}: {exc}"
        if error not in outcome.cleanup_errors:
            outcome.cleanup_errors.append(error)
        write_manifest(
            outcome,
            _platform_manifest_path(
                args.domain,
                args.profile,
                getattr(args, "release_version", None),
            ),
        )
        raise

    manifest = write_manifest(
        outcome,
        _platform_manifest_path(
            args.domain, args.profile, getattr(args, "release_version", None)
        ),
    )
    print(summarise_cleanup(cleanup))
    print(f"cleanup recorded: {manifest}")
    if cleanup.failures:
        failed = ", ".join(failure.table for failure in cleanup.failures)
        raise RuntimeError(
            f"platform cleanup failed for {len(cleanup.failures)} table(s): {failed}"
        )
    return cleanup


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
    with use_release_version(getattr(args, "release_version", None)):
        exit_code = run_judge_from_args(judge_args)
    if exit_code != 0:
        raise SystemExit(exit_code)


def run_pipeline(args: argparse.Namespace) -> None:
    """Build, evaluate, then remove only the platform entities this run created.

    This is the convenient end-to-end entry point.  The individual commands
    remain available with their existing behavior; only this wrapper confirms
    the delete and supplies the judge-specific smoke-test flags.
    """

    total = 6
    reporter = PipelineReporter()
    reporter.start(domain=args.domain, profile=args.profile, total_stages=total)
    release_version = getattr(args, "release_version", None)
    pipeline_args = argparse.Namespace(
        domain=args.domain,
        profile=args.profile,
        release_version=release_version,
    )
    delete_args = argparse.Namespace(
        domain=args.domain,
        profile=args.profile,
        yes=True,
        dry_run=False,
        release_version=release_version,
    )
    upload_args = argparse.Namespace(
        domain=args.domain,
        profile=args.profile,
        replace=False,
        dry_run=False,
        release_version=release_version,
        cleanup_after_upload=not getattr(args, "keep_platform_data", False),
    )
    judge_args = argparse.Namespace(
        domain=args.domain,
        profile=args.profile,
        command_argv=["--allow-uncalibrated", "--limit", "3"],
        release_version=release_version,
    )
    stages = (
        (
            PipelineStage(1, total, "dataset", "Build and validate dataset"),
            run_build_dataset,
            pipeline_args,
        ),
        (
            PipelineStage(2, total, "qa", "Build and verify Q&A pairs"),
            run_qa_build,
            pipeline_args,
        ),
        (
            PipelineStage(
                3, total, "platform.delete", "Delete existing platform dataset"
            ),
            run_dataset_delete,
            delete_args,
        ),
        (
            PipelineStage(
                4, total, "platform.upload", "Upload and verify platform dataset"
            ),
            run_dataset_upload,
            upload_args,
        ),
        (
            PipelineStage(5, total, "judge", "Run three-question judge preview"),
            run_judge,
            judge_args,
        ),
    )

    primary_error: BaseException | None = None
    primary_traceback = None
    cleanup_error: BaseException | None = None
    upload_outcome = None

    with use_release_version(release_version):
        try:
            for stage, handler, stage_args in stages:
                with reporter.stage(stage):
                    result = handler(stage_args)
                if stage.number == 4:
                    upload_outcome = result or getattr(
                        upload_args, "upload_outcome", None
                    )
        except BaseException as exc:
            primary_error = exc
            primary_traceback = exc.__traceback__
            upload_outcome = getattr(upload_args, "upload_outcome", upload_outcome)

        if upload_outcome is not None:
            cleanup_stage = PipelineStage(
                6, total, "platform.cleanup", "Delete uploaded platform data"
            )
            try:
                with reporter.stage(cleanup_stage):
                    if getattr(args, "keep_platform_data", False):
                        print(
                            "cleanup skipped: --keep-platform-data retained "
                            "this run's uploaded entities"
                        )
                    else:
                        run_uploaded_data_cleanup(
                            argparse.Namespace(
                                domain=args.domain,
                                profile=args.profile,
                                release_version=release_version,
                                upload_outcome=upload_outcome,
                            )
                        )
            except BaseException as exc:
                cleanup_error = exc

    if primary_error is not None:
        if cleanup_error is not None:
            primary_error.add_note(
                "Platform cleanup also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        reporter.finish(domain=args.domain, profile=args.profile, succeeded=False)
        raise primary_error.with_traceback(primary_traceback)

    if cleanup_error is not None:
        reporter.finish(domain=args.domain, profile=args.profile, succeeded=False)
        raise cleanup_error

    reporter.finish(domain=args.domain, profile=args.profile, succeeded=True)


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


def run_anchors_export(args: argparse.Namespace) -> None:
    """Propose candidate anchors from a scored run as a CSV the graders fill in.

    §10.2 needs human-graded anchors and nothing in the pipeline produced any —
    which is why calibration, and therefore every release scorecard, has been
    blocked. Sheet carries the evidence and none of the judge's own scores: a
    grader shown the judge's 4 hands back a 4, and the measurement is agreement
    between two independent opinions.
    """

    from judge.anchors_io import export_candidates

    export_candidates(
        args.domain,
        run_id=args.from_run or None,
        profile=args.profile,
        count=args.count or None,
        output=Path(args.sheet) if args.sheet else None,
    )


def run_anchors_import(args: argparse.Namespace) -> None:
    """Ingest a filled grading sheet into judge/anchors/<domain>.json.

    Grades must be RECONCILED between both graders before this runs — the
    calibration module has no notion of per-grader votes. The set is checked for
    §10.2 strength before it is written, so a set a constant-scoring judge would
    pass is refused here rather than after a provider budget has been spent.
    """

    from judge.anchors_io import import_grades

    import_grades(
        args.domain,
        Path(args.sheet),
        output=Path(args.anchors_out) if args.anchors_out else None,
        force=args.force,
    )


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
    "run-pipeline": run_pipeline,
    "score": run_score,
    "calibrate": run_calibrate,
    "anchors-export": run_anchors_export,
    "anchors-import": run_anchors_import,
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
    # NOT --run: `score --run DOMAIN=RUN_ID` is a passthrough flag, and a
    # top-level flag of the same name is consumed by this parser first, which
    # silently dropped the pin and combined the most recent runs instead.
    parser.add_argument(
        "--from-run",
        default="",
        help="anchors-export: run id to propose anchors from. Defaults to the "
        "most recent scored run for the domain.",
    )
    parser.add_argument(
        "--sheet",
        default="",
        help="anchors-export: where to write the grading sheet (defaults to the "
        "run directory). anchors-import: the filled sheet to ingest — required.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="anchors-export: how many candidates to propose. Defaults to two "
        "above the domain's min_anchors, so a rejected anchor leaves headroom.",
    )
    # NOT --out, for the same reason: `score --out DIR` is a passthrough flag.
    parser.add_argument(
        "--anchors-out",
        default="",
        help="anchors-import: write the anchor set here instead of the "
        "calibration.anchors_path configured for the domain.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="anchors-import: write an anchor set that fails the §10.2 strength "
        "test anyway, as a work in progress. Calibration will still refuse it.",
    )
    parser.add_argument(
        "--version",
        dest="release_version",
        default=None,
        help=(
            "Unified release-bundle directory name. Full outputs are written "
            "under release/<version>/<domain>/. Defaults to the domain config."
        ),
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
    parser.add_argument(
        "--keep-platform-data",
        action="store_true",
        help=(
            "run-pipeline: retain entities uploaded by this run for debugging. "
            "By default they are deleted after judging, including on failure."
        ),
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

    if forward_help:
        # A --help dispatch produces no artefacts, so it earns no log file.
        with use_release_version(args.release_version):
            COMMANDS[args.command](args)
        return

    log_path = _resolve_command_log_path(args)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        tee_out = _TeeTextStream(sys.stdout, log_file)
        tee_err = _TeeTextStream(sys.stderr, log_file)
        with contextlib.redirect_stdout(tee_out), contextlib.redirect_stderr(tee_err):
            print(f"[log] writing command output to {log_path}")
            try:
                with use_release_version(args.release_version):
                    COMMANDS[args.command](args)
            except Exception as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
