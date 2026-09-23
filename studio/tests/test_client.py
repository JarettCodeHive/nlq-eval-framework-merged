"""Transport behaviour of the Studio client, against a fake httpx client.

No network. What is being checked is the shape of what we would send, and how
the client behaves when the platform misbehaves — a long upload is ~130
requests against a shared org, so a 429 or an expiring token mid-run is the
expected case, not the exotic one.
"""

from __future__ import annotations

import base64
import json

import pytest

from studio.client import (
    StudioClient,
    StudioError,
    _extract_id,
    _records,
    encode_query,
)
from studio.config import StudioSettings


def _settings(**overrides) -> StudioSettings:
    base = {
        "base_url": "https://api-qa.platform.claris.com",
        "auth_token": "token-1",
        "org_id": 4104,
        "max_retries": 2,
        "backoff_base_s": 0.001,
        "backoff_max_s": 0.002,
    }
    return StudioSettings(**{**base, **overrides})


class _Response:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeHTTP:
    """Replays a scripted list of responses and records every request."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def request(self, method, path, *, json=None, params=None, headers=None):
        self.requests.append(
            {
                "method": method,
                "path": path,
                "json": json,
                "params": params or {},
                "headers": headers or {},
            }
        )
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


# --- the query DSL -----------------------------------------------------------


def test_query_is_base64_of_a_json_filter_document() -> None:
    """Studio takes a base64'd Mongo-style document, not a query string."""

    encoded = encode_query({"$limit": 1, "$filter": {"$eq": {"Type": "schema"}}})

    assert json.loads(base64.b64decode(encoded)) == {
        "$limit": 1,
        "$filter": {"$eq": {"Type": "schema"}},
    }


def test_find_entity_sends_the_captured_filter() -> None:
    http = _FakeHTTP([_Response(200, [{"Name": "accounts", "Id": 25}])])
    client = StudioClient(_settings(), client=http, token_provider=lambda: "t")

    assert client.find_entity_id("accounts") == 25

    request = http.requests[0]
    assert request["method"] == "GET"
    assert request["path"] == "/org/4104/entity"
    assert json.loads(base64.b64decode(request["params"]["query"])) == {
        "$limit": 100,
        "$skip": 0,
        "$select": [{"Name": {}}],
        "$filter": {"$regex": {"Name": "accounts", "$ci": True}},
    }


def test_find_entity_ignores_a_regex_near_miss() -> None:
    """The filter is a case-insensitive regex, so it also returns `accounts_old`."""

    http = _FakeHTTP([_Response(200, [{"Name": "accounts_old", "Id": 9}])])
    client = StudioClient(_settings(), client=http, token_provider=lambda: "t")

    assert client.find_entity_id("accounts") is None


def test_find_entity_returns_none_when_absent() -> None:
    http = _FakeHTTP([_Response(200, [])])
    client = StudioClient(_settings(), client=http, token_provider=lambda: "t")

    assert client.find_entity_id("accounts") is None


# --- paths and payloads ------------------------------------------------------


def test_load_rows_posts_the_batch_to_the_job_endpoint() -> None:
    http = _FakeHTTP([_Response(200, {"ok": True})])
    client = StudioClient(_settings(), client=http, token_provider=lambda: "t")
    rows = [{"Fields": {"account_id": 1}}, {"Fields": {"account_id": 2}}]

    client.load_rows(25, rows)

    request = http.requests[0]
    assert (request["method"], request["path"]) == ("POST", "/org/4104/entity/25/job")
    assert request["json"] == rows
    assert request["headers"]["Authorization"] == "Bearer token-1"


def test_delete_entity_targets_the_id_path() -> None:
    http = _FakeHTTP([_Response(204)])
    client = StudioClient(_settings(), client=http, token_provider=lambda: "t")

    client.delete_entity(18)

    assert (http.requests[0]["method"], http.requests[0]["path"]) == (
        "DELETE",
        "/org/4104/entity/18",
    )


def test_schema_context_body_matches_the_capture() -> None:
    http = _FakeHTTP([_Response(200, {"Id": 3})])
    client = StudioClient(_settings(), client=http, token_provider=lambda: "t")

    client.create_schema_context(25, "accounts.csv")

    assert http.requests[0]["json"] == {
        "Content": {"Value": {"Type": "plaintext", "PlainText": ""}},
        "Type": "schema",
        "Meta": {
            "EntityDefId": 25,
            "Name": "accounts.csv",
            "SourceDataType": "Other",
        },
    }


# --- the unverified bit: where the new entity's id comes back ----------------


@pytest.mark.parametrize(
    "payload",
    [
        {"Id": 25},
        {"id": 25},
        {"EntityDefId": 25},
        {"data": {"Id": 25}},
        [{"Id": 25}],
        25,
    ],
)
def test_entity_id_is_found_under_any_plausible_key(payload) -> None:
    """The create response is the one shape the capture never showed."""

    assert _extract_id(payload) == 25


def test_a_create_response_with_no_id_fails_loudly_with_the_payload() -> None:
    http = _FakeHTTP([_Response(200, {"status": "created"})])
    client = StudioClient(_settings(), client=http, token_provider=lambda: "t")

    with pytest.raises(StudioError) as excinfo:
        client.create_entity({"Name": "accounts", "Fields": []})

    assert "could not find its id" in str(excinfo.value)
    assert '"status": "created"' in str(excinfo.value)


@pytest.mark.parametrize(
    "payload",
    [[{"Id": 1}], {"data": [{"Id": 1}]}, {"items": [{"Id": 1}]}],
)
def test_list_envelopes_are_normalised(payload) -> None:
    assert _records(payload) == [{"Id": 1}]


# --- failure handling --------------------------------------------------------


def test_a_429_is_retried_then_succeeds() -> None:
    http = _FakeHTTP(
        [_Response(429, text="slow down"), _Response(200, {"Id": 25})]
    )
    client = StudioClient(_settings(), client=http, token_provider=lambda: "t")

    assert client.create_entity({"Name": "accounts", "Fields": []}) == 25
    assert len(http.requests) == 2


def test_retries_are_bounded_and_then_reported() -> None:
    http = _FakeHTTP([_Response(500, text="boom")] * 3)
    client = StudioClient(_settings(), client=http, token_provider=lambda: "t")

    with pytest.raises(StudioError) as excinfo:
        client.load_rows(25, [])

    assert len(http.requests) == 3  # 1 attempt + max_retries=2
    assert "failed after retries" in str(excinfo.value)


def test_a_401_re_mints_the_token_once_and_retries() -> None:
    """A 600k-row upload can outlive a one-hour token mid-batch."""

    http = _FakeHTTP([_Response(401, text="expired"), _Response(200, {"ok": True})])
    client = StudioClient(_settings(), client=http, token_provider=lambda: "token-2")

    client.load_rows(25, [{"Fields": {}}])

    assert http.requests[0]["headers"]["Authorization"] == "Bearer token-1"
    assert http.requests[1]["headers"]["Authorization"] == "Bearer token-2"


def test_a_second_401_is_not_retried_again() -> None:
    http = _FakeHTTP([_Response(401, text="expired")] * 2)
    client = StudioClient(_settings(), client=http, token_provider=lambda: "token-2")

    with pytest.raises(StudioError) as excinfo:
        client.load_rows(25, [])

    assert len(http.requests) == 2
    assert "could not be re-minted" in str(excinfo.value)


def test_a_403_is_not_retried() -> None:
    """A 403 is a permissions answer; re-minting just spends another token."""

    http = _FakeHTTP([_Response(403, text="forbidden")])
    client = StudioClient(_settings(), client=http, token_provider=lambda: "t")

    with pytest.raises(StudioError) as excinfo:
        client.delete_entity(18)

    assert len(http.requests) == 1
    assert "403" in str(excinfo.value)


def test_a_204_with_no_body_is_fine() -> None:
    http = _FakeHTTP([_Response(204)])
    client = StudioClient(_settings(), client=http, token_provider=lambda: "t")

    assert client.delete_entity(18) is None


def test_dry_run_records_the_plan_and_sends_nothing() -> None:
    client = StudioClient(_settings(), dry_run=True)

    client.create_entity({"Name": "accounts", "Fields": [{"Name": "a"}]})
    client.load_rows(25, [{"Fields": {}}] * 4800)

    assert [call["path"] for call in client.calls] == [
        "/org/4104/entity",
        "/org/4104/entity/25/job",
    ]
    assert client.calls[1]["body_items"] == 4800
