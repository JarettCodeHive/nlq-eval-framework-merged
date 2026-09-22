"""Live Pulse client (HC-4) — verified against QA org 4104, 2026-09-07."""

from __future__ import annotations

import base64
import json
import time

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
    assert b["model_name"] == DEFAULT_MODEL_NAME
    assert b["stream"] is False and b["is_quick_prompt"] is False
    assert resp.answer_text == "There are 89 active accounts."
    assert _CHAT_BODY["analysis_request"][0]["sql"] in resp.generated_sql


def test_each_question_gets_its_own_chat_session():
    """Evaluation questions must be independent.

    A shared chat_session_id makes each question a turn in one growing
    conversation, so a later answer can be coloured by an earlier question —
    a confound invisible in the scorecard and unreconstructable afterwards.
    """
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_CHAT_BODY)

    c = _client(handler)
    c.query("crm-t1-001")
    c.query("crm-t1-001")
    assert bodies[0]["chat_session_id"] != bodies[1]["chat_session_id"]
    assert bodies[0]["message_session_id"] != bodies[1]["message_session_id"]


def test_question_is_sent_once_not_twice():
    """`prompt` carries the question; `messages` must not repeat it.

    Duplicating it made every transcript read ["user", "user", "assistant"] —
    the platform saw each question twice, which is not the input we mean to
    measure.
    """
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_CHAT_BODY)

    _client(handler).query("crm-t1-001")
    body = bodies[0]
    assert body["prompt"] == "How many active accounts do we have?"
    assert body["messages"] == []


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


def test_extract_sql_returns_a_lone_statement_bare():
    """One statement needs no labelling — keep the SQL clean."""
    data = {"analysis_request": [{"id": "primary", "sql": "SELECT 1"}]}
    assert _extract_sql(data) == "SELECT 1"


def test_extract_sql_labels_the_multi_statement_case():
    out = _extract_sql(_CHAT_BODY)
    assert out.startswith("-- [primary]")
    assert "SELECT COUNT(*) AS active_accounts" in out
    assert "GROUP BY industry" in out  # the breakdown is no longer discarded


def test_extract_sql_keeps_every_statement_with_primary_first():
    """The platform is an agent: one question can produce several statements,
    and the one tagged `primary` is not necessarily the answering query.

    Keeping only `primary` made the judge penalise the platform for omissions
    that a discarded statement had in fact covered — see _extract_sql's
    docstring for the case this comes from.
    """
    data = {
        "analysis_request": [
            {"id": "breakdown", "sql": "SELECT b FROM t GROUP BY b"},
            {"id": "primary", "sql": "SELECT COUNT(*) FROM t"},
        ]
    }
    out = _extract_sql(data)
    # primary is labelled and leads, but the breakdown survives.
    assert out.startswith("-- [primary]\nSELECT COUNT(*) FROM t")
    assert "SELECT b FROM t GROUP BY b" in out
    assert out.count("-- [") == 2


def test_extract_sql_joins_all_when_no_primary():
    data = {
        "analysis_request": [
            {"id": "a", "sql": "SELECT 1"},
            {"id": "b", "sql": "SELECT 2"},
        ]
    }
    out = _extract_sql(data)
    assert "SELECT 1" in out and "SELECT 2" in out
    assert out.count("-- [") == 2


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


def test_messages_fallback_returns_the_answer_not_the_raw_blob():
    """The assistant turn's content is stringified JSON, not prose.

    Returning it raw handed the judge a ~24KB blob carrying `dashboard` and
    `reasoning` instead of the ~1KB answer — scoring the wrong text and leaking
    the agent's own reasoning into it.
    """
    inner = json.dumps(
        {
            "analysis": "There are 6,673 contacts.",
            "dashboard": {"title": "should not be scored"},
            "reasoning": "should not leak into the judged answer",
        }
    )
    data = {
        "error": False,
        "messages": [
            {"role": "user", "content": "How many contacts?"},
            {"role": "assistant", "content": inner},
        ],
    }
    out = _extract_answer(data)
    assert out == "There are 6,673 contacts."
    assert "dashboard" not in out
    assert "reasoning" not in out


def test_messages_fallback_never_returns_a_user_turn():
    """A user turn is the question, not an answer — scoring it would compare
    the question against itself."""
    data = {
        "error": False,
        "messages": [
            {"role": "user", "content": "How many contacts?"},
            {"role": "assistant", "content": json.dumps({"analysis": "6,673."})},
        ],
    }
    assert _extract_answer(data) == "6,673."


# --- token refresh -------------------------------------------------------


def _jwt(exp_epoch: int) -> str:
    """A structurally valid unsigned JWT carrying just an `exp` claim."""
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": exp_epoch}).encode())
        .decode()
        .rstrip("=")
    )
    return f"header.{payload}.signature"


def _fresh_jwt(minutes: int = 60) -> str:
    return _jwt(int(time.time()) + minutes * 60)


def _provider_client(handler, provider, **settings_kw) -> PulseClient:
    s = _settings(**settings_kw)
    http = httpx.Client(base_url=s.base_url, transport=httpx.MockTransport(handler))
    return PulseClient(s, _PAIRS, client=http, token_provider=provider)


def test_no_provider_sends_the_configured_token_unchanged():
    """The default path must not change: without a provider the token in
    settings is what goes on the wire, every time."""
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json=_CHAT_BODY)

    c = _client(handler, auth_token="eyJconfigured.jwt.token")
    c.query("crm-t1-001")
    assert seen == ["Bearer eyJconfigured.jwt.token"]


def test_token_close_to_expiry_is_reminted_before_the_request():
    minted = _fresh_jwt(60)
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        return minted

    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json=_CHAT_BODY)

    # 60s of life left is inside TOKEN_REFRESH_MARGIN_S, so it must be replaced
    # before the call rather than after a failure.
    c = _provider_client(handler, provider, auth_token=_jwt(int(time.time()) + 60))
    c.query("crm-t1-001")
    assert calls["n"] == 1
    assert seen == [f"Bearer {minted}"]


def test_token_with_headroom_is_not_reminted():
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        return _fresh_jwt(60)

    c = _provider_client(
        lambda r: httpx.Response(200, json=_CHAT_BODY),
        provider,
        auth_token=_fresh_jwt(50),
    )
    c.query("crm-t1-001")
    assert calls["n"] == 0, "a token with 50 min left should be reused"


def test_401_triggers_exactly_one_remint_then_succeeds():
    minted = _fresh_jwt(60)
    provider_calls = {"n": 0}

    def provider():
        provider_calls["n"] += 1
        return minted

    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        if len(seen) == 1:
            return httpx.Response(401, text='{"error":"Unauthenticated"}')
        return httpx.Response(200, json=_CHAT_BODY)

    c = _provider_client(handler, provider, auth_token=_fresh_jwt(50))
    assert c.query("crm-t1-001").answer_text == "There are 89 active accounts."
    assert provider_calls["n"] == 1
    assert seen[1] == f"Bearer {minted}"


def test_401_after_a_remint_gives_up_instead_of_looping():
    """A fresh token that is also rejected means the credential source is wrong.
    Retrying would burn tokens against a wall."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, text="nope")

    with pytest.raises(PulseResponseError, match="401"):
        _provider_client(
            handler, lambda: _fresh_jwt(60), auth_token=_fresh_jwt(50), max_retries=3
        ).query("crm-t1-001")
    assert calls["n"] == 2, "one original attempt + one after re-minting"


def test_auth_retry_does_not_consume_the_retryable_budget():
    """A 401 arriving on the final retry must still get its re-mint. Otherwise a
    token expiring late in a run loses an answer a refresh would have saved."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(503, text="down")
        if calls["n"] == 3:
            return httpx.Response(401, text="expired")
        return httpx.Response(200, json=_CHAT_BODY)

    c = _provider_client(
        handler,
        lambda: _fresh_jwt(60),
        auth_token=_fresh_jwt(50),
        max_retries=2,
        backoff_base_s=0.001,
        backoff_max_s=0.002,
    )
    assert c.query("crm-t1-001").answer_text == "There are 89 active accounts."
    assert calls["n"] == 4


def test_provider_returning_nothing_fails_loudly():
    with pytest.raises(PulseResponseError, match="empty token"):
        _provider_client(
            lambda r: httpx.Response(200, json=_CHAT_BODY),
            lambda: "  ",
            auth_token=_jwt(int(time.time()) + 60),
        ).query("crm-t1-001")
