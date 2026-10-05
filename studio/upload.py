"""Upload or delete one domain/profile's dataset on the Studio platform.

Takes `--domain` and `--profile` like every other stage and resolves the CSVs
through `judge.resolve.dataset_csv_dir`, so the dataset that gets uploaded is
by construction the same one `score --pulse sql` replays offline and the same
one the Q&A pairs were authored against.

Tables are uploaded in the `table_order` from the domain's release config —
parents before children — so a partial upload leaves a referentially sensible
prefix rather than orphan rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from judge.resolve import ResolutionError, dataset_csv_dir, dataset_release_config
from studio import schema
from studio.client import StudioClient, _extract_id


def _job_id(payload: object) -> int | None:
    """The job id from a bulk-load reply, if it carries one."""

    return _extract_id(payload)


@dataclass
class TableOutcome:
    """What happened to one table."""

    table: str
    rows: int = 0
    columns: int = 0
    batches: int = 0
    entity_id: int | None = None
    existed: bool = False
    created_by_run: bool = False
    skipped: str = ""
    cleanup_status: str = "not_applicable"
    cleanup_error: str = ""
    job_ids: list[int] = field(default_factory=list)
    # What the platform says it holds, checked against what we sent.
    rows_on_platform: int | None = None
    # Did every batch report a job that finished cleanly? A weaker signal than
    # the row count: it describes batches, and cannot see a batch never sent.
    jobs_confirmed: bool = True
    # The verdict that matters. True only when the rows are known to be there —
    # by row count where the platform will give one, by job status otherwise.
    loads_confirmed: bool = True


@dataclass
class UploadOutcome:
    domain: str
    profile: str
    csv_dir: Path
    tables: list[TableOutcome] = field(default_factory=list)
    cleanup_status: str = "retained"
    cleanup_completed_at: str | None = None
    cleanup_errors: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(t.rows for t in self.tables)

    @property
    def created_tables(self) -> list[TableOutcome]:
        """Entities proven to have been created by this upload invocation."""

        return [
            table
            for table in self.tables
            if table.created_by_run and table.entity_id is not None
        ]


@dataclass(frozen=True)
class CleanupFailure:
    """One run-owned entity that could not be removed."""

    table: str
    entity_id: int
    error: str


@dataclass
class CleanupOutcome:
    """Result of deleting only the entities created by one upload run."""

    attempted: list[tuple[str, int]] = field(default_factory=list)
    deleted: list[tuple[str, int]] = field(default_factory=list)
    failures: list[CleanupFailure] = field(default_factory=list)


def table_order(domain: str, csv_dir: Path) -> list[str]:
    """Tables to upload, parents first.

    The order comes from the release config rather than a directory listing:
    alphabetical order would push `contact_campaigns` ahead of `contacts`.
    """

    configured = dataset_release_config(domain).get("table_order") or []
    present = {path.stem for path in csv_dir.glob("*.csv")}
    ordered = [name for name in configured if name in present]
    # Anything on disk the config does not name still gets uploaded, after the
    # ordered set — a new table should not be silently dropped.
    return ordered + sorted(present - set(ordered))


def _resolve_csv_dir(domain: str, profile: str) -> Path:
    csv_dir = dataset_csv_dir(domain, profile)
    if csv_dir is None or not csv_dir.is_dir():
        raise ResolutionError(
            f"no generated CSVs for domain={domain!r} profile={profile!r}"
            + (f" at {csv_dir}" if csv_dir else "")
            + f".\nBuild them first: python main.py build-dataset --domain {domain} "
            f"--profile {profile}"
        )
    return csv_dir


def upload_domain(
    domain: str,
    profile: str,
    *,
    client: StudioClient,
    replace: bool = False,
    progress=None,
    on_outcome: Callable[[UploadOutcome], None] | None = None,
) -> UploadOutcome:
    """Create each table, bulk-load its rows, attach its schema context."""

    csv_dir = _resolve_csv_dir(domain, profile)
    outcome = UploadOutcome(domain=domain, profile=profile, csv_dir=csv_dir)
    if on_outcome is not None:
        # Expose the mutable record before the first remote mutation. If a load
        # fails halfway through, the caller can still clean every entity whose
        # creation was confirmed.
        on_outcome(outcome)
    say = progress.report if progress else (lambda _message: None)

    for table in table_order(domain, csv_dir):
        path = csv_dir / f"{table}.csv"
        columns, rows = schema.read_csv(path)
        numeric = schema.numeric_columns(rows, columns)
        result = TableOutcome(table=table, rows=len(rows), columns=len(columns))

        existing = client.find_entity_id(table)
        if existing is not None:
            result.existed = True
            if not replace:
                result.skipped = (
                    f"entity {existing} already exists; pass --replace to delete "
                    "and re-upload it"
                )
                result.entity_id = existing
                say(f"{table}: {result.skipped}")
                outcome.tables.append(result)
                continue
            say(f"{table}: deleting existing entity {existing}")
            client.delete_entity(existing)

        say(
            f"{table}: creating entity ({len(columns)} columns, "
            f"{len(numeric)} numeric) and loading {len(rows):,} rows"
        )
        entity_id = client.create_entity(
            schema.entity_definition(table, columns, numeric)
        )
        result.entity_id = entity_id
        result.created_by_run = True
        result.cleanup_status = (
            "pending" if outcome.cleanup_status == "pending" else "retained"
        )
        outcome.tables.append(result)

        encoded = [schema.encode_row(row, columns, numeric) for row in rows]
        for index, batch in enumerate(
            schema.batched_within(
                encoded, max_rows=client.batch_rows, max_bytes=client.batch_bytes
            ),
            start=1,
        ):
            job = client.load_rows(entity_id, batch)
            result.batches += 1

            # A bulk load returns a job with status "waiting" — firing the next
            # batch without waiting would queue dozens of them and report
            # success having confirmed nothing. Worse, an evaluation started
            # afterwards could query a half-loaded table.
            job_id = _job_id(job)
            if job_id is None:
                result.jobs_confirmed = False
                say(f"{table}: batch {index} sent ({len(batch):,} rows, no job id)")
                continue

            result.job_ids.append(job_id)
            status = client.wait_for_job(entity_id, job_id)
            if status is None:
                result.jobs_confirmed = False
                say(
                    f"{table}: batch {index} sent ({len(batch):,} rows, job "
                    f"{job_id} — UNCONFIRMED, no status route)"
                )
            else:
                say(
                    f"{table}: batch {index} landed ({len(batch):,} rows, job "
                    f"{job_id} -> {status.get('Status')})"
                )

        # The definitive check. Job statuses describe batches; this describes the
        # table, and it is the only thing that catches a batch that never ran.
        counted = client.count_rows(entity_id)
        result.rows_on_platform = counted
        if counted is None:
            # No count available, so the job statuses are all we have to go on.
            result.loads_confirmed = result.jobs_confirmed
            say(
                f"{table}: platform would not report a row count; falling back to "
                f"job status ({'confirmed' if result.jobs_confirmed else 'UNCONFIRMED'})"
            )
        elif counted != result.rows:
            result.loads_confirmed = False
            say(
                f"{table}: ROW COUNT MISMATCH — sent {result.rows:,}, "
                f"platform holds {counted:,}"
            )
        else:
            # The count is the stronger evidence: it proves the rows are there
            # even if a job status was unreadable along the way.
            result.loads_confirmed = True
            say(f"{table}: verified {counted:,} rows on the platform")

        if client.find_schema_context(entity_id) is None:
            client.create_schema_context(entity_id, f"{table}.csv")

    return outcome


def cleanup_uploaded_entities(
    outcome: UploadOutcome,
    *,
    client: StudioClient,
    progress=None,
) -> CleanupOutcome:
    """Delete only entities confirmed as created by this upload invocation.

    Entity ids come directly from the upload record. No table-name lookup is
    permitted here: a same-named entity may have been replaced by somebody
    else after the upload and must not be guessed at during cleanup.
    """

    cleanup = CleanupOutcome()
    say = progress.report if progress else (lambda _message: None)
    outcome.cleanup_status = "in_progress"
    outcome.cleanup_errors = []

    for table in reversed(outcome.created_tables):
        assert table.entity_id is not None
        entity_id = table.entity_id
        cleanup.attempted.append((table.table, entity_id))
        say(f"{table.table}: deleting run-owned entity {entity_id}")
        try:
            client.delete_entity(entity_id)
        except Exception as exc:  # noqa: BLE001 - record every platform failure
            error = f"{type(exc).__name__}: {exc}"
            table.cleanup_status = "failed"
            table.cleanup_error = error
            cleanup.failures.append(
                CleanupFailure(table=table.table, entity_id=entity_id, error=error)
            )
            outcome.cleanup_errors.append(
                f"{table.table} (entity {entity_id}): {error}"
            )
            say(f"{table.table}: cleanup FAILED for entity {entity_id} — {error}")
        else:
            table.cleanup_status = "deleted"
            table.cleanup_error = ""
            cleanup.deleted.append((table.table, entity_id))

    outcome.cleanup_status = "failed" if cleanup.failures else "completed"
    outcome.cleanup_completed_at = datetime.now(timezone.utc).isoformat()
    return cleanup


def delete_domain(
    domain: str,
    profile: str,
    *,
    client: StudioClient,
    progress=None,
) -> UploadOutcome:
    """Delete every table of this domain/profile that exists on the platform.

    Resolved from the local CSVs, not from whatever the org happens to hold, so
    this can only ever remove tables this pipeline is responsible for.
    """

    csv_dir = _resolve_csv_dir(domain, profile)
    outcome = UploadOutcome(domain=domain, profile=profile, csv_dir=csv_dir)
    say = progress.report if progress else (lambda _message: None)

    # Children before parents — the reverse of upload order.
    for table in reversed(table_order(domain, csv_dir)):
        result = TableOutcome(table=table)
        entity_id = client.find_entity_id(table)
        if entity_id is None:
            result.skipped = "not present on the platform"
            say(f"{table}: {result.skipped}")
        else:
            result.entity_id = entity_id
            result.existed = True
            say(f"{table}: deleting entity {entity_id}")
            client.delete_entity(entity_id)
        outcome.tables.append(result)

    return outcome


def describe_plan(client: StudioClient) -> str:
    """Render the requests a --dry-run would have sent."""

    lines = [f"{len(client.calls)} request(s) planned:"]
    for call in client.calls:
        detail = f"  {call['method']:6} {call['path']}"
        if call["body_items"] is not None:
            detail += f"  [{call['body_items']:,} rows]"
        lines.append(f"{detail}   # {call['summary']}")
    return "\n".join(lines)


def summarise(outcome: UploadOutcome, *, action: str) -> str:
    """One readable block per run, for the console and for a log."""

    lines = [
        f"{action} {outcome.domain} / {outcome.profile}",
        f"source: {outcome.csv_dir}",
    ]
    for table in outcome.tables:
        if table.skipped:
            lines.append(f"  {table.table:24} SKIPPED — {table.skipped}")
        elif table.rows:
            if table.loads_confirmed:
                flag = f"  verified={table.rows_on_platform:,}"
            elif table.rows_on_platform is not None:
                flag = f"  [MISMATCH: platform holds {table.rows_on_platform:,}]"
            else:
                flag = "  [UNVERIFIED]"
            lines.append(
                f"  {table.table:24} entity={table.entity_id} "
                f"rows={table.rows:,} batches={table.batches}{flag}"
            )
        else:
            lines.append(f"  {table.table:24} entity={table.entity_id} deleted")
    if outcome.total_rows:
        lines.append(f"  {'TOTAL':24} rows={outcome.total_rows:,}")
    if any(t.rows and not t.loads_confirmed for t in outcome.tables):
        lines.append(
            "  WARNING: some loads could not be confirmed — the platform gave no "
            "job status. Do not evaluate against these tables until the row "
            "counts are checked by hand."
        )
    return "\n".join(lines)


def summarise_cleanup(cleanup: CleanupOutcome) -> str:
    """Render a concise cleanup result without implying name-based deletion."""

    lines = [
        f"Deleted {len(cleanup.deleted)}/{len(cleanup.attempted)} "
        "run-owned platform entities"
    ]
    for table, entity_id in cleanup.deleted:
        lines.append(f"  {table:24} entity={entity_id} deleted")
    for failure in cleanup.failures:
        lines.append(
            f"  {failure.table:24} entity={failure.entity_id} FAILED — "
            f"{failure.error}"
        )
    return "\n".join(lines)


def write_manifest(outcome: UploadOutcome, path: Path) -> Path:
    """Atomically record entity ownership and cleanup status for this run."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "domain": outcome.domain,
                "profile": outcome.profile,
                "csv_dir": str(outcome.csv_dir),
                "cleanup": {
                    "status": outcome.cleanup_status,
                    "completed_at": outcome.cleanup_completed_at,
                    "errors": outcome.cleanup_errors,
                },
                "tables": [
                    {
                        "table": t.table,
                        "entity_id": t.entity_id,
                        "created_by_run": t.created_by_run,
                        "rows": t.rows,
                        "batches": t.batches,
                        "job_ids": t.job_ids,
                        "rows_on_platform": t.rows_on_platform,
                        "loads_confirmed": t.loads_confirmed,
                        "skipped": t.skipped,
                        "cleanup_status": t.cleanup_status,
                        "cleanup_error": t.cleanup_error,
                    }
                    for t in outcome.tables
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
