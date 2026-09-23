"""Claris Studio data-plane client — create, load and delete entities.

Route family and payloads were reverse-engineered from the Studio QA web app's
own network traffic, because the published Swagger covers only the AI service
(chat, guidelines, read-only dataproviders) and has nothing for data management.
Each method below names the captured request it reproduces.

    GET    /org/{org}/entity?query=<base64>   list / find by name
    POST   /org/{org}/entity                  create, returns the entity id
    POST   /org/{org}/entity/{id}/job         bulk-load rows
    DELETE /org/{org}/entity/{id}             delete
    GET    /org/{org}/context?query=<base64>  find a schema context
    POST   /org/{org}/context                 attach a schema context

`query` is a base64-encoded Mongo-style filter document, not a query string.

Retry, TLS and token handling are shared with the judge's platform client so
the two cannot drift apart on a corp network — see `judge/pulse_client.py`.
"""

from __future__ import annotations

import base64
import json
import random
import sys
import time
from typing import Any

from judge.pulse_client import (
    _bearer,
    _is_transient,
    load_pulse_settings,
    load_pulse_token_provider,
    resolve_tls_verify,
)
from studio.config import StudioSettings


# A bulk load returns a job, not a result:
#   {"ID": 350, "Name": "Batch create 3 entities", "Status": "waiting"}
#
# VERIFIED 2026-09-23 against org 4104: `GET /org/{org}/job/{id}` reports it, and
# a finished job carries the detail that actually matters:
#   {"Status": "succeeded",
#    "Result": {"TotalCount": 3, "SucceededCount": 3, "FailedCount": 0,
#               "CRUD": {"Succeeded": [{"ID": 1355633, "Index": 0}, ...]}}}
# The alternatives are kept as a fallback in case the route moves.
JOB_ROUTE_CANDIDATES: tuple[str, ...] = (
    "{org}/job/{job}",
    "{org}/jobs/{job}",
    "{org}/entity/{entity}/job/{job}",
)

# Statuses that mean "not finished yet". Anything else is treated as terminal,
# so an unrecognised status ends the wait rather than hanging forever — and is
# reported, because a status we do not know about is worth a human look.
JOB_PENDING_STATUSES: frozenset[str] = frozenset(
    {"waiting", "queued", "pending", "running", "processing", "in_progress", "started"}
)
JOB_FAILED_STATUSES: frozenset[str] = frozenset(
    {"failed", "error", "errored", "cancelled", "canceled", "aborted"}
)
# The verified success value. Listed so an unfamiliar terminal status can be
# called out rather than quietly accepted as success.
JOB_SUCCESS_STATUSES: frozenset[str] = frozenset({"succeeded", "success", "complete",
                                                  "completed", "done", "finished"})


class StudioError(RuntimeError):
    """A Studio request failed in a way the caller cannot retry past."""


class StudioJobError(StudioError):
    """A bulk-load job did not finish, or finished badly."""


class _RetryableStudioError(RuntimeError):
    """Internal: a 429/5xx worth backing off and retrying."""


class _AuthStudioError(RuntimeError):
    """Internal: a 401. Retryable once, and only after re-minting the token."""


def encode_query(document: dict[str, Any]) -> str:
    """Studio's `query` parameter: base64 of a JSON filter document."""

    return base64.b64encode(
        json.dumps(document, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def _extract_id(payload: Any) -> int | None:
    """Pull an entity id out of a response.

    VERIFIED against org 4104 on 2026-09-23: both the create response and the
    list records carry it as `ID` (two capitals). The other spellings are kept
    as a cheap hedge against a schema tweak, and an unrecognised payload is
    reported in full rather than failing opaquely.
    """

    if isinstance(payload, int):
        return payload
    if isinstance(payload, dict):
        for key in ("Id", "ID", "id", "EntityDefId", "entityDefId", "entity_id"):
            value = payload.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        for key in ("data", "Data", "result", "Result", "entity", "Entity"):
            if key in payload:
                found = _extract_id(payload[key])
                if found is not None:
                    return found
    if isinstance(payload, list) and len(payload) == 1:
        return _extract_id(payload[0])
    return None


def _records(payload: Any) -> list[dict]:
    """Pull the records out of a list response.

    VERIFIED shape: `{"Data": [...], "Total": 9, "Page": 1, "TotalPages": 2,
    "Limit": 5}` — so the list is paginated, which `find_entity_id` has to
    account for. The other envelope keys are a hedge.
    """

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "Data", "items", "Items", "records", "Records", "value"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _assert_no_rows_failed(status: dict, job_id: int, entity_id: int) -> None:
    """Raise if the job dropped rows, however cheerful its Status is."""

    result = status.get("Result")
    if not isinstance(result, dict):
        return
    failed = result.get("FailedCount")
    total = result.get("TotalCount")
    succeeded = result.get("SucceededCount")
    if isinstance(failed, int) and failed > 0:
        raise StudioJobError(
            f"bulk-load job {job_id} for entity {entity_id} reported status "
            f"{status.get('Status')!r} but dropped {failed} of {total} rows. "
            f"The table is short. Detail: {json.dumps(result, default=str)[:400]}"
        )
    if isinstance(total, int) and isinstance(succeeded, int) and succeeded != total:
        raise StudioJobError(
            f"bulk-load job {job_id} for entity {entity_id}: {succeeded} of "
            f"{total} rows succeeded with no FailedCount to explain the gap."
        )


def _token_is_expired(token: str) -> bool:
    """True when the JWT's own `exp` says it is already dead."""

    import datetime

    from judge.pulse_client import token_expiry

    try:
        expiry = token_expiry(token)
    except Exception:  # noqa: BLE001 — an unreadable token is not our problem here
        return False
    if expiry is None:
        return False
    return expiry <= datetime.datetime.now(datetime.timezone.utc)


class StudioClient:
    """One org's Studio data plane. Not thread-safe; use one per thread."""

    def __init__(
        self,
        settings: StudioSettings,
        *,
        client: Any | None = None,  # httpx.Client — injectable for tests
        token_provider: Any | None = None,  # Callable[[], str]
        dry_run: bool = False,
    ) -> None:
        self._settings = settings
        self._token = settings.auth_token
        self._dry_run = dry_run
        # Re-minting is configured by PULSE_REFRESH_*; a full CRM upload is
        # 600k rows across ~130 requests and can outlive a one-hour token.
        if token_provider is None and not dry_run:
            try:
                token_provider = load_pulse_token_provider(load_pulse_settings())
            except Exception:  # noqa: BLE001 — refresh is optional, not required
                token_provider = None
        self._token_provider = token_provider
        if token_provider is not None and not dry_run and _token_is_expired(
            settings.auth_token
        ):
            # Every request would otherwise 401 once before re-minting. Harmless
            # but noisy, and it makes a real 401 harder to spot in a long upload.
            try:
                self._token = token_provider()
            except Exception:  # noqa: BLE001 — fall back to the 401 path
                pass
        # Every request the client made, for --dry-run and for auditing a run.
        self.calls: list[dict[str, Any]] = []
        # None = not probed yet, "" = probed and nothing worked, else a template.
        self._job_route: str | None = None

        if client is not None or dry_run:
            self._client = client
            self._owns_client = False
        else:
            import httpx

            self._client = httpx.Client(
                base_url=settings.base_url,
                timeout=settings.timeout_s,
                verify=resolve_tls_verify_for(settings),
            )
            self._owns_client = True

    # --- plumbing --------------------------------------------------------

    @property
    def org_path(self) -> str:
        return f"/org/{self._settings.org_id}"

    @property
    def batch_rows(self) -> int:
        """Rows per bulk-load request — the caller splits, the client posts."""

        return self._settings.batch_rows

    @property
    def batch_bytes(self) -> int:
        """Maximum serialised size of one bulk-load request."""

        return self._settings.batch_bytes

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": _bearer(self._token),
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
        }

    def _check(self, status_code: int, text: str) -> None:
        if 200 <= status_code < 300:
            return
        snippet = (text or "")[:500]
        if status_code == 429 or status_code >= 500:
            raise _RetryableStudioError(f"Studio {status_code}: {snippet!r}")
        if status_code == 401:
            raise _AuthStudioError(f"Studio 401: {snippet!r}")
        raise StudioError(f"Studio {status_code}: {snippet!r}")

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        params: dict[str, str] | None = None,
        summary: str = "",
    ) -> Any:
        """One request with bounded backoff and a single re-mint on 401."""

        record = {
            "method": method,
            "path": path,
            "params": params or {},
            "summary": summary or f"{method} {path}",
            "body_items": len(body) if isinstance(body, list) else None,
        }
        self.calls.append(record)
        if self._dry_run:
            return None

        settings = self._settings
        last_exc: Exception | None = None
        refreshed = False
        for attempt in range(settings.max_retries + 1):
            try:
                response = self._client.request(
                    method,
                    path,
                    json=body,
                    params=params,
                    headers=self._headers(),
                )
                self._check(response.status_code, response.text)
                if not response.content:
                    return None
                try:
                    return response.json()
                except ValueError:
                    return response.text
            except _AuthStudioError as exc:
                last_exc = exc
                if refreshed or self._token_provider is None:
                    raise StudioError(
                        f"{exc} — the Studio token is rejected and could not be "
                        "re-minted. Refresh PULSE_AUTH_TOKEN, or configure "
                        "PULSE_REFRESH_* so long uploads can renew it."
                    ) from exc
                self._token = self._token_provider()
                refreshed = True
                continue
            except _RetryableStudioError as exc:
                last_exc = exc
            except Exception as exc:  # noqa: BLE001 — network faults only
                if not _is_transient(exc):
                    raise
                last_exc = exc
            if attempt < settings.max_retries:
                delay = min(
                    settings.backoff_max_s, settings.backoff_base_s * (2**attempt)
                )
                time.sleep(delay * (0.5 + random.random() / 2))
        raise StudioError(f"{summary or path} failed after retries: {last_exc}")

    def close(self) -> None:
        if getattr(self, "_owns_client", False) and self._client is not None:
            self._client.close()

    def __enter__(self) -> "StudioClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- entities --------------------------------------------------------

    def find_entity_id(self, name: str) -> int | None:
        """The id of the entity called `name`, or None. Reproduces capture 1.

        The UI filters with a case-insensitive regex, which also matches
        `accounts_old`, so the exact name is re-checked on the results.

        The response is paginated (`Total` / `TotalPages` / `Limit`). A regex on
        one table name should never overflow one page of 100, but if it somehow
        does, a silent miss would read as "this table does not exist" and cause
        a duplicate upload — so that case raises instead.
        """

        payload = self._request(
            "GET",
            f"{self.org_path}/entity",
            params={
                "query": encode_query(
                    {
                        "$limit": 100,
                        "$skip": 0,
                        "$select": [{"Name": {}}],
                        "$filter": {"$regex": {"Name": name, "$ci": True}},
                    }
                )
            },
            summary=f"find entity {name!r}",
        )
        if isinstance(payload, dict):
            total, limit = payload.get("Total"), payload.get("Limit")
            if isinstance(total, int) and isinstance(limit, int) and total > limit:
                raise StudioError(
                    f"the entity list for {name!r} is paginated ({total} matches "
                    f"over a {limit}-row page) and this client reads only the "
                    "first page. Narrow the filter before trusting the result."
                )
        for record in _records(payload):
            if str(record.get("Name", "")).lower() == name.lower():
                found = _extract_id(record)
                if found is not None:
                    return found
        return None

    def create_entity(self, definition: dict[str, Any]) -> int:
        """Create the table and return its id. Reproduces capture 2."""

        payload = self._request(
            "POST",
            f"{self.org_path}/entity",
            body=definition,
            summary=f"create entity {definition.get('Name')!r} "
            f"({len(definition.get('Fields', []))} fields)",
        )
        if self._dry_run:
            return -1
        entity_id = _extract_id(payload)
        if entity_id is None:
            raise StudioError(
                "created the entity but could not find its id in the response. "
                "The id field name is the one part of this API that the captured "
                f"traffic never showed. Response was: {json.dumps(payload)[:500]}"
            )
        return entity_id

    def load_rows(self, entity_id: int, rows: list[dict[str, Any]]) -> Any:
        """Bulk-load one batch of rows. Reproduces capture 6."""

        return self._request(
            "POST",
            f"{self.org_path}/entity/{entity_id}/job",
            body=rows,
            summary=f"load {len(rows)} rows into entity {entity_id}",
        )

    def delete_entity(self, entity_id: int) -> None:
        """Delete the table and its data. Reproduces the DELETE capture."""

        self._request(
            "DELETE",
            f"{self.org_path}/entity/{entity_id}",
            summary=f"delete entity {entity_id}",
        )

    # --- bulk-load jobs --------------------------------------------------

    def discover_job_route(self, entity_id: int, job_id: int) -> str | None:
        """Find the route that reports a job's status, and remember it.

        Tried once per client and cached: every batch of every table would
        otherwise re-probe the same dead candidates.
        """

        if self._job_route is not None:
            return self._job_route or None

        for template in JOB_ROUTE_CANDIDATES:
            path = template.format(
                org=self.org_path, entity=entity_id, job=job_id
            )
            try:
                response = self._client.request(
                    "GET", path, json=None, params=None, headers=self._headers()
                )
            except Exception:  # noqa: BLE001 — a dead candidate is the normal case
                continue
            if 200 <= response.status_code < 300:
                self._job_route = template
                return template
        # Cache the failure as an empty string so we probe only once.
        self._job_route = ""
        return None

    def job_status(self, entity_id: int, job_id: int) -> dict | None:
        """One status read, or None if no route is known."""

        template = self.discover_job_route(entity_id, job_id)
        if template is None:
            return None
        payload = self._request(
            "GET",
            template.format(org=self.org_path, entity=entity_id, job=job_id),
            summary=f"status of job {job_id}",
        )
        return payload if isinstance(payload, dict) else None

    def wait_for_job(
        self,
        entity_id: int,
        job_id: int,
        *,
        timeout_s: float | None = None,
        poll_s: float = 1.0,
    ) -> dict | None:
        """Block until a bulk-load job leaves a pending status.

        Returns the last status document, or None when no status route exists —
        in which case the caller has to decide whether to trust an unconfirmed
        load. It never silently treats "unknown" as "finished".
        """

        deadline = time.monotonic() + (
            timeout_s if timeout_s is not None else self._settings.job_timeout_s
        )
        last: dict | None = None
        while True:
            last = self.job_status(entity_id, job_id)
            if last is None:
                return None
            status = str(last.get("Status", "")).strip().lower()
            if status in JOB_FAILED_STATUSES:
                raise StudioJobError(
                    f"bulk-load job {job_id} for entity {entity_id} reported "
                    f"status {status!r}: {json.dumps(last, default=str)[:300]}"
                )
            if status not in JOB_PENDING_STATUSES:
                # A job reports "succeeded" as a whole while still having dropped
                # individual rows — `FailedCount` is the only thing that says so.
                # Trusting `Status` alone would silently accept a short table.
                _assert_no_rows_failed(last, job_id, entity_id)
                if status not in JOB_SUCCESS_STATUSES:
                    print(
                        f"[studio] NOTE: job {job_id} finished with an unfamiliar "
                        f"status {status!r}; treating it as terminal. Payload: "
                        f"{json.dumps(last, default=str)[:200]}",
                        file=sys.stderr,
                    )
                return last
            if time.monotonic() >= deadline:
                raise StudioJobError(
                    f"bulk-load job {job_id} for entity {entity_id} was still "
                    f"{status!r} after {self._settings.job_timeout_s:.0f}s. The rows "
                    "may still be loading; do not evaluate against this table yet."
                )
            time.sleep(poll_s)

    def count_rows(self, entity_id: int) -> int | None:
        """How many rows the platform actually holds, or None if it will not say.

        VERIFIED route: `GET /org/{org}/entity/{id}/record`, which answers with
        the same paginated envelope as `/entity` — so `Total` is the row count
        without pulling the rows. This is the independent check on a load: job
        statuses describe batches, this describes the table.
        """

        payload = self._request(
            "GET",
            f"{self.org_path}/entity/{entity_id}/record",
            params={"query": encode_query({"$limit": 1, "$skip": 0})},
            summary=f"count rows in entity {entity_id}",
        )
        if isinstance(payload, dict) and isinstance(payload.get("Total"), int):
            return payload["Total"]
        return None

    # --- schema context --------------------------------------------------

    def find_schema_context(self, entity_id: int) -> int | None:
        """The schema context already attached to this entity, if any."""

        payload = self._request(
            "GET",
            f"{self.org_path}/context",
            params={
                "query": encode_query(
                    {
                        "$limit": 1,
                        "$skip": 0,
                        "$filter": {
                            "$and": [
                                {"$eq": {"Type": "schema"}},
                                {"$eq": {"Meta.EntityDefId": entity_id}},
                            ]
                        },
                    }
                )
            },
            summary=f"find schema context for entity {entity_id}",
        )
        records = _records(payload)
        return _extract_id(records[0]) if records else None

    def create_schema_context(
        self, entity_id: int, source_name: str, description: str = ""
    ) -> Any:
        """Attach the schema context the AI side reads. Reproduces capture 5.

        `description` is the free text the platform shows the model alongside
        the schema. The UI import leaves it empty; a populated one is the
        supported place to state what a table means.
        """

        return self._request(
            "POST",
            f"{self.org_path}/context",
            body={
                "Content": {"Value": {"Type": "plaintext", "PlainText": description}},
                "Type": "schema",
                "Meta": {
                    "EntityDefId": entity_id,
                    "Name": source_name,
                    "SourceDataType": "Other",
                },
            },
            summary=f"attach schema context to entity {entity_id}",
        )


def resolve_tls_verify_for(settings: StudioSettings) -> "str | bool":
    """Same TLS resolution the judge's platform client uses.

    `resolve_tls_verify` is typed against PulseSettings but only calls
    `httpx_verify()`, which StudioSettings also provides — so the corp
    inspection CA is trusted identically on both paths.
    """

    return resolve_tls_verify(settings)  # type: ignore[arg-type]
