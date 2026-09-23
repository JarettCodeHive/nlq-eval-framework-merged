"""Live smoke test for the Studio client — the one thing unit tests cannot do.

Everything in `studio/tests/` runs against fakes. This talks to the real
platform, and exists to settle the parts the captured traffic never showed:
what a create response looks like, what field the new entity id arrives under,
and what a bulk load and delete actually return.

SAFE BY CONSTRUCTION. It never touches a domain table. It creates one throwaway
entity named `zz_studio_selftest`, puts three rows in it, and deletes it in a
`finally` block — so a crash mid-test still cleans up. Nothing here reads or
writes `accounts`, `contacts`, or anything else the pipeline owns.

    python -m studio.selftest              # create, load, verify, delete
    python -m studio.selftest --keep       # leave the table behind to inspect

Exits non-zero on the first failure, with the raw response printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from studio import schema
from studio.client import (
    StudioClient,
    StudioError,
    _extract_id,
    _records,
    encode_query,
)
from studio.config import MissingStudioCredentials, load_studio_settings

# Prefixed so it sorts away from real tables and is obviously disposable.
TEST_TABLE = "zz_studio_selftest"
TEST_COLUMNS = ["selftest_id", "label", "amount", "blank"]
TEST_ROWS = [
    {"selftest_id": "1", "label": "first", "amount": "10", "blank": ""},
    {"selftest_id": "2", "label": "second", "amount": "20.5", "blank": ""},
    {"selftest_id": "3", "label": "third", "amount": "", "blank": ""},
]


def _show(label: str, payload: Any) -> None:
    """Print a response compactly — these are the shapes we came to learn."""

    if payload is None:
        print(f"    {label}: <empty body>")
        return
    text = json.dumps(payload, indent=2, default=str)
    if len(text) > 900:
        text = text[:900] + "\n    ... (truncated)"
    print(f"    {label}: {text}")


def _step(number: int, description: str) -> None:
    print(f"\n[{number}] {description}")


def _preflight_token(settings) -> bool:
    """Report token freshness before spending calls on a stale credential.

    A 401 from an expired token and a 401 from a wrong org look identical, and
    the client's one-shot re-mint hides the difference further. Say which it is
    up front — an expired token here is a 30-second fix, not a debugging session.
    """

    import datetime

    from judge.pulse_client import (
        load_pulse_settings,
        load_pulse_token_provider,
        token_expiry,
    )

    expiry = token_expiry(settings.auth_token)
    if expiry is None:
        print("  token  : no readable `exp` claim — cannot check freshness")
        return True

    minutes = (expiry - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 60
    if minutes > 0:
        print(f"  token  : valid for {minutes:.0f} min")
        return True

    print(f"  token  : EXPIRED {-minutes:.0f} min ago ({expiry.isoformat()})")
    try:
        can_refresh = load_pulse_token_provider(load_pulse_settings()) is not None
    except Exception as exc:  # noqa: BLE001 — a broken refresh is not fatal here
        print(f"  refresh: MISCONFIGURED — {exc}")
        can_refresh = False
    else:
        print(f"  refresh: {'configured' if can_refresh else 'NOT configured'}")

    if can_refresh:
        print("  → the first call will 401 and the client will re-mint; continuing")
        return True

    print(
        "\nFAIL: the token is expired and cannot be re-minted.\n"
        "Fix one of these, then re-run:\n"
        "  - paste a fresh PULSE_AUTH_TOKEN into judge/.env (copy it from the\n"
        "    Authorization header of any request in the Studio web app), or\n"
        "  - set PULSE_REFRESH_TOKEN / PULSE_COGNITO_* so it can re-mint itself."
    )
    return False


def run(*, keep: bool = False) -> int:
    try:
        settings = load_studio_settings()
    except MissingStudioCredentials as exc:
        print(f"FAIL: {exc}")
        return 2

    print("Studio self-test")
    print(f"  target : {settings.redacted}")
    print(f"  table  : {TEST_TABLE} (throwaway; no domain table is touched)")
    if not _preflight_token(settings):
        return 2

    entity_id: int | None = None
    client = StudioClient(settings)
    try:
        # 1. Read-only, and the first proof that auth and the route family work.
        _step(1, "GET /org/{org}/entity — can we reach the data plane at all?")
        payload = client._request(
            "GET",
            f"{client.org_path}/entity",
            params={
                "query": encode_query(
                    {"$limit": 5, "$skip": 0, "$select": [{"Name": {}}]}
                )
            },
            summary="list entities",
        )
        _show("response", payload)
        records = _records(payload)
        print(f"    parsed {len(records)} record(s)")
        if records:
            print(
                "    id-looking fields on a record: "
                f"{[k for k in records[0] if 'id' in k.lower()]}"
            )
        else:
            print(
                "    no records parsed — either the org is empty, or the list "
                "envelope differs from what studio.client._records expects."
            )

        # 2. Refuse to continue if a previous run left the table behind.
        _step(2, f"is {TEST_TABLE} already present from an earlier run?")
        existing = client.find_entity_id(TEST_TABLE)
        if existing is not None:
            print(f"    found entity {existing} — deleting it first")
            client.delete_entity(existing)
        else:
            print("    no, clean start")

        # 3. The unverified one: what does create return, and where is the id?
        _step(3, "POST /org/{org}/entity — create, and find the id in the reply")
        numeric = schema.numeric_columns(TEST_ROWS, TEST_COLUMNS)
        definition = schema.entity_definition(TEST_TABLE, TEST_COLUMNS, numeric)
        print(f"    numeric columns inferred: {sorted(numeric)}")
        created = client._request(
            "POST",
            f"{client.org_path}/entity",
            body=definition,
            summary="create entity",
        )
        _show("response", created)
        entity_id = _extract_id(created)
        if entity_id is None:
            print("    FAIL: no id found in the create response.")
            print("    This is the field name the capture never showed — add it to")
            print("    studio.client._extract_id once the shape above is known.")
            return 1
        print(f"    entity id = {entity_id}")

        # 4. Bulk load, including a null numeric and an all-blank column.
        _step(4, "POST /org/{org}/entity/{id}/job — load 3 rows")
        rows = [schema.encode_row(r, TEST_COLUMNS, numeric) for r in TEST_ROWS]
        print(f"    sending: {json.dumps(rows)}")
        loaded = client.load_rows(entity_id, rows)
        _show("response", loaded)
        print("    note: if this returns a job id, loading may be ASYNCHRONOUS —")
        print("    the pipeline would then need to poll before evaluating.")

        # 5. Does the platform agree the table exists and is findable by name?
        _step(5, "GET /org/{org}/entity — is it findable by name now?")
        found = client.find_entity_id(TEST_TABLE)
        print(f"    find_entity_id({TEST_TABLE!r}) -> {found}")
        if found != entity_id:
            print(f"    WARNING: expected {entity_id}. Either the list response "
                  "shape differs from the create response, or creation is async.")

        # 6. Schema context, the last call the UI import makes.
        _step(6, "POST /org/{org}/context — attach the schema context")
        before = client.find_schema_context(entity_id)
        print(f"    existing context: {before}")
        context = client.create_schema_context(entity_id, f"{TEST_TABLE}.csv")
        _show("response", context)

        print("\nAll live calls completed.")
        return 0

    except StudioError as exc:
        print(f"\nFAIL: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — a smoke test reports everything
        print(f"\nFAIL: {type(exc).__name__}: {exc}")
        return 1
    finally:
        # Cleanup runs even on failure: this is the whole reason the test uses a
        # throwaway name rather than a real table.
        if entity_id is not None and not keep:
            _step(7, f"DELETE /org/{{org}}/entity/{entity_id} — cleaning up")
            try:
                client.delete_entity(entity_id)
                gone = client.find_entity_id(TEST_TABLE)
                print(f"    deleted; find_entity_id -> {gone} "
                      f"({'confirmed gone' if gone is None else 'STILL PRESENT'})")
            except Exception as exc:  # noqa: BLE001
                print(f"    CLEANUP FAILED: {type(exc).__name__}: {exc}")
                print(f"    Remove entity {entity_id} by hand: "
                      f"DELETE {client.org_path}/entity/{entity_id}")
        elif entity_id is not None:
            print(f"\n[7] --keep: entity {entity_id} ({TEST_TABLE}) left in place. "
                  "Delete it when you are done.")
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the test table behind so you can inspect it in Studio",
    )
    sys.exit(run(keep=parser.parse_args().keep))


if __name__ == "__main__":
    main()
