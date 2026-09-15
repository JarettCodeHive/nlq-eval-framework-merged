"""FloodgateJudge tests.

The `anthropic` SDK is only needed by Floodgate runs, so these drive a stub
module installed into `sys.modules`. That is not merely convenient: it is the
only way to assert the exact request Floodgate receives — the bare model id, the
JSON prefill turn, and the headers Floodgate authenticates and meters on.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from judge.config import (
    FloodgateNarrativeSettings,
    FloodgateOIDCSettings,
    MissingCredentials,
    load_judge_config,
)
from judge.contracts import JudgeRequest
from judge.floodgate_judge import (
    FloodgateJudge,
    appleconnect_token,
    explain_floodgate_error,
)
from judge.parsing import JudgeOutputError

GOOD = (
    '{"factual_correctness": 5, "completeness": 4, "format_adherence": 5, '
    '"sql_plausibility": 4, "rationales": {"factual_correctness": "matches", '
    '"completeness": "mostly", "format_adherence": "fine", '
    '"sql_plausibility": "plausible"}}'
)

REQ = JudgeRequest(
    question="How many accounts?",
    expected_answer="72",
    judge_reference="72 active accounts",
    platform_answer="There are 72.",
    generated_sql="SELECT COUNT(*) FROM accounts",
    domain="crm",
    question_id="q-001",
)


# --- anthropic stub ---------------------------------------------------


class _StubStatusError(Exception):
    def __init__(self, status_code: int, message: str = "boom", retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
        self.response = types.SimpleNamespace(status_code=status_code, headers=headers)


class _StubConnectionError(Exception):
    pass


class _StubTimeoutError(Exception):
    pass


def _resp(text: str, stop_reason: str = "end_turn"):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
    )


class _StubAnthropic:
    instances: list["_StubAnthropic"] = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.calls: list[dict] = []
        self.script: list = []
        self.closed = False
        self.messages = types.SimpleNamespace(create=self._create)
        _StubAnthropic.instances.append(self)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.script.pop(0) if self.script else _resp(GOOD[1:])
        if isinstance(result, Exception):
            raise result
        return result

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def stub_anthropic(monkeypatch):
    module = types.ModuleType("anthropic")
    module.AsyncAnthropic = _StubAnthropic
    module.APIStatusError = _StubStatusError
    module.APIConnectionError = _StubConnectionError
    module.APITimeoutError = _StubTimeoutError
    monkeypatch.setitem(sys.modules, "anthropic", module)
    _StubAnthropic.instances.clear()
    yield module


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Backoff is asserted by call count, not by wall clock."""
    slept: list[float] = []

    async def _sleep(delay):
        slept.append(delay)

    monkeypatch.setattr("judge.floodgate_judge.asyncio.sleep", _sleep)
    return slept


def _cfg(**overrides):
    base = {
        "model": "anthropic.claude-sonnet-5",
        "seed": None,
        "cache_enabled": False,
        "max_retries": 3,
        "backoff_base_s": 0.001,
        "backoff_max_s": 0.002,
    }
    return load_judge_config("crm").model_copy(update={**base, **overrides})


def _judge(script=None, *, config=None, **settings_kwargs) -> FloodgateJudge:
    judge = FloodgateJudge(
        FloodgateOIDCSettings(**settings_kwargs),
        config or _cfg(),
        token_provider=lambda: "tok-1",
    )
    # The one-off parameter probe would eat the first scripted response;
    # tests that care about it set _probed = False themselves.
    judge._probed = True
    if script is not None:
        _StubAnthropic.instances[-1].script = list(script)
    return judge


def _stub() -> _StubAnthropic:
    return _StubAnthropic.instances[-1]


# --- settings ---------------------------------------------------------


def test_base_url_stops_before_v1_so_the_sdk_can_append_it():
    # The Anthropic SDK appends `/v1/messages`; a base URL carrying /v1 doubles it.
    assert not FloodgateOIDCSettings().base_url.rstrip("/").endswith("/v1")
    assert FloodgateOIDCSettings().base_url.startswith("https://floodgate.g.apple.com")


def test_redacted_provenance_never_leaks_the_project_token():
    s = FloodgateOIDCSettings(project_token="secret-abc")
    blob = " ".join(f"{k}={v}" for k, v in s.redacted.items())
    assert "secret-abc" not in blob
    assert s.redacted["project_token_set"] == "True"
    assert s.redacted["auth"] == "appleconnect-oidc"


def test_narrative_settings_report_mtls_provenance():
    s = FloodgateNarrativeSettings(cert_path="/tls/tls.crt", key_path="/tls/tls.key")
    assert s.redacted["auth"] == "narrative-mtls"


# --- model validation -------------------------------------------------


def test_a_non_anthropic_model_fails_before_the_first_call():
    # config/judge/default.json ships gpt-4o-mini; a Floodgate run that forgets
    # FLOODGATE_MODEL must not discover that as a 400 from the proxy.
    with pytest.raises(ValueError, match="FLOODGATE_MODEL"):
        FloodgateJudge(
            FloodgateOIDCSettings(),
            _cfg(model="gpt-4o-mini"),
            token_provider=lambda: "t",
        )


def test_a_prefixed_model_id_names_the_bare_form():
    with pytest.raises(ValueError, match="anthropic.claude-sonnet-5"):
        FloodgateJudge(
            FloodgateOIDCSettings(),
            _cfg(model="aws:anthropic.claude-sonnet-5"),
            token_provider=lambda: "t",
        )


# --- request shape ----------------------------------------------------


async def test_prefill_forces_json_and_is_reattached():
    judge = _judge([_resp('"factual_correctness": 5}')])
    raw = await judge._complete("score this")

    assert _stub().calls[0]["messages"] == [
        {"role": "user", "content": "score this"},
        {"role": "assistant", "content": "{"},
    ]
    # The model never re-emits the prefilled brace, so the judge restores it
    # before judge.parsing — which is strict and would reject a bare fragment.
    assert raw == '{"factual_correctness": 5}'


async def test_no_prefill_when_json_mode_is_off():
    judge = _judge([_resp("free text")], config=_cfg(json_mode=False))
    raw = await judge._complete("p")

    assert len(_stub().calls[0]["messages"]) == 1
    assert raw == "free text"


async def test_model_is_sent_bare_at_temperature_zero_with_no_seed():
    judge = _judge()
    await judge._complete("p")

    call = _stub().calls[0]
    assert call["model"] == "anthropic.claude-sonnet-5"
    assert call["temperature"] == 0.0
    assert "seed" not in call  # Anthropic has no such parameter


def test_a_seed_warns_rather_than_being_silently_dropped():
    with pytest.warns(RuntimeWarning, match="no `seed`"):
        FloodgateJudge(
            FloodgateOIDCSettings(), _cfg(seed=42), token_provider=lambda: "t"
        )


def test_model_version_records_the_proxy_not_just_the_model():
    # Cache keys and verdicts stamp this; the same model elsewhere is not the
    # same measurement.
    assert _judge()._model_str == "floodgate/anthropic.claude-sonnet-5"


async def test_temperature_is_always_enforced_on_anthropic():
    judge = _judge([_resp(GOOD[1:])])
    verdict = await judge._judge_combined(REQ)
    assert verdict.temperature_enforced is True
    assert verdict.model_version == "floodgate/anthropic.claude-sonnet-5"
    assert verdict.factual_correctness == 5


# --- temperature: adaptive-thinking models reject it outright ----------


def _temp_rejected(status: int = 400) -> _StubStatusError:
    # Verbatim shape of what Floodgate returns for anthropic.claude-sonnet-5.
    return _StubStatusError(
        status,
        "Failed to call LLM service: invoking wrapper: http response error "
        "StatusCode: 400, ValidationException: `temperature` is deprecated for "
        "this model.",
    )


async def test_temperature_rejection_fails_loudly_by_default():
    # Anthropic has no seed, so dropping temperature leaves NO determinism
    # control. §10.1 says that must not happen silently.
    judge = _judge([_temp_rejected()], config=_cfg(require_temperature_zero=True))

    with pytest.raises(RuntimeError, match="no determinism control at all"):
        await judge._complete("p")


async def test_temperature_rejection_degrades_when_the_run_allows_it():
    judge = _judge(
        [_temp_rejected(), _resp("}")],
        config=_cfg(require_temperature_zero=False),
    )

    with pytest.warns(RuntimeWarning, match="NO determinism control"):
        assert await judge._complete("p") == "{}"

    # First attempt carried temperature; the retry dropped it for good.
    assert _stub().calls[0]["temperature"] == 0.0
    assert "temperature" not in _stub().calls[1]
    assert judge.temperature_enforced is False


async def test_a_degraded_run_stamps_temperature_enforced_false_on_the_verdict():
    judge = _judge(
        [_temp_rejected(), _resp(GOOD[1:])],
        config=_cfg(require_temperature_zero=False),
    )
    with pytest.warns(RuntimeWarning):
        verdict = await judge._judge_combined(REQ)

    # This is what reaches the scorecard and the run manifest.
    assert verdict.temperature_enforced is False


async def test_the_probe_negotiates_once_before_the_workers_start():
    judge = _judge(config=_cfg(require_temperature_zero=False))
    judge._probed = False
    _stub().script = [_temp_rejected(), _resp("}"), _resp("}"), _resp("}")]

    with pytest.warns(RuntimeWarning):
        await asyncio.gather(judge._complete("a"), judge._complete("b"))

    # probe (rejected) + probe retry + two real calls — and no worker paid the
    # 400 itself, which is the point of probing serially.
    assert len(_stub().calls) == 4
    assert all("temperature" not in c for c in _stub().calls[1:])


async def test_a_400_that_is_not_about_temperature_is_not_renegotiated():
    judge = _judge([_StubStatusError(400, "unknown model")])

    with pytest.raises(RuntimeError, match="unknown model"):
        await judge._complete("p")
    assert len(_stub().calls) == 1


# --- prefill: Vertex-served Claude rejects it --------------------------


def _prefill_rejected() -> _StubStatusError:
    # Verbatim shape of what Vertex returns via Floodgate.
    return _StubStatusError(
        400,
        "This model does not support assistant message prefill. The conversation "
        "must end with a user message.",
    )


async def test_prefill_rejection_degrades_and_keeps_scoring():
    # Floodgate picks Bedrock vs Vertex itself, so this can happen on any model
    # at any time — it must never fail a run.
    judge = _judge([_prefill_rejected(), _resp(GOOD)])

    with pytest.warns(RuntimeWarning, match="rejects assistant prefill"):
        raw = await judge._complete("p")

    assert len(_stub().calls[0]["messages"]) == 2  # first try carried the prefill
    assert _stub().calls[1]["messages"] == [{"role": "user", "content": "p"}]
    # No brace is re-attached once prefill is off, or the JSON would be corrupted.
    assert raw == GOOD


async def test_prefill_degradation_is_permanent_for_the_instance():
    judge = _judge([_prefill_rejected(), _resp(GOOD), _resp(GOOD)])
    with pytest.warns(RuntimeWarning):
        await judge._complete("p")
    await judge._complete("p")

    assert judge._prefill_json is False
    assert len(_stub().calls[2]["messages"]) == 1


async def test_temperature_and_prefill_can_both_be_negotiated_away():
    judge = _judge(
        [_temp_rejected(), _prefill_rejected(), _resp(GOOD)],
        config=_cfg(require_temperature_zero=False),
    )
    with pytest.warns(RuntimeWarning):
        assert await judge._complete("p") == GOOD

    final = _stub().calls[-1]
    assert "temperature" not in final
    assert len(final["messages"]) == 1


# --- headers ----------------------------------------------------------


async def test_required_and_optional_floodgate_headers():
    judge = _judge(
        [_resp("}"), _resp("}")],
        user_agent="nlq-judge/2.0",
        project_token="proj-secret",
    )
    await judge._complete("p")
    await judge._complete("p")

    first, second = (c["extra_headers"] for c in _stub().calls)
    assert first["User-Agent"] == "nlq-judge/2.0"
    assert first["Authorization"] == "Bearer tok-1"
    assert first["X-Floodgate-Project-Token"] == "proj-secret"
    # A reused request id makes a retry storm look to Floodgate like one slow call.
    assert first["X-Client-Request-Id"] != second["X-Client-Request-Id"]


async def test_the_token_is_minted_once_and_reused_within_its_ttl():
    minted: list[int] = []

    def provider() -> str:
        minted.append(1)
        return f"tok-{len(minted)}"

    judge = FloodgateJudge(FloodgateOIDCSettings(), _cfg(), token_provider=provider)
    judge._probed = True
    _stub().script = [_resp("}"), _resp("}")]
    await judge._complete("p")
    await judge._complete("p")

    assert len(minted) == 1


# --- failure handling -------------------------------------------------


async def test_expired_token_is_re_minted_once_without_spending_backoff(no_sleep):
    tokens = iter(["tok-1", "tok-2"])
    judge = FloodgateJudge(
        FloodgateOIDCSettings(), _cfg(), token_provider=lambda: next(tokens)
    )
    judge._probed = True
    _stub().script = [_StubStatusError(401), _resp("}")]

    await judge._complete("p")

    assert _stub().calls[1]["extra_headers"]["Authorization"] == "Bearer tok-2"
    assert no_sleep == []  # a token refresh is not a backoff


async def test_a_second_401_is_not_retried_forever():
    judge = FloodgateJudge(
        FloodgateOIDCSettings(), _cfg(), token_provider=lambda: "tok"
    )
    _stub().script = [_StubStatusError(401), _StubStatusError(401)]

    with pytest.raises(RuntimeError, match="onboard"):
        await judge._complete("p")


async def test_rate_limits_and_provider_capacity_are_retried(no_sleep):
    judge = _judge([_StubStatusError(429), _StubStatusError(529), _resp("}")])

    assert await judge._complete("p") == "{}"
    assert len(no_sleep) == 2


async def test_backoff_carries_jitter_and_honours_retry_after(no_sleep):
    judge = _judge(
        [_StubStatusError(429, retry_after=4), _resp("}")],
        # backoff_max_s must not clamp below the server's ask for this assertion
        config=_cfg(backoff_max_s=30.0),
    )
    await judge._complete("p")

    # Jittered into [0.5, 1.0] of the 4s the server asked for.
    assert 2.0 <= no_sleep[0] <= 4.0


async def test_a_content_filter_rejection_is_not_retried(no_sleep):
    judge = _judge([_StubStatusError(451), _resp("}")])

    with pytest.raises(RuntimeError, match="Critical code"):
        await judge._complete("p")
    assert no_sleep == []


async def test_truncation_is_reported_as_such_not_as_bad_json():
    # Retrying malformed output against a too-small budget just burns quota.
    judge = _judge([_resp('"factual_correctness": ', stop_reason="max_tokens")])

    with pytest.raises(RuntimeError, match="truncated at max_tokens"):
        await judge._complete("p")


async def test_an_empty_response_fails_loudly():
    empty = types.SimpleNamespace(content=[], stop_reason="end_turn")
    judge = _judge([empty])

    with pytest.raises(JudgeOutputError, match="no text content"):
        await judge._complete("p")


async def test_aclose_closes_the_sdk_client():
    judge = _judge()
    await judge.aclose()
    assert _stub().closed


def test_explain_reads_status_codes_the_floodgate_way():
    assert "provider capacity" in explain_floodgate_error(Exception("x"), 529)
    assert "FLOODGATE_PROJECT_TOKEN" in explain_floodgate_error(Exception("x"), 429)
    assert (
        explain_floodgate_error(Exception("x"), 418)
        == "floodgate judge call failed (418): x"
    )


# --- audit trail ------------------------------------------------------


async def test_malformed_output_is_retried_and_every_attempt_is_audited(tmp_path):
    import json

    log_path = tmp_path / "prompts.jsonl"
    judge = FloodgateJudge(
        FloodgateOIDCSettings(),
        _cfg(malformed_output_retries=1),
        prompt_log_path=log_path,
        judge_run_id="judge-run-floodgate-001",
        token_provider=lambda: "tok",
    )
    judge._probed = True
    _stub().script = [_resp('"bad": true}'), _resp(GOOD[1:])]

    verdict = await judge._judge_combined(REQ)

    assert verdict.factual_correctness == 5
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(records) == 2
    assert records[0]["parse_error"]
    assert records[1]["parse_error"] is None
    assert records[1]["model_version"] == "floodgate/anthropic.claude-sonnet-5"


# --- provider detection from the environment --------------------------


@pytest.fixture
def clean_env(monkeypatch):
    """Isolate from judge/.env, which load_env() reads with override=True."""
    monkeypatch.setattr("judge.config.load_env", lambda env_file=None: None)
    for name in (
        "LLM_PROVIDER",
        "AZURE_OPENAI_ENDPOINT",
        "OPENAI_API_KEY",
        "FLOODGATE_MODEL",
        "FLOODGATE_BASE_URL",
        "FLOODGATE_USER_AGENT",
        "FLOODGATE_PROJECT_TOKEN",
        "FLOODGATE_NARRATIVE_CERT",
        "FLOODGATE_NARRATIVE_KEY",
        "FLOODGATE_APPLECONNECT",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_a_narrative_certificate_selects_floodgate_without_forcing_it(clean_env):
    from judge.config import load_llm_settings

    clean_env.setenv("FLOODGATE_NARRATIVE_CERT", "/tls/tls.crt")
    clean_env.setenv("FLOODGATE_NARRATIVE_KEY", "/tls/tls.key")
    clean_env.setenv("FLOODGATE_MODEL", "anthropic.claude-haiku-4-5-20251001-v1:0")

    settings = load_llm_settings()

    assert isinstance(settings, FloodgateNarrativeSettings)
    assert settings.model == "anthropic.claude-haiku-4-5-20251001-v1:0"


def test_half_a_certificate_pair_fails_with_both_variable_names(clean_env):
    from judge.config import load_llm_settings

    clean_env.setenv("LLM_PROVIDER", "floodgate")
    clean_env.setenv("FLOODGATE_NARRATIVE_CERT", "/tls/tls.crt")

    with pytest.raises(MissingCredentials, match="FLOODGATE_NARRATIVE_KEY"):
        load_llm_settings()


def test_oidc_without_the_appleconnect_cli_says_what_to_do_instead(clean_env):
    from judge.config import load_llm_settings

    clean_env.setenv("LLM_PROVIDER", "floodgate")
    clean_env.setenv("FLOODGATE_APPLECONNECT", "/nonexistent/appleconnect")

    with pytest.raises(MissingCredentials, match="FLOODGATE_NARRATIVE_CERT"):
        load_llm_settings()


def test_azure_still_wins_when_no_floodgate_credential_is_present(clean_env):
    from judge.config import AzureSettings, load_llm_settings

    clean_env.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    clean_env.setenv("AZURE_OPENAI_DEPLOYMENT", "dep")
    clean_env.setenv("AZURE_OPENAI_API_VERSION", "2024-06-01")
    clean_env.setenv("AZURE_OPENAI_API_KEY", "k")

    assert isinstance(load_llm_settings(), AzureSettings)


def test_floodgate_model_takes_precedence_over_the_default_config(clean_env):
    clean_env.setenv("FLOODGATE_MODEL", "anthropic.claude-opus-5")
    assert load_judge_config("crm").model == "anthropic.claude-opus-5"


# --- token minting ----------------------------------------------------


def test_missing_appleconnect_points_at_the_server_side_alternative():
    with pytest.raises(MissingCredentials, match="FLOODGATE_NARRATIVE_CERT"):
        appleconnect_token("/nonexistent/appleconnect")


def test_token_is_read_from_the_id_token_line(monkeypatch):
    def fake_run(argv, **kwargs):
        assert "hvys3fcwcteqrvw3qzkvtk86viuoqv" in argv
        return types.SimpleNamespace(
            returncode=0,
            stdout="access-token abc\noauth-id-token eyJ-the-one\n",
            stderr="",
        )

    monkeypatch.setattr("judge.floodgate_judge.subprocess.run", fake_run)
    assert appleconnect_token() == "eyJ-the-one"


def test_a_failed_getToken_surfaces_the_cli_error(monkeypatch):
    monkeypatch.setattr(
        "judge.floodgate_judge.subprocess.run",
        lambda argv, **kw: types.SimpleNamespace(
            returncode=1, stdout="", stderr="not signed in"
        ),
    )
    with pytest.raises(MissingCredentials, match="not signed in"):
        appleconnect_token()
