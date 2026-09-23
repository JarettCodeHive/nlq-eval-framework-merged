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
from pathlib import Path

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
    skipped: str = ""
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

    @property
    def total_rows(self) -> int:
        return sum(t.rows for t in self.tables)


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
) -> UploadOutcome:
    """Create each table, bulk-load its rows, attach its schema context."""

    csv_dir = _resolve_csv_dir(domain, profile)
    outcome = UploadOutcome(domain=domain, profile=profile, csv_dir=csv_dir)
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

        outcome.tables.append(result)

    return outcome


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


def write_manifest(outcome: UploadOutcome, path: Path) -> Path:
    """Record the entity ids, so a later delete does not have to guess them."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "domain": outcome.domain,
                "profile": outcome.profile,
                "csv_dir": str(outcome.csv_dir),
                "tables": [
                    {
                        "table": t.table,
                        "entity_id": t.entity_id,
                        "rows": t.rows,
                        "batches": t.batches,
                        "job_ids": t.job_ids,
                        "rows_on_platform": t.rows_on_platform,
                        "loads_confirmed": t.loads_confirmed,
                        "skipped": t.skipped,
                    }
                    for t in outcome.tables
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
