def test_a_pass_the_judge_calls_factually_wrong_is_counted(tmp_path):
    """exact-match cannot see this class of error on its own.

    For a bare scalar it tests presence, so an answer that LEADS with the wrong
    figure still passes if the right one appears in a breakdown further down.
    Found five times by hand across the CRM and Sales runs of 2026-10-09 — e.g.
    "How many interactions are phone call events?", expected 28,627, answered
    "a total of 57,349" with 28,627 appearing later under engagement type.

    Counted and reported, never folded into the percentage: letting the judge
    overturn exact_match would couple the two scorers, and their independence is
    what made this visible at all.
    """

    from judge.contracts import DIMENSIONS, JudgeVerdict
    from judge.exact_match import ExactMatchOutcome, ExactMatchResult
    from scorecard.summary import GroupStats

    def verdict(factual: int) -> JudgeVerdict:
        base = dict.fromkeys(DIMENSIONS, 5)
        base["factual_correctness"] = factual
        return JudgeVerdict(
            dimension_rationales={d: "r" for d in DIMENSIONS},
            prompt_version="p",
            model_version="m",
            **base,
        )

    passed = ExactMatchOutcome(ExactMatchResult.PASS, "")
    failed = ExactMatchOutcome(ExactMatchResult.FAIL, "")
    stats = GroupStats()

    stats.add(verdict(1), passed, {})  # contradiction
    stats.add(verdict(2), passed, {})  # contradiction
    stats.add(verdict(3), passed, {})  # "missing fields", not a contradiction
    stats.add(verdict(5), passed, {})  # agreement
    stats.add(verdict(1), failed, {})  # both agree it is wrong

    assert stats.scorer_disagreements == 2
    assert stats.exact_match_pass == 4, "the percentage itself must not change"
