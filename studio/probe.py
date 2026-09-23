"""Discovery probe for the two endpoints the pipeline still needs.

The self-test proved create/load/delete work, and turned up one thing that
changes the design: a bulk load returns `{"ID": 350, "Name": "Batch create 3
entities", "Status": "waiting"}`. **Loading is asynchronous.** So `upload_domain`
firing 53 batches back to back does not mean 53 batches have landed, and an
evaluation run started straight afterwards could be querying a half-loaded
table. The pipeline needs to poll — and nothing captured so far shows the
endpoint to poll.

This probe finds it by trying the plausible routes and reporting which answer.
It also looks for a way to read a row count back, so an upload can verify that
what it sent actually arrived.

Same safety model as the self-test: one throwaway table, deleted in `finally`.
Read-only probing otherwise — it never touches an existing table.

    python -m studio.probe
"""

from __future__ import annotations

import json
import time
from typing import Any

from studio import schema
from studio.client import StudioClient, _extract_id, _records, encode_query
from studio.config import MissingStudioCredentials, load_studio_settings
from studio.selftest import TEST_COLUMNS, TEST_ROWS, TEST_TABLE


def _try(client: StudioClient, method: str, path: str, **kwargs) -> tuple[bool, Any]:
    """Attempt one request, reporting rather than raising.

    A probe expects most of what it tries to 404; that is the signal, not a
    failure. Retries are pointless here, so they are bypassed.
    """

    try:
        response = client._client.request(
            method, path, json=None, params=kwargs.get("params"),
            headers=client._headers(),
        )
    except Exception as exc:  # noqa: BLE001 — network faults are a result too
        print(f"    {method:6} {path:52} {type(exc).__name__}")
        return False, None

    ok = 200 <= response.status_code < 300
    body = response.text[:200].replace("\n", " ")
    print(f"    {method:6} {path:52} http={response.status_code} {body!r}")
    if not ok:
        return False, None
    try:
        return True, response.json()
    except ValueError:
        return True, response.text


def list_all_entities(client: StudioClient) -> list[dict]:
    """Walk every page — the org holds more than one page of tables."""

    print("\n[1] every entity in the org (paginated)")
    found: list[dict] = []
    page = 1
    while True:
        payload = client._request(
            "GET",
            f"{client.org_path}/entity",
            params={
                "query": encode_query(
                    {"$limit": 100, "$skip": (page - 1) * 100, "$select": [{"Name": {}}]}
                )
            },
            summary=f"list entities page {page}",
        )
        records = _records(payload)
        found.extend(records)
        total = payload.get("Total") if isinstance(payload, dict) else None
        pages = payload.get("TotalPages") if isinstance(payload, dict) else 1
        if not records or page >= (pages or 1):
            break
        page += 1

    print(f"    {len(found)} entity/entities (Total reported: {total})")
    for record in sorted(found, key=lambda r: _extract_id(r) or 0):
        print(f"      ID={_extract_id(record):<5} {record.get('Name')}")
    return found


def probe_job_status(client: StudioClient, entity_id: int, job_id: int) -> None:
    """Find the route that reports whether a bulk load has finished."""

    print(f"\n[4] where does job {job_id} report its status?")
    candidates = [
        ("GET", f"{client.org_path}/job/{job_id}"),
        ("GET", f"{client.org_path}/jobs/{job_id}"),
        ("GET", f"{client.org_path}/entity/{entity_id}/job/{job_id}"),
        ("GET", f"{client.org_path}/entity/{entity_id}/job"),
    ]
    for method, path in candidates:
        ok, payload = _try(client, method, path)
        if ok:
            print(f"    -> FOUND: {method} {path}")
            print(f"       {json.dumps(payload, indent=2, default=str)[:600]}")
            return

    # The query DSL is how /entity and /context are filtered; try it on /job too.
    for collection in ("job", "jobs"):
        ok, payload = _try(
            client,
            "GET",
            f"{client.org_path}/{collection}",
            params={"query": encode_query({"$limit": 5, "$skip": 0})},
        )
        if ok:
            print(f"    -> FOUND a job collection: GET {client.org_path}/{collection}")
            print(f"       {json.dumps(payload, indent=2, default=str)[:600]}")
            return

    print("    -> NONE of these worked. Capture a Studio import and look for the")
    print("       request the UI polls after its own bulk load.")


def probe_row_read(client: StudioClient, entity_id: int) -> None:
    """Find a way to count the rows that actually landed."""

    print(f"\n[5] can we read rows back from entity {entity_id} to verify a load?")
    query = {"query": encode_query({"$limit": 5, "$skip": 0})}
    candidates = [
        ("GET", f"{client.org_path}/entity/{entity_id}/record", query),
        ("GET", f"{client.org_path}/entity/{entity_id}/records", query),
        ("GET", f"{client.org_path}/entity/{entity_id}/data", query),
        ("GET", f"{client.org_path}/entity/{entity_id}/row", query),
        ("GET", f"{client.org_path}/record/{entity_id}", query),
        ("GET", f"{client.org_path}/entity/{entity_id}", None),
    ]
    for method, path, params in candidates:
        ok, payload = _try(client, method, path, params=params)
        if ok and payload:
            text = json.dumps(payload, default=str)
            # A row read should echo our own field names back.
            if any(column in text for column in TEST_COLUMNS):
                print(f"    -> FOUND rows: {method} {path}")
                print(f"       {text[:600]}")
                return
    print("    -> no row-read route found among the obvious candidates.")


def run() -> int:
    try:
        settings = load_studio_settings()
    except MissingStudioCredentials as exc:
        print(f"FAIL: {exc}")
        return 2

    print("Studio discovery probe")
    print(f"  target: {settings.redacted}")

    entity_id: int | None = None
    client = StudioClient(settings)
    try:
        list_all_entities(client)

        print(f"\n[2] create {TEST_TABLE} to probe against")
        numeric = schema.numeric_columns(TEST_ROWS, TEST_COLUMNS)
        entity_id = client.create_entity(
            schema.entity_definition(TEST_TABLE, TEST_COLUMNS, numeric)
        )
        print(f"    entity id = {entity_id}")

        print("\n[3] load 3 rows and capture the job id")
        loaded = client.load_rows(
            entity_id,
            [schema.encode_row(r, TEST_COLUMNS, numeric) for r in TEST_ROWS],
        )
        print(f"    {json.dumps(loaded, default=str)}")
        job_id = _extract_id(loaded)
        status = loaded.get("Status") if isinstance(loaded, dict) else None
        print(f"    job id = {job_id}, status = {status!r}")

        if job_id is not None:
            probe_job_status(client, entity_id, job_id)
            # Give an async job a moment, then look again — if we found a route
            # above this shows the status actually transitions.
            time.sleep(3)
            print("\n[4b] the same probe 3s later (does the status move?)")
            probe_job_status(client, entity_id, job_id)

        probe_row_read(client, entity_id)
        return 0

    except Exception as exc:  # noqa: BLE001 — a probe reports everything
        print(f"\nFAIL: {type(exc).__name__}: {exc}")
        return 1
    finally:
        if entity_id is not None:
            print(f"\n[6] cleaning up entity {entity_id}")
            try:
                client.delete_entity(entity_id)
                print(f"    deleted; still there? {client.find_entity_id(TEST_TABLE)}")
            except Exception as exc:  # noqa: BLE001
                print(f"    CLEANUP FAILED: {exc} — remove entity {entity_id} by hand")
        client.close()


if __name__ == "__main__":
    raise SystemExit(run())
