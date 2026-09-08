"""Live Pulse client (HC-4) — verified against QA org 4104, 2026-09-07."""

from __future__ import annotations

import json

import httpx
import pytest

from judge.pulse_client import (
    DEFAULT_MODEL_NAME,
    MissingPulseCredentials,
    PulseClient,
    PulseResponseError,
    PulseSettings,
    _bearer,
    _extract_answer,
    _extract_sql,
    load_pulse_settings,
)

_PAIRS = [
    {
        "question_id": "crm-t1-001",
        "natural_language_question": "How many active accounts do we have?",
        "expected_answer": "89",
        "reference_sql": "SELECT count(*) FROM accounts WHERE is_active",
    }
]

# A realistic /tco/chat body (stream=false), trimmed.
_CHAT_BODY = {
    "response": json.dumps(
        {
            "analysis": "There are 89 active accounts.",
            "dashboard": {"root": {"items": []}},
            "reasoning": "counted rows",
        }
    ),
    "error": False,
    "errorCode": None,
    "analysis_request": [
        {
            "id": "primary",
            "sql": "SELECT COUNT(*) AS active_accounts FROM studio.accounts WHERE is_active = 'true'",
        },
        {
            "id": "9f-breakdown",
            "sql": "SELECT industry, COUNT(*) AS n FROM studio.accounts GROUP BY industry",
        },
    ],
    "record_counts": [{"table_name": "accounts", "records": 100}],
    "messages": [],
}


def _settings(**kw) -> PulseSettings:
    base = dict(
        base_url="https://api-qa.platform.claris.com",
        auth_token="eyJraw.jwt.token",
        org_id=4104,
    )
    base.update(kw)
    return PulseSettings(**base)


def _client(handler, **settings_kw) -> PulseClient:
    s = _settings(**settings_kw)
    http = httpx.Client(base_url=s.base_url, transport=httpx.MockTransport(handler))
    return PulseClient(s, _PAIRS, client=http)


# --- credential loading --------------------------------------------------


def test_load_pulse_settings_missing_creds_is_loud(monkeypatch):
    # Don't let a developer's real judge/.env leak into the test.
    monkeypatch.setattr("judge.pulse_client.load_env", lambda *a, **k: None)
    for var in ("PULSE_BASE_URL", "PULSE_AUTH_TOKEN", "PULSE_ORG_ID"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(MissingPulseCredentials, match="PULSE_"):
        load_pulse_settings(env_file=None)


def test_load_pulse_settings_qa_defaults(monkeypatch):
    monkeypatch.setattr("judge.pulse_client.load_env", lambda *a, **k: None)
    monkeypatch.setenv("PULSE_BASE_URL", "https://api-qa.platform.claris.com/")
    monkeypatch.setenv("PULSE_AUTH_TOKEN", "tok")
    monkeypatch.setenv("PULSE_ORG_ID", "4104")
    for var in (
        "PULSE_MODEL_NAME",
        "PULSE_STREAM",
        "PULSE_FEATURES",
        "PULSE_CA_BUNDLE",
    ):
        monkeypatch.delenv(var, raising=False)
    s = load_pulse_settings(env_file=None)
    assert s.org_id == 4104
    assert s.base_url == "https://api-qa.platform.claris.com"  # trailing / trimmed
    assert s.model_name == "claude-sonnet-5"
    assert s.features == "TCOApiV2Feature"
    assert s.chat_path_for() == "/api-proxy/org/4104/ai-svc/v2/tco/chat"
    assert s.httpx_verify() is True
    assert "auth_token" not in s.redacted


# --- auth ------------------------------------------------------------


def test_bearer_prefix_added_only_when_missing():
    assert _bearer("eyJ.raw.jwt") == "Bearer eyJ.raw.jwt"
    assert _bearer("Bearer eyJ.raw.jwt") == "Bearer eyJ.raw.jwt"
    assert _bearer("  bearer x ") == "bearer x"


def test_query_sends_bearer_and_expected_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["features"] = request.headers.get("x-claris-features")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_CHAT_BODY)

    resp = _client(handler).query("crm-t1-001")

    assert seen["url"].endswith("/api-proxy/org/4104/ai-svc/v2/tco/chat")
    assert seen["auth"] == "Bearer eyJraw.jwt.token"
    assert seen["features"] == "TCOApiV2Feature"
    b = seen["body"]
    assert b["prompt"] == "How many active accounts do we have?"
    assert b["messages"][0]["role"] == "user"
    assert b["model_name"] == DEFAULT_MODEL_NAME
    assert b["stream"] is False and b["is_quick_prompt"] is False
    assert resp.answer_text == "There are 89 active accounts."
    assert resp.generated_sql == _CHAT_BODY["analysis_request"][0]["sql"]


def test_chat_session_id_stable_message_id_not():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_CHAT_BODY)

    c = _client(handler)
    c.query("crm-t1-001")
    c.query("crm-t1-001")
    assert bodies[0]["chat_session_id"] == bodies[1]["chat_session_id"]
    assert bodies[0]["message_session_id"] != bodies[1]["message_session_id"]


def test_unknown_question_id_raises_keyerror():
    with pytest.raises(KeyError):
        _client(lambda r: httpx.Response(200, json=_CHAT_BODY)).query("nope")


# --- response extraction (confirmed QA shape) ------------------------


def test_extract_answer_unwraps_stringified_response():
    assert _extract_answer(_CHAT_BODY) == "There are 89 active accounts."


def test_extract_answer_raises_on_error_field():
    with pytest.raises(PulseResponseError, match="error"):
        _extract_answer({"error": True, "errorCode": "E_LIMIT", "response": "{}"})


def test_extract_answer_refusal_is_still_scored():
    assert (
        _extract_answer({"refusal": "Cannot answer.", "response": None})
        == "Cannot answer."
    )


def test_extract_answer_fallback_paths_still_work():
    assert _extract_answer({"answer_text": "A"}) == "A"
    assert _extract_answer({"data": {"summary": "B"}}) == "B"
    with pytest.raises(PulseResponseError, match="keys seen"):
        _extract_answer({"nope": 1})


def test_extract_sql_prefers_primary_entry():
    assert (
        _extract_sql(_CHAT_BODY)
        == "SELECT COUNT(*) AS active_accounts FROM studio.accounts WHERE is_active = 'true'"
    )


def test_extract_sql_joins_all_when_no_primary():
    data = {
        "analysis_request": [
            {"id": "a", "sql": "SELECT 1"},
            {"id": "b", "sql": "SELECT 2"},
        ]
    }
    assert _extract_sql(data) == "SELECT 1;\nSELECT 2"


def test_extract_sql_none_when_absent():
    assert _extract_sql({"response": "{}"}) is None


# --- SSE streaming mode ---------------------------------------------


def test_stream_mode_folds_events():
    sse = (
        'event: content\ndata: {"content": "There are "}\n\n'
        'event: content\ndata: {"content": "89 accounts."}\n\n'
        'event: analysis_complete\ndata: {"analysis_request": [{"id": "primary", "sql": "SELECT 1"}]}\n\n'
        "event: done\ndata: {}\n\n"
    )

    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200, text=sse, headers={"content-type": "text/event-stream"}
        )

    r = _client(handler, stream=True).query("crm-t1-001")
    assert r.answer_text == "There are 89 accounts."
    assert r.generated_sql == "SELECT 1"


def test_stream_error_event_raises():
    def handler(request):
        return httpx.Response(
            200,
            text='event: error\ndata: {"error": "boom"}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    with pytest.raises(PulseResponseError, match="SSE error"):
        _client(handler, stream=True).query("crm-t1-001")


# --- retries / errors ---------------------------------------------


def test_retries_on_503_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="down")
        return httpx.Response(200, json=_CHAT_BODY)

    c = _client(handler, max_retries=3, backoff_base_s=0.001, backoff_max_s=0.002)
    assert c.query("crm-t1-001").answer_text == "There are 89 active accounts."
    assert calls["n"] == 3


def test_non_retryable_4xx_raises_immediately():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, text='{"error":"Unauthenticated"}')

    with pytest.raises(PulseResponseError, match="401"):
        _client(handler, max_retries=3).query("crm-t1-001")
    assert calls["n"] == 1


def test_mode_reflects_stream_setting():
    assert _client(lambda r: httpx.Response(200, json=_CHAT_BODY)).mode == "live"
    assert (
        _client(
            lambda r: httpx.Response(200, text="event: done\ndata: {}\n\n"), stream=True
        ).mode
        == "live-stream"
    )
