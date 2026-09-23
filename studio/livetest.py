"""Real single-table round trip against the platform.

The self-test proved the endpoints work on a throwaway table. This does the real
thing on a real release table — create, load every batch, wait for each load
job, then delete — at the smallest scale that still exercises multi-batch
loading. `campaigns` is 5,000 rows, which is two batches.

It is deliberately one table. A full CRM upload is 601,480 rows over ~190
requests, and there is no point discovering a payload problem on request 150.

    python -m studio.livetest                      # campaigns, upload then delete
    python -m studio.livetest --table contacts     # a bigger one (48,480 rows)
    python -m studio.livetest --keep               # leave it up to query in Studio
    python -m studio.livetest --inventory          # just list the org, change nothing

`--keep` is what you want before running an evaluation against the table.
Without it the table is deleted again, so the org ends as it started.
"""

from __future__ import annotations

import argparse
import json
import time

from judge.resolve import dataset_csv_dir
from studio import schema
from studio.client import StudioClient, StudioError, _extract_id, _records, encode_query
from studio.config import MissingStudioCredentials, load_studio_settings


def inventory(client: StudioClient) -> list[dict]:
    """Every entity in the org, across pages.

    Worth printing before anything else: the org already held `campaigns` and
    `contact_campaigns` from an earlier attempt, and a table that exists changes
    what an upload does.
    """

    print("\n--- org inventory ---")
    found: list[dict] = []
    page = 0
    total = None
    while True:
        payload = client._request(
            "GET",
            f"{client.org_path}/entity",
            params={
                "query": encode_query(
                    {"$limit": 100, "$skip": page * 100, "$select": [{"Name": {}}]}
                )
            },
            summary=f"inventory page {page + 1}",
        )
        records = _records(payload)
        found.extend(records)
        if isinstance(payload, dict):
            total = payload.get("Total")
            pages = payload.get("TotalPages") or 1
        else:
            pages = 1
        page += 1
        if not records or page >= pages:
            break

    print(f"{len(found)} entities (platform reports Total={total})")
    for record in sorted(found, key=lambda r: _extract_id(r) or 0):
        print(f"  ID={_extract_id(record):<6} {record.get('Name')}")
    return found


def round_trip(
    client: StudioClient,
    *,
    domain: str,
    profile: str,
    table: str,
    keep: bool,
) -> int:
    csv_dir = dataset_csv_dir(domain, profile)
    if csv_dir is None or not (csv_dir / f"{table}.csv").is_file():
        print(f"FAIL: no {table}.csv under {csv_dir}")
        return 2

    path = csv_dir / f"{table}.csv"
    columns, rows = schema.read_csv(path)
    numeric = schema.numeric_columns(rows, columns)
    encoded = [schema.encode_row(r, columns, numeric) for r in rows]
    batches = list(
        schema.batched_within(
            encoded, max_rows=client.batch_rows, max_bytes=client.batch_bytes
        )
    )

    print(f"\n--- {table} ---")
    print(f"source   : {path}")
    print(f"rows     : {len(rows):,} in {len(batches)} batch(es)")
    print(f"columns  : {len(columns)} ({len(numeric)} numeric: {sorted(numeric)})")
    print(f"max body : {max(len(json.dumps(b)) for b in batches):,} bytes")

    entity_id: int | None = None
    try:
        existing = client.find_entity_id(table)
        if existing is not None:
            print(f"\nexisting entity {existing} found — deleting it first")
            client.delete_entity(existing)
            print(f"  deleted; still present? {client.find_entity_id(table)}")

        print("\ncreating entity")
        started = time.time()
        entity_id = client.create_entity(
            schema.entity_definition(table, columns, numeric)
        )
        print(f"  entity id = {entity_id}  ({time.time() - started:.1f}s)")

        confirmed = True
        for index, batch in enumerate(batches, start=1):
            started = time.time()
            job = client.load_rows(entity_id, batch)
            job_id = _extract_id(job)
            sent = time.time() - started

            if job_id is None:
                confirmed = False
                print(
                    f"  batch {index}/{len(batches)}: {len(batch):,} rows sent in "
                    f"{sent:.1f}s — NO JOB ID, cannot confirm"
                )
                continue

            status = client.wait_for_job(entity_id, job_id)
            waited = time.time() - started
            if status is None:
                confirmed = False
                print(
                    f"  batch {index}/{len(batches)}: {len(batch):,} rows, job "
                    f"{job_id} sent in {sent:.1f}s — UNCONFIRMED, no status route"
                )
            else:
                print(
                    f"  batch {index}/{len(batches)}: {len(batch):,} rows, job "
                    f"{job_id} -> {status.get('Status')} in {waited:.1f}s"
                )

        print("\nattaching schema context")
        client.create_schema_context(entity_id, f"{table}.csv")

        # The independent check: job statuses describe batches, this describes
        # the table, and only this can see a batch that never ran at all.
        counted = client.count_rows(entity_id)
        print(f"\nfind_entity_id({table!r}) -> {client.find_entity_id(table)}")
        print(f"row count on platform : {counted if counted is not None else 'unknown'}")
        print(f"row count in the CSV  : {len(rows):,}")

        if counted == len(rows):
            print(f"VERIFIED: all {len(rows):,} rows are on the platform")
            return 0
        if counted is None:
            print(
                "row count unavailable; falling back to job status "
                f"({'confirmed' if confirmed else 'UNCONFIRMED'})"
            )
            return 0 if confirmed else 1
        print(
            f"MISMATCH: sent {len(rows):,}, platform holds {counted:,} — "
            f"{len(rows) - counted:,} row(s) missing. Do not evaluate against this."
        )
        return 1

    except StudioError as exc:
        print(f"\nFAIL: {exc}")
        return 1
    finally:
        if entity_id is not None and not keep:
            print(f"\ndeleting entity {entity_id}")
            try:
                client.delete_entity(entity_id)
                print(f"  gone? {client.find_entity_id(table) is None}")
            except Exception as exc:  # noqa: BLE001
                print(f"  CLEANUP FAILED: {exc} — delete entity {entity_id} by hand")
        elif entity_id is not None:
            print(f"\n--keep: entity {entity_id} ({table}) is live in the org.")
            print("  Delete it with: python -m studio.livetest --delete-only "
                  f"--table {table}")


def delete_only(client: StudioClient, table: str) -> int:
    entity_id = client.find_entity_id(table)
    if entity_id is None:
        print(f"{table}: not present")
        return 0
    print(f"{table}: deleting entity {entity_id}")
    client.delete_entity(entity_id)
    print(f"  gone? {client.find_entity_id(table) is None}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", default="crm")
    parser.add_argument("--profile", default="full", choices=("dev", "full"))
    parser.add_argument(
        "--table",
        default="campaigns",
        help="which release table to round-trip (default: the smallest, campaigns)",
    )
    parser.add_argument(
        "--keep", action="store_true", help="leave the table live after uploading"
    )
    parser.add_argument(
        "--inventory", action="store_true", help="list the org and change nothing"
    )
    parser.add_argument(
        "--delete-only", action="store_true", help="just delete --table, upload nothing"
    )
    args = parser.parse_args()

    try:
        settings = load_studio_settings()
    except MissingStudioCredentials as exc:
        raise SystemExit(f"FAIL: {exc}")

    print("Studio live test")
    print(f"  target: {settings.redacted}")

    with StudioClient(settings) as client:
        inventory(client)
        if args.inventory:
            raise SystemExit(0)
        if args.delete_only:
            raise SystemExit(delete_only(client, args.table))
        raise SystemExit(
            round_trip(
                client,
                domain=args.domain,
                profile=args.profile,
                table=args.table,
                keep=args.keep,
            )
        )


if __name__ == "__main__":
    main()
