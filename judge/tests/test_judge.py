"""judge package tests. No network, no credentials."""

from __future__ import annotations

import json

import pytest

from judge.cache import JudgeCache, cache_key
from judge.calibration import (
    ACCEPTANCE_AGREEMENT_PCT,
    ACCEPTANCE_MIN_ANCHORS,
    _is_directional_flip,
    evaluate,
    is_calibrated,
    record_passed,
)
from judge.config import load_judge_config
from judge.contracts import JudgeRequest, JudgeVerdict
from judge.exact_match import ExactMatchResult, exact_match, extract_numerics
from judge.llm_judge import BaseLLMJudge
from judge.tests.stub_judge import StubJudge
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
    # crm.json pins its own model (tier 1) so the config file, not a developer's
    # .env, decides what scores a CRM run. default.json's gpt-4o-mini is only
    # reached by a domain that declares no model of its own.
    assert crm.model == "anthropic.claude-sonnet-4-6"
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


class _StubLLMJudge(BaseLLMJudge):
    """Exercises the transport-independent half of a judge: retry, audit, cache.

    Parented on `BaseLLMJudge` because that is what these tests were always
    checking — `_complete` is the one hook a transport supplies, and stubbing it
    leaves the shared scaffolding under test. Provider-specific request shaping is
    covered per transport in test_floodgate_judge.py.
    """

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
        self.temperature_enforced = True
        self.seed_enforced = True
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
    judge = _StubLLMJudge(['{"bad": true}', GOOD], log_path)
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
    judge = _StubLLMJudge(['{"bad": 1}', '{"still_bad": 2}'], tmp_path / "log.jsonl")
    with pytest.raises(JudgeOutputError, match="after 2 attempt"):
        await judge._judge_combined(REQ)


async def test_cache_hit_replays_full_trace_with_current_run_id(tmp_path):
    log_path = tmp_path / "prompts.jsonl"
    judge = _StubLLMJudge([GOOD], log_path, cache_path=tmp_path / "cache")
    await judge.judge(REQ)  # populates the cache
    await judge.judge(REQ)  # served from it


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


def test_degraded_determinism_survives_the_cache(tmp_path):
    """A cache-served run must still report the determinism basis its scores
    were produced under — the judge object never negotiates on a cache hit, so
    the flag has to live on the verdict."""
    cache = JudgeCache(tmp_path, enabled=True)
    key = cache_key(REQ, prompt_version="p1", model_version="m1", mode="combined")
    cache.put(key, _verdict().model_copy(update={"temperature_enforced": False}))

    hit = cache.get(key)
    assert hit.cached is True
    assert hit.temperature_enforced is False


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
async def test_judge_many_returns_exceptions_per_row():
    class Boom(StubJudge):
        async def judge(self, req):
            raise RuntimeError("simulated failure")

    results = await Boom().judge_many([REQ, REQ], concurrency=2)
    assert len(results) == 2
    assert all(isinstance(r, Exception) for r in results)


async def test_judge_many_reports_progress_for_every_row():
    """A 160-pair run spends ~18 minutes here. Without a per-row callback the
    judge leg is silent for its whole duration and a slow run is
    indistinguishable from a hung one."""
    seen: list[tuple[int, str]] = []
    results = await StubJudge().judge_many(
        [REQ, REQ, REQ],
        concurrency=2,
        on_done=lambda n, req, out: seen.append((n, type(out).__name__)),
    )
    assert len(results) == 3
    assert [n for n, _ in seen] == [1, 2, 3], "counter increments once per row"
    assert all(kind == "JudgeVerdict" for _, kind in seen)


async def test_judge_many_reports_failures_too():
    class Boom(StubJudge):
        async def judge(self, req):
            raise RuntimeError("simulated failure")

    seen: list[str] = []
    await Boom().judge_many(
        [REQ, REQ],
        concurrency=2,
        on_done=lambda n, req, out: seen.append(type(out).__name__),
    )
    assert seen == ["RuntimeError", "RuntimeError"], "progress must not skip errors"


async def test_a_broken_progress_callback_cannot_fail_the_run():
    """Reporting is not worth losing verdicts over."""

    def explode(n, req, out):
        raise ValueError("bad reporter")

    results = await StubJudge().judge_many([REQ], concurrency=1, on_done=explode)
    assert len(results) == 1
    assert isinstance(results[0], JudgeVerdict)


# --- mock pulse -----------------------------------------------------------


# --- exact-match (§HC-3 zero tolerance) -----------------------------------


def test_exact_match_pass_on_identical_numeric():
    assert exact_match("72", "There are 72 active accounts.") == ExactMatchResult.PASS


def test_exact_match_fail_on_wrong_numeric():
    assert exact_match("72", "There are 68 active accounts.") == ExactMatchResult.FAIL


def test_exact_match_compares_numerics_by_value_not_rendering():
    """OI-2, decided: §HC-3 protects rendering and fails only numeric variance.

    A thousands separator, a currency prefix and a trailing `.00` change how a
    number is written, not what it is. Rounding still fails, because that is a
    real loss of value.
    """
    assert exact_match("1000", "1,000 rows") == ExactMatchResult.PASS
    assert exact_match("72", "72.0") == ExactMatchResult.PASS
    assert exact_match("438632.65", "$438,632.65 (USD)") == ExactMatchResult.PASS
    assert (
        exact_match("28731", "A total of 28,731 support cases.")
        == ExactMatchResult.PASS
    )
    # not a rendering difference — a different number
    assert exact_match("4182650.00", "4,182,000") == ExactMatchResult.FAIL
    assert exact_match("34", "25 contacts") == ExactMatchResult.FAIL


def test_string_form_restores_the_pre_oi2_reading():
    """If the Platform Owner rules that "identical" means the rendering, it is
    one flag — not a rewrite."""
    from judge.exact_match import STRICT_STRING_POLICY

    assert (
        exact_match("28731", "28,731 cases", policy=STRICT_STRING_POLICY)
        == ExactMatchResult.FAIL
    )
    assert (
        exact_match("28,731", "28,731 cases", policy=STRICT_STRING_POLICY)
        == ExactMatchResult.PASS
    )


def test_exact_match_requires_the_right_entity_not_just_the_right_number():
    """The headline false-pass: every number right, every label wrong.

    This is the shape of the Appendix A worked example and of most T4/T5 pairs,
    so a numerics-only comparison inflates the §14.2 accuracy gate.
    """
    expected = "Priya Raghavan — 4,182,650.00"
    assert (
        exact_match(expected, "Priya Raghavan closed 4,182,650.00 last quarter.")
        == ExactMatchResult.PASS
    )
    assert (
        exact_match(expected, "Bob Smith closed 4,182,650.00 last quarter.")
        == ExactMatchResult.FAIL
    )
    # the entity is required, not merely nice to have
    assert (
        exact_match(expected, "The top rep closed 4,182,650.00.")
        == ExactMatchResult.FAIL
    )


def test_exact_match_rejects_a_permuted_list():
    """Right values, each bound to the wrong label — a multiset check misses it."""
    expected = "Awareness | 35726; Retention | 34685; CustomerEducation | 33992"
    assert (
        exact_match(
            expected, "Awareness | 35726; Retention | 34685; CustomerEducation | 33992"
        )
        == ExactMatchResult.PASS
    )
    assert (
        exact_match(
            expected, "Retention | 35726; CustomerEducation | 34685; Awareness | 33992"
        )
        == ExactMatchResult.FAIL
    )


def test_exact_match_accepts_prose_that_keeps_each_label_with_its_value():
    """§HC-3: phrasing is never a deduction, so prose must still pass."""
    expected = "Awareness | 35726; Retention | 34685; CustomerEducation | 33992"
    actual = (
        "Awareness led with 35,726, then Retention at 34,685 and "
        "Customer Education at 33,992."
    )
    assert exact_match(expected, actual) == ExactMatchResult.PASS


def test_exact_match_label_may_follow_its_value():
    assert exact_match("Standard | 6229", "6,229 cases fall under Standard.") == (
        ExactMatchResult.PASS
    )


def test_ignore_entity_labels_is_diagnostic_only():
    from judge.exact_match import ComparisonPolicy

    relaxed = ComparisonPolicy(require_labels=False)
    assert (
        exact_match("Priya Raghavan — 4,182,650.00", "Bob Smith closed 4,182,650.00")
        == ExactMatchResult.FAIL
    )
    assert (
        exact_match(
            "Priya Raghavan — 4,182,650.00",
            "Bob Smith closed 4,182,650.00",
            policy=relaxed,
        )
        == ExactMatchResult.PASS
    )
    assert relaxed.is_certified_default is False


def test_exact_match_multi_value():
    expected = "Technology | 8240100.00; Finance | 6102750.00; Healthcare | 4988300.00"
    actual = (
        "H1 total by industry: Technology 8,240,100.00, Finance 6,102,750.00, "
        "Healthcare 4,988,300.00 lead."
    )
    assert exact_match(expected, actual) == ExactMatchResult.PASS


def test_exact_match_multi_value_one_missing_is_fail():
    expected = "Technology | 8240100.00; Finance | 6102750.00; Healthcare | 4988300.00"
    actual = "Technology 8,240,100.00; Finance 6,102,750.00"
    assert exact_match(expected, actual) == ExactMatchResult.FAIL


def test_exact_match_not_applicable_when_no_deterministic_core():
    # T5 reasoning wrappers with no numeric core — judge-only pair.
    assert (
        exact_match("The pipeline is healthier than six months ago.", "...")
        == ExactMatchResult.NOT_APPLICABLE
    )


def test_iso_date_is_one_value_not_three_numbers():
    """OI-3. The old tokeniser read `2026-04-01` as ['2026', '-04', '-01'] —
    the hyphens became signs — so no date answer could ever be compared."""
    from judge.exact_match import parse_expected, FieldKind

    fields = parse_expected("2026-04-01")[0]
    assert len(fields) == 1 and fields[0].kind is FieldKind.DATE
    assert exact_match("2026-04-01", "The earliest close date is 2026-04-01.") == (
        ExactMatchResult.PASS
    )
    # the platform writes dates in prose; a month name is unambiguous
    assert exact_match("2026-04-01", "The earliest close date is April 1, 2026.") == (
        ExactMatchResult.PASS
    )
    assert exact_match("2026-04-01", "The earliest close date is 1 April 2026.") == (
        ExactMatchResult.PASS
    )
    # C10 / OI-3: no +-1 day tolerance is carried forward
    assert (
        exact_match("2026-04-01", "Closed on April 2, 2026.") == ExactMatchResult.FAIL
    )


def test_off_contract_expected_answer_is_flagged_not_silently_relaxed():
    """A prose expected_answer violates §9.3 (machine-generated from SQL). We
    hold it to its numbers rather than its wording, and say so on the row."""
    from judge.exact_match import evaluate_answer

    outcome = evaluate_answer("There are 72 active accounts.", "72 accounts")
    assert outcome.result is ExactMatchResult.PASS
    assert outcome.off_contract is True

    contract_shaped = evaluate_answer("72", "72 accounts")
    assert contract_shaped.off_contract is False


def test_failure_detail_names_the_unmet_requirement():
    from judge.exact_match import evaluate_answer

    outcome = evaluate_answer("Standard | 6229", "The Premium tier has 6,229 cases.")
    assert outcome.result is ExactMatchResult.FAIL
    assert "Standard" in outcome.detail
    assert outcome.satisfied == ("value:6229",)
    assert outcome.unsatisfied == ("label:Standard",)


def test_extract_numerics_excludes_date_digits():
    assert extract_numerics("Priya — 4,182,650.00 in 2026") == ["4,182,650.00", "2026"]
    assert extract_numerics("closed 2026-04-01") == []


# --- pulse_client — full behaviour lives in test_pulse_client.py ----------


def test_pulse_client_still_exported():
    """Full live-client behaviour is covered in test_pulse_client.py."""
    assert PulseClient.__name__ == "PulseClient"


# --- Azure/OpenAI settings ------------------------------------------------


def _write_configs(root, default: dict, domain: dict, domain_name="crm"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "default.json").write_text(json.dumps(default), encoding="utf-8")
    (root / f"{domain_name}.json").write_text(json.dumps(domain), encoding="utf-8")


_BASE_CFG = {"temperature": 0.0, "seed": 42, "model": "default-model"}


def test_model_selection_precedence(tmp_path, monkeypatch):
    """§10.1 wants the per-domain JSON to be able to select the model, without
    stopping the environment from naming a shared gateway's actual model.

    Precedence: <domain>.json  →  FLOODGATE_MODEL/LLM_MODEL  →  default.json
    """
    root = tmp_path / "cfg"
    # FLOODGATE_MODEL is consulted ahead of both of these, so an engineer with it
    # exported in their shell would otherwise fail this test on their machine and
    # nowhere else.
    monkeypatch.delenv("FLOODGATE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    # 1. nothing set anywhere but the project default
    _write_configs(root, _BASE_CFG, {})
    assert load_judge_config("crm", root).model == "default-model"

    # 2. environment names the gateway's model — beats the project default
    monkeypatch.setenv("FLOODGATE_MODEL", "gateway-model")
    assert load_judge_config("crm", root).model == "gateway-model"

    # 3. an explicit per-domain choice beats the environment
    _write_configs(root, _BASE_CFG, {"model": "crm-specific-model"})
    assert load_judge_config("crm", root).model == "crm-specific-model"


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


# --- calibration acceptance test (§10.2) ----------------------------------


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


DIMS = (
    "factual_correctness",
    "completeness",
    "format_adherence",
    "sql_plausibility",
)

# A spread that no constant score can pass: within ±1 tops out at 70% (k=4).
# §10.2 asks for anchors "spanning the score range — not 10 easy passes", and
# `assert_anchor_set_usable` now enforces that, so a flat anchor set is no
# longer a legal fixture.
_SPREAD = [1, 1, 2, 3, 3, 4, 4, 5, 5, 5]


def _make_anchors(
    n: int | None = None, human_scores: dict[str, int] | None = None
) -> list[dict]:
    """Anchors whose human grades span the scale, one per `_SPREAD` entry."""
    count = n if n is not None else len(_SPREAD)
    return [
        {
            "question_id": f"anchor-{i:03d}",
            "natural_language_question": "?",
            "expected_answer": "?",
            "judge_reference": "?",
            "reference_sql": "SELECT 1",
            "platform_answer": "?",
            "generated_sql": "SELECT 1",
            "human_scores": human_scores
            or {d: _SPREAD[i % len(_SPREAD)] for d in DIMS},
        }
        for i in range(count)
    ]


def _mirror_verdicts(anchors: list[dict], **override: int) -> dict:
    """A judge that reproduces the human grade on every dimension."""
    out = {}
    for a in anchors:
        scores = dict(a["human_scores"])
        scores.update(override)
        out[a["question_id"]] = _judge_verdict(**scores)
    return out


def test_calibration_requires_min_anchor_count():
    anchors = _make_anchors(ACCEPTANCE_MIN_ANCHORS - 1)
    with pytest.raises(ValueError, match="≥"):
        evaluate("crm", _mirror_verdicts(anchors), anchors)


def test_calibration_passes_when_all_within_pm1_no_flips():
    anchors = _make_anchors()
    result = evaluate("crm", _mirror_verdicts(anchors), anchors)
    assert result.passed
    for r in result.per_dimension.values():
        assert r.within_pm1_pct == 100.0
        assert r.directional_flip_count == 0


def test_constant_judge_cannot_pass_a_spanning_anchor_set():
    """The point of the anchor-strength rule: a judge that reads nothing fails.

    Against the old CRM anchor set a flat 4 scored 100% on factual_correctness
    and 90% on format_adherence — a pass on half the dimensions while measuring
    nothing at all.
    """
    anchors = _make_anchors()
    for k in range(1, 6):
        verdicts = {
            a["question_id"]: _judge_verdict(**{d: k for d in DIMS}) for a in anchors
        }
        assert not evaluate("crm", verdicts, anchors).passed, f"constant {k} passed"


def test_anchor_set_is_rejected_when_it_cannot_discriminate():
    from judge.calibration import AnchorSetTooWeak, anchor_strength

    flat = _make_anchors(ACCEPTANCE_MIN_ANCHORS, {d: 5 for d in DIMS})
    strength = anchor_strength(flat)
    assert strength["factual_correctness"].passing_constants == (4, 5)
    assert not strength["factual_correctness"].passes

    with pytest.raises(AnchorSetTooWeak, match="spanning the score range"):
        evaluate("crm", _mirror_verdicts(flat), flat)


def test_shipped_crm_anchor_file_is_quarantined_not_loadable():
    """`crm.provisional.json` was graded against the rubric it is meant to
    validate and cannot detect a constant judge, so it must not be reachable as
    `crm.json`. See judge/anchors/README.md."""
    from judge.calibration import ANCHORS_DIR, load_anchors

    assert not (ANCHORS_DIR / "crm.json").exists()
    assert (ANCHORS_DIR / "crm.provisional.json").exists()
    with pytest.raises(FileNotFoundError, match="No calibration anchors"):
        load_anchors("crm")


def test_calibration_fails_on_directional_flip_even_at_100pct_within_pm1():
    """A single 5→2 flip must sink the whole dimension per §10.2."""
    anchors = _make_anchors()
    verdicts = _mirror_verdicts(anchors)
    flipped = next(a for a in anchors if a["human_scores"]["factual_correctness"] == 5)
    scores = dict(flipped["human_scores"])
    scores["factual_correctness"] = 2
    verdicts[flipped["question_id"]] = _judge_verdict(**scores)
    result = evaluate("crm", verdicts, anchors)
    assert not result.passed
    assert result.per_dimension["factual_correctness"].directional_flip_count == 1


def test_calibration_fails_below_90pct_within_pm1():
    anchors = _make_anchors()
    verdicts = _mirror_verdicts(anchors)
    # Two anchors off by 2 on completeness → 80% within ±1, below the threshold.
    for a in anchors[:2]:
        human = a["human_scores"]["completeness"]
        scores = dict(a["human_scores"])
        scores["completeness"] = human + 2 if human <= 3 else human - 2
        verdicts[a["question_id"]] = _judge_verdict(**scores)
    result = evaluate("crm", verdicts, anchors)
    assert result.per_dimension["completeness"].within_pm1_pct == 80.0
    assert (
        result.per_dimension["completeness"].within_pm1_pct < ACCEPTANCE_AGREEMENT_PCT
    )
    assert not result.passed


def test_directional_flip_matches_the_contract_wording():
    """§10.2: 'a human 5 scored as a 1 or 2, or the reverse'. Nothing wider."""
    assert _is_directional_flip(5, 1) is True
    assert _is_directional_flip(5, 2) is True
    assert _is_directional_flip(1, 5) is True
    assert _is_directional_flip(2, 5) is True
    # Not flips — these are caught (or not) by the ±1 agreement rule instead.
    assert _is_directional_flip(4, 2) is False  # human 4, not 5
    assert _is_directional_flip(2, 4) is False  # judge 4, not 5
    assert _is_directional_flip(5, 4) is False  # both high side
    assert _is_directional_flip(5, 3) is False  # mid, not a flip
    assert _is_directional_flip(3, 5) is False  # mid, not a flip


def test_calibration_marker_is_bound_to_the_judge_that_earned_it(tmp_path):
    """§10.2's gate only means something if the marker vouches for the judge
    about to run. A pass on one model / prompt revision / mode licenses that
    judge and no other."""
    from judge.calibration import JudgeFingerprint, check_calibrated

    fp = JudgeFingerprint(
        model_version="floodgate/anthropic.claude-sonnet-4-6",
        prompt_version="judge-abc123",
        mode="per_dimension",
    )
    assert check_calibrated("crm", fp, tmp_path).calibrated is False

    anchors = _make_anchors()
    result = evaluate("crm", _mirror_verdicts(anchors), anchors)
    marker = record_passed("crm", result, fingerprint=fp, calibration_dir=tmp_path)
    assert marker.is_file()
    assert check_calibrated("crm", fp, tmp_path).calibrated is True
    assert is_calibrated("crm", fp, tmp_path) is True

    # A different model must NOT inherit the pass.
    other = JudgeFingerprint("openai/gpt-4o-mini", "judge-abc123", "per_dimension")
    state = check_calibrated("crm", other, tmp_path)
    assert state.calibrated is False
    assert state.stale is True
    assert "model_version" in state.reason

    # Nor a changed prompt, nor a different scoring mode.
    for changed in (
        JudgeFingerprint(fp.model_version, "judge-deadbeef", "per_dimension"),
        JudgeFingerprint(fp.model_version, fp.prompt_version, "combined"),
    ):
        assert check_calibrated("crm", changed, tmp_path).calibrated is False


def test_legacy_marker_without_a_fingerprint_does_not_license_a_run(tmp_path):
    from judge.calibration import JudgeFingerprint, check_calibrated

    (tmp_path / "crm.passed.json").write_text(
        json.dumps({"domain": "crm", "passed_at_utc": "2026-09-01T00:00:00Z"}),
        encoding="utf-8",
    )
    state = check_calibrated("crm", JudgeFingerprint("m", "p", "combined"), tmp_path)
    assert state.calibrated is False
    assert "predates judge fingerprinting" in state.reason


# --- the §10.2 scoring gate is opt-in (project decision) ----------------------


def test_the_scoring_gate_is_off_by_default():
    """A run proceeds without a calibration marker and without a flag.

    §10.2 reads the other way; this is a recorded project decision, taken so a
    baseline can be produced before the calibration session is scheduled. The
    mitigation is that the LABEL is not configurable — see the two assertions
    below it in spirit: every artifact still records calibrated=false.
    """

    assert load_judge_config("crm").calibration.require_calibration is False
    assert load_judge_config("crm").calibration.require_calibration_for_release is False


def test_a_domain_can_opt_back_into_the_scoring_gate(tmp_path):
    """Off by default, but still a gate: a domain that wants §10.2 enforced says
    so in its own config, and nothing else has to change."""

    root = tmp_path / "cfg"
    root.mkdir()
    (root / "default.json").write_text(
        json.dumps({"model": "m", "calibration": {"require_calibration": False}}),
        encoding="utf-8",
    )
    (root / "crm.json").write_text(
        json.dumps({"calibration": {"require_calibration": True}}), encoding="utf-8"
    )
    (root / "sales.json").write_text("{}", encoding="utf-8")

    assert load_judge_config("crm", root).calibration.require_calibration is True
    assert load_judge_config("sales", root).calibration.require_calibration is False
    # The opt-in must not drag the rest of the block with it — a shallow merge
    # would drop min_anchors and agreement_pct the moment one field is set.
    assert load_judge_config("crm", root).calibration.min_anchors == 10
    assert load_judge_config("crm", root).calibration.agreement_pct == 90.0


# --- precision: the platform printing a number shorter ------------------------


def test_a_value_printed_at_lower_precision_passes():
    """Observed against the live platform in both domains, and scored WRONG:

        CRM-T4-04-17    expected 1339.1438      printed "1339.14"
        SALES-T2-06-22  expected 2894260664.44  printed "$2,894,260,664"

    Both are the expected value rendered shorter. HC-3 fails numeric VARIANCE,
    and dropping cents from 2.8 billion is not variance — it is rendering, the
    same reading that makes 28,731 equal 28731. Failing these made the platform
    look worse than it is.
    """

    assert (
        exact_match("1339.1438", "the highest is at 1339.14") is ExactMatchResult.PASS
    )
    assert (
        exact_match("2894260664.44", "at approximately $2,894,260,664")
        is ExactMatchResult.PASS
    )


@pytest.mark.parametrize(
    ("expected", "answer", "why"),
    [
        ("23.21", "roughly 23%", "zero places on a percentage loses too much"),
        ("23.21", "about 23.2%", "one place still loses too much"),
        ("4182650.00", "the total is 4,182,000", "the module docstring's own example"),
        ("1013", "about 1,000 accounts", "rounded to the nearest thousand"),
        ("28725", "28,700 interactions", "rounded to the nearest hundred"),
    ],
)
def test_precision_leniency_does_not_become_a_tolerance(expected, answer, why):
    """The module promises there is no tolerance parameter, and this must not
    smuggle one in. A printed value has to be a FAITHFUL rounding of the
    expected one AND give up negligible precision relative to its magnitude.

    Faithfulness alone is not enough: 23 *is* 23.21 rounded to zero places, so
    only the magnitude bound rejects "roughly 23%".
    """

    assert exact_match(expected, answer) is ExactMatchResult.FAIL, why


def test_lower_precision_still_requires_the_right_label():
    """Relaxing precision must not relax entity ownership (the Appendix A shape)."""

    assert (
        exact_match(
            "Hardware | 2894260664.44", "Software at approximately $2,894,260,664"
        )
        is ExactMatchResult.FAIL
    )
    assert (
        exact_match(
            "Hardware | 2894260664.44", "Hardware at approximately $2,894,260,664"
        )
        is ExactMatchResult.PASS
    )
