"""judge package tests. No network, no credentials."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError

from judge.cache import JudgeCache, cache_key
from judge.calibration import (
    ACCEPTANCE_AGREEMENT_PCT,
    ACCEPTANCE_MIN_ANCHORS,
    _is_directional_flip,
    evaluate,
    is_calibrated,
    record_passed,
)
from judge.config import AzureSettings, OpenAISettings, load_judge_config
from judge.contracts import JudgeRequest, JudgeVerdict
from judge.mock_judge import MockJudge
from judge.exact_match import ExactMatchResult, exact_match, extract_numerics
from judge.mock_pulse import MockPulse, load_pairs
from judge.openai_judge import OpenAIJudge
from judge.parsing import JudgeOutputError, parse_combined, parse_dimension
from judge.prompts import prompt_version, render_combined, render_dimension
from judge.pulse_client import PulseClient

REQ = JudgeRequest(
    question="How many active accounts do we have?",
    expected_answer="72",
    judge_reference="There are 72 active accounts.",
    platform_answer="There are 72 active accounts in the CRM.",
    generated_sql="SELECT COUNT(*) FROM accounts WHERE is_active = true",
    question_id="crm-t1-001",
)

GOOD = (
    '{"factual_correctness": 5, "completeness": 4, '
    '"format_adherence": 5, "sql_plausibility": 5, "rationales": {'
    '"factual_correctness": "Factually correct.", '
    '"completeness": "One minor detail is absent.", '
    '"format_adherence": "Correct format.", '
    '"sql_plausibility": "SQL is coherent."}}'
)


def _verdict(**scores: int) -> JudgeVerdict:
    base = dict(
        factual_correctness=5, completeness=5, format_adherence=5, sql_plausibility=5
    )
    base.update(scores)
    return JudgeVerdict(
        dimension_rationales={d: "ok" for d in base},
        prompt_version="p",
        model_version="m",
        **base,
    )


# --- normalization --------------------------------------------------------


def test_worst_possible_verdict_normalizes_to_zero():
    """(x-1)/4, not x/5. All-1s must be 0.0, never 0.2."""
    v = _verdict(
        factual_correctness=1, completeness=1, format_adherence=1, sql_plausibility=1
    )
    assert v.overall_score == 1.0
    assert v.normalized == 0.0


def test_best_and_midpoint():
    assert _verdict().normalized == 1.0
    mid = _verdict(
        factual_correctness=3, completeness=3, format_adherence=3, sql_plausibility=3
    )
    assert mid.normalized == 0.5


@pytest.mark.parametrize("bad", [0, 6, -1])
def test_out_of_range_scores_rejected(bad):
    with pytest.raises(Exception):
        _verdict(factual_correctness=bad)


# --- parsing --------------------------------------------------------------


def test_parse_combined_accepts_strict_json_only():
    assert parse_combined(GOOD)["completeness"] == 4
    with pytest.raises(JudgeOutputError, match="malformed JSON"):
        parse_combined("Sure:\n```json\n" + GOOD + "\n```")


def test_out_of_range_is_rejected_not_clamped():
    with pytest.raises(JudgeOutputError, match="outside the valid range"):
        parse_combined(
            GOOD.replace('"factual_correctness": 5', '"factual_correctness": 7')
        )


def test_missing_dimension_and_rationale_rejected():
    with pytest.raises(JudgeOutputError, match="structured-output schema"):
        parse_combined(GOOD.replace('"sql_plausibility": 5', '"other": 5'))
    with pytest.raises(JudgeOutputError, match="rationales"):
        parse_combined(GOOD.replace(', "rationales": {', ', "missing_rationales": {'))


@pytest.mark.parametrize(
    "replacement",
    ['"factual_correctness": "5"', '"factual_correctness": 5.0'],
)
def test_score_type_is_rejected_not_coerced(replacement):
    with pytest.raises(JudgeOutputError, match="expected an integer"):
        parse_combined(GOOD.replace('"factual_correctness": 5', replacement, 1))


def test_parse_dimension():
    assert parse_dimension(
        '{"rationale": "ok", "completeness": 3}', "completeness"
    ) == (3, "ok")


# --- prompts --------------------------------------------------------------


def test_templates_load_from_disk():
    prompt = render_combined(REQ)
    assert REQ.judge_reference in prompt
    assert REQ.expected_answer in prompt
    assert "json" in prompt.lower()  # required by response_format=json_object
    assert '"rationales"' in prompt
    assert "REFERENCE SQL" not in prompt
    assert "reference_sql" not in JudgeRequest.model_fields
    assert REQ.generated_sql in prompt


def test_judge_request_rejects_reference_sql_leakage():
    with pytest.raises(Exception, match="reference_sql"):
        JudgeRequest(**REQ.model_dump(), reference_sql="SELECT secret_ground_truth")


def test_per_dimension_prompt_is_scoped():
    assert "DIMENSION UNDER EVALUATION: Completeness" in render_dimension(
        REQ, "completeness"
    )


def test_prompt_version_stable():
    assert prompt_version() == prompt_version()
    assert prompt_version().startswith("judge-")


# --- config ---------------------------------------------------------------


def test_domain_config_overrides_default():
    crm = load_judge_config("crm")
    assert crm.mode == "combined"  # crm.json override
    assert crm.temperature == 0.0  # inherited from default
    assert crm.max_tokens == 1024  # inherited from default
    assert crm.model == "gpt-4o-mini"
    assert crm.seed == 42


@pytest.mark.parametrize(
    "domain", ["crm", "sales", "finance", "project_management", "logistics"]
)
def test_every_domain_has_complete_judge_config(domain):
    cfg = load_judge_config(domain)
    assert cfg.model
    assert cfg.temperature == 0.0
    assert cfg.seed is not None
    assert cfg.max_tokens > 0
    assert cfg.max_retries >= 0
    assert cfg.concurrency >= 1


class _StubOpenAIJudge(OpenAIJudge):
    def __init__(self, responses: list[str], log_path, cache_path=None):
        self._responses = iter(responses)
        self.calls = 0
        self._config = load_judge_config("crm").model_copy(
            update={
                "malformed_output_retries": 1,
                "cache_enabled": cache_path is not None,
            }
        )
        self._model_str = "stub-model"
        self._judge_run_id = "judge-run-stable-001"
        self._prompt_log_path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        self._cache = JudgeCache(
            cache_path or log_path.parent / "disabled", enabled=cache_path is not None
        )

    async def _complete(self, prompt: str) -> str:
        self.calls += 1
        return next(self._responses)


async def test_malformed_output_is_retried_and_every_attempt_is_audited(tmp_path):
    log_path = tmp_path / "prompts.jsonl"
    judge = _StubOpenAIJudge(['{"bad": true}', GOOD], log_path)
    verdict = await judge._judge_combined(REQ)

    assert judge.calls == 2
    assert verdict.factual_correctness == 5
    assert len(verdict.audit_trace) == 2
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(records) == 2
    assert all(record["judge_run_id"] == "judge-run-stable-001" for record in records)
    assert records[0]["parse_error"]
    assert records[1]["parse_error"] is None
    assert records[1]["raw_response"] == GOOD


async def test_malformed_output_fails_loudly_after_retry_limit(tmp_path):
    judge = _StubOpenAIJudge(['{"bad": 1}', '{"still_bad": 2}'], tmp_path / "log.jsonl")
    with pytest.raises(JudgeOutputError, match="after 2 attempt"):
        await judge._judge_combined(REQ)


async def test_cache_hit_replays_full_trace_with_current_run_id(tmp_path):
    log_path = tmp_path / "prompts.jsonl"
    judge = _StubOpenAIJudge([GOOD], log_path, cache_path=tmp_path / "cache")
    fresh = await judge.judge(REQ)
    cached = await judge.judge(REQ)

    assert not fresh.cached
    assert cached.cached
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(records) == 2
    assert records[0]["cached"] is False
    assert records[1]["cached"] is True
    assert records[1]["raw_response"] == GOOD
    assert records[1]["judge_run_id"] == "judge-run-stable-001"


async def test_transient_timeout_uses_bounded_retry_policy():
    class Completions:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise APITimeoutError(
                    request=httpx.Request("POST", "https://example.test")
                )
            return "ok"

    completions = Completions()
    judge = OpenAIJudge.__new__(OpenAIJudge)
    judge._config = load_judge_config("crm").model_copy(
        update={"max_retries": 2, "backoff_base_s": 0.001, "backoff_max_s": 0.001}
    )
    judge._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    assert await judge._create_with_backoff({"model": "stub"}) == "ok"
    assert completions.calls == 3


def test_request_includes_zero_temperature_and_fixed_seed():
    judge = OpenAIJudge.__new__(OpenAIJudge)
    judge._config = load_judge_config("crm")
    judge._api_model = judge._config.model
    judge._max_tokens_param = "max_tokens"
    judge._send_seed = True
    judge._send_temperature = True
    judge._json_mode = True

    kwargs = judge._build_kwargs("score this")
    assert kwargs["temperature"] == 0.0
    assert kwargs["seed"] == 42


def _temp_rejection() -> object:
    from openai import APIStatusError

    resp = httpx.Response(
        400,
        request=httpx.Request("POST", "https://gw.test"),
        json={"error": {"message": "temperature does not support 0"}},
    )
    return APIStatusError(
        "temperature unsupported",
        response=resp,
        body={"error": {"message": "'temperature' is not supported"}},
    )


def _stub_judge(*, require_temp0: bool) -> OpenAIJudge:
    judge = OpenAIJudge.__new__(OpenAIJudge)
    judge._config = load_judge_config("crm").model_copy(
        update={"max_retries": 0, "backoff_base_s": 0.001, "backoff_max_s": 0.001}
    )
    judge._json_mode = False
    judge._api_model = judge._config.model
    judge._model_str = judge._config.model
    judge._max_tokens_param = "max_tokens"
    judge._send_seed = True
    judge._send_temperature = True
    judge._require_temp0 = require_temp0
    judge.temperature_enforced = True
    return judge


async def test_temperature_zero_rejection_fails_loudly_by_default():
    judge = _stub_judge(require_temp0=True)

    async def create(**kwargs):
        raise _temp_rejection()

    judge._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    with pytest.raises(RuntimeError, match="temperature=0"):
        await judge._complete_unbounded("prompt")


async def test_temperature_zero_rejection_degrades_when_allowed():
    judge = _stub_judge(require_temp0=False)
    calls = {"n": 0}

    async def create(**kwargs):
        calls["n"] += 1
        if "temperature" in kwargs:
            raise _temp_rejection()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
        )

    judge._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    out = await judge._complete_unbounded("prompt")
    assert out == "{}"
    assert judge.temperature_enforced is False
    assert calls["n"] == 2  # first with temperature (rejected), retry without


# --- cache ----------------------------------------------------------------


def test_cache_roundtrip_marks_served_entries(tmp_path):
    cache = JudgeCache(tmp_path, enabled=True)
    key = cache_key(REQ, prompt_version="p1", model_version="m1", mode="combined")
    assert cache.get(key) is None

    cache.put(key, _verdict())
    hit = cache.get(key)
    assert hit is not None
    assert hit.cached is True
    assert cache.hits == 1


def test_cache_key_changes_with_mode():
    a = cache_key(REQ, prompt_version="p", model_version="m", mode="combined")
    b = cache_key(REQ, prompt_version="p", model_version="m", mode="per_dimension")
    assert a != b


def test_cache_key_changes_with_prompt_and_model_version():
    base = cache_key(REQ, prompt_version="p1", model_version="m1", mode="combined")
    assert base != cache_key(
        REQ, prompt_version="p2", model_version="m1", mode="combined"
    )
    assert base != cache_key(
        REQ, prompt_version="p1", model_version="m2", mode="combined"
    )


def test_corrupt_cache_entry_is_a_miss_not_a_crash(tmp_path):
    cache = JudgeCache(tmp_path, enabled=True)
    key = cache_key(REQ, prompt_version="p", model_version="m", mode="combined")
    cache.put(key, _verdict())
    path = next(tmp_path.rglob("*.json"))
    path.write_text("{ not json", encoding="utf-8")
    assert cache.get(key) is None


# --- mock judge -----------------------------------------------------------


async def test_mock_judge_is_deterministic():
    """CI gate — a gate that varies is not a gate."""
    a = await MockJudge().judge(REQ)
    b = await MockJudge().judge(REQ)
    assert a.model_dump() == b.model_dump()


async def test_missing_sql_scores_plausibility_one():
    verdict = await MockJudge().judge(REQ.model_copy(update={"generated_sql": None}))
    assert verdict.sql_plausibility == 1


async def test_judge_many_returns_exceptions_per_row():
    class Boom(MockJudge):
        async def judge(self, req):
            raise RuntimeError("simulated failure")

    results = await Boom().judge_many([REQ, REQ], concurrency=2)
    assert len(results) == 2
    assert all(isinstance(r, Exception) for r in results)


# --- mock pulse -----------------------------------------------------------


def test_mock_pulse_loads_good_and_regressed_fixtures():
    good = MockPulse(mode="good")
    regressed = MockPulse(mode="regressed")
    ids = load_pairs()
    for pair in ids:
        g = good.query(pair["question_id"])
        r = regressed.query(pair["question_id"])
        assert g.question_id == pair["question_id"]
        assert r.question_id == pair["question_id"]


def test_mock_pulse_missing_fixture_raises():
    pulse = MockPulse(mode="good")
    with pytest.raises(KeyError, match="no fixture"):
        pulse.query("crm-does-not-exist")


# --- exact-match (§HC-3 zero tolerance) -----------------------------------


def test_exact_match_pass_on_identical_numeric():
    assert exact_match("72", "There are 72 active accounts.") == ExactMatchResult.PASS


def test_exact_match_fail_on_wrong_numeric():
    assert exact_match("72", "There are 68 active accounts.") == ExactMatchResult.FAIL


def test_exact_match_zero_tolerance_on_decimal_and_thousands():
    # §HC-3 is byte-identical: '72' vs '72.0' fails; '1000' vs '1,000' fails.
    assert exact_match("72", "72.0") == ExactMatchResult.FAIL
    assert exact_match("1000", "1,000 rows") == ExactMatchResult.FAIL


def test_exact_match_multi_value():
    expected = "Technology 8,240,100.00; Finance 6,102,750.00; Healthcare 4,988,300.00"
    actual = (
        "H1 total by industry: Technology 8,240,100.00, Finance 6,102,750.00, "
        "Healthcare 4,988,300.00 lead."
    )
    assert exact_match(expected, actual) == ExactMatchResult.PASS


def test_exact_match_multi_value_one_missing_is_fail():
    expected = "Technology 8,240,100.00; Finance 6,102,750.00; Healthcare 4,988,300.00"
    actual = "Technology 8,240,100.00; Finance 6,102,750.00"
    assert exact_match(expected, actual) == ExactMatchResult.FAIL


def test_exact_match_not_applicable_when_no_numeric():
    # T5 reasoning wrappers with no numeric core — judge-only pair.
    assert (
        exact_match("The pipeline is healthier than six months ago.", "...")
        == ExactMatchResult.NOT_APPLICABLE
    )


def test_extract_numerics_preserves_formatting():
    assert extract_numerics("Priya — 4,182,650.00 in 2026") == ["4,182,650.00", "2026"]


def test_numeric_normalization_relaxes_oi2_cases():
    from judge.exact_match import NumericNormalization

    norm = NumericNormalization()
    # the cases the live QA run failed on
    assert (
        exact_match("438632.65", "$438,632.65 (USD)", normalize=norm)
        == ExactMatchResult.PASS
    )
    assert exact_match("72", "72.0", normalize=norm) == ExactMatchResult.PASS
    assert (
        exact_match("7735084.39", "$7,735,084.39", normalize=norm)
        == ExactMatchResult.PASS
    )
    # still catches a genuinely wrong number
    assert exact_match("34", "25 contacts", normalize=norm) == ExactMatchResult.FAIL
    # default (normalize=None) stays strict
    assert exact_match("438632.65", "438,632.65") == ExactMatchResult.FAIL


def test_numeric_normalization_can_disable_each_relaxation():
    from judge.exact_match import NumericNormalization

    only_sep = NumericNormalization(trailing_decimal_zeros=False)
    assert exact_match("1000", "1,000", normalize=only_sep) == ExactMatchResult.PASS
    assert exact_match("72", "72.0", normalize=only_sep) == ExactMatchResult.FAIL
    assert only_sep.label == "thousands-sep"


# --- pulse_client — full behaviour lives in test_pulse_client.py ----------


def test_pulse_client_still_exported():
    """Full live-client behaviour is covered in test_pulse_client.py."""
    assert PulseClient.__name__ == "PulseClient"


# --- Azure/OpenAI settings ------------------------------------------------


def test_openai_and_azure_settings_have_uniform_model_accessor():
    """model_version in the scorecard must be uniform across providers."""
    o = OpenAISettings(api_key="sk-x", model="gpt-4o-mini")
    a = AzureSettings(
        endpoint="https://x.openai.azure.com",
        api_key="k",
        api_version="2024-06-01",
        deployment="my-deploy",
    )
    assert o.model == "gpt-4o-mini"
    assert a.model == "azure/my-deploy"
    # redacted() must never leak the key or endpoint tail
    for redacted in (o.redacted, a.redacted):
        for v in redacted.values():
            if v is not None:
                assert "k" not in str(v) or "gpt" in str(v) or "openai" in str(v)


def test_openai_judge_preserves_environment_selected_model(tmp_path):
    """OPENAI_MODEL/LLM_MODEL must override the per-domain config default."""
    settings = OpenAISettings(api_key="sk-x", model="gateway-deployment")
    judge = OpenAIJudge(
        settings,
        load_judge_config("crm"),
        cache_dir=tmp_path / "cache",
    )
    assert judge._api_model == "gateway-deployment"
    assert judge._model_str == "gateway-deployment"


# --- prompt template — worked negative examples (§10.2) -------------------


def test_prompt_carries_non_determinism_boundary_examples():
    """§10.2 explicitly requires worked negative examples in the prompt."""
    prompt = render_combined(REQ)
    assert "NON-DETERMINISM BOUNDARY" in prompt
    assert "DO NOT DEDUCT" in prompt
    assert "DO DEDUCT" in prompt
    # Both a positive-not-deducted example and a negative-deducted example must
    # appear so the model sees the shape of both edges.
    assert "There are 72 active accounts in the CRM." in prompt  # DO-NOT-DEDUCT example
    assert "There are 68 active accounts." in prompt  # DO-DEDUCT example


def test_per_dimension_prompt_also_carries_boundary_examples():
    prompt = render_dimension(REQ, "factual_correctness")
    assert "NON-DETERMINISM BOUNDARY" in prompt
    assert "DO NOT DEDUCT" in prompt


# --- calibration acceptance test (§10.3) ----------------------------------


def _judge_verdict(**scores):
    base = dict(
        factual_correctness=5, completeness=5, format_adherence=5, sql_plausibility=5
    )
    base.update(scores)
    return JudgeVerdict(
        dimension_rationales={d: "ok" for d in base},
        prompt_version="p",
        model_version="m",
        **base,
    )


def _make_anchors(n: int, human_scores: dict[str, int] | None = None) -> list[dict]:
    human = human_scores or {
        d: 5
        for d in (
            "factual_correctness",
            "completeness",
            "format_adherence",
            "sql_plausibility",
        )
    }
    return [
        {
            "question_id": f"anchor-{i:03d}",
            "natural_language_question": "?",
            "expected_answer": "?",
            "judge_reference": "?",
            "reference_sql": "SELECT 1",
            "platform_answer": "?",
            "generated_sql": "SELECT 1",
            "human_scores": human,
        }
        for i in range(n)
    ]


def test_calibration_requires_min_anchor_count():
    anchors = _make_anchors(ACCEPTANCE_MIN_ANCHORS - 1)
    verdicts = {a["question_id"]: _judge_verdict() for a in anchors}
    with pytest.raises(ValueError, match="≥"):
        evaluate("crm", verdicts, anchors)


def test_calibration_passes_when_all_within_pm1_no_flips():
    anchors = _make_anchors(ACCEPTANCE_MIN_ANCHORS)
    # human 5 across the board, judge 4 across the board → within ±1, no flips
    verdicts = {
        a["question_id"]: _judge_verdict(
            **{
                d: 4
                for d in (
                    "factual_correctness",
                    "completeness",
                    "format_adherence",
                    "sql_plausibility",
                )
            }
        )
        for a in anchors
    }
    result = evaluate("crm", verdicts, anchors)
    assert result.passed
    for d, r in result.per_dimension.items():
        assert r.within_pm1_pct == 100.0
        assert r.directional_flip_count == 0


def test_calibration_fails_on_directional_flip_even_at_100pct_within_pm1_after_partial_perfect():
    """A single 5→2 flip must sink the whole dimension per §10.3 (never directional)."""
    anchors = _make_anchors(ACCEPTANCE_MIN_ANCHORS)
    verdicts = {a["question_id"]: _judge_verdict() for a in anchors}
    # First anchor: judge scores factual as 2 (human was 5) → directional flip
    verdicts[anchors[0]["question_id"]] = _judge_verdict(factual_correctness=2)
    result = evaluate("crm", verdicts, anchors)
    assert not result.passed
    assert result.per_dimension["factual_correctness"].directional_flip_count == 1


def test_calibration_fails_below_90pct_within_pm1():
    anchors = _make_anchors(ACCEPTANCE_MIN_ANCHORS)
    verdicts = {a["question_id"]: _judge_verdict() for a in anchors}
    # 2 out of 10 are >1 apart on completeness (3 vs 5 = diff 2, not a flip)
    for a in anchors[:2]:
        verdicts[a["question_id"]] = _judge_verdict(completeness=3)
    result = evaluate("crm", verdicts, anchors)
    assert not result.passed
    assert result.per_dimension["completeness"].within_pm1_pct == 80.0
    assert (
        result.per_dimension["completeness"].within_pm1_pct < ACCEPTANCE_AGREEMENT_PCT
    )


def test_directional_flip_detection():
    assert _is_directional_flip(5, 2) is True
    assert _is_directional_flip(1, 5) is True
    assert _is_directional_flip(5, 4) is False  # both high side
    assert _is_directional_flip(5, 3) is False  # mid, not a flip
    assert _is_directional_flip(3, 5) is False  # mid, not a flip


def test_calibration_marker_gate(tmp_path):
    """is_calibrated returns True only after record_passed writes the marker."""
    assert is_calibrated("crm", calibration_dir=tmp_path) is False
    anchors = _make_anchors(ACCEPTANCE_MIN_ANCHORS)
    verdicts = {a["question_id"]: _judge_verdict() for a in anchors}
    result = evaluate("crm", verdicts, anchors)
    assert result.passed
    marker = record_passed("crm", result, calibration_dir=tmp_path)
    assert marker.is_file()
    assert is_calibrated("crm", calibration_dir=tmp_path) is True
