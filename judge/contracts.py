"""Judge contracts — Execution Spec §10 rubric shape.

Four named dimensions, each an integer 1-5. Score shape and 1/3/5 anchors are
authoritative in the Spec; 2 and 4 are interpolated in the rubric (see
judge/rubric.md §non-goals).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

DIMENSIONS: tuple[str, ...] = (
    "factual_correctness",
    "completeness",
    "format_adherence",
    "sql_plausibility",
)


class JudgeRequest(BaseModel):
    """Everything the judge is allowed to see about one Q&A pair + Platform response."""

    model_config = ConfigDict(extra="forbid")

    question: str

    # Two distinct reference fields (§9.4). NOT interchangeable:
    #   expected_answer  → the bare deterministic ground truth ("42")
    #   judge_reference  → the ideal full response a good Platform would give
    # The rubric anchors Factual Correctness on expected_answer, Completeness
    # and Format Adherence on judge_reference. See templates/judge/_context.jinja.
    expected_answer: str
    judge_reference: str

    platform_answer: str
    generated_sql: str | None = None
    domain: str = "crm"

    # Carried through so cache keys and result rows can be traced to a pair.
    question_id: str | None = None


class JudgeVerdict(BaseModel):
    """One judge judgment."""

    # `model_version` collides with pydantic's `model_` protected namespace; silence.
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    # Section 10.1 requires a distinct rationale for every dimension. Keeping
    # these separate prevents an apparently plausible general explanation from
    # masking an unsupported individual score.
    dimension_rationales: dict[str, str]

    factual_correctness: int = Field(ge=1, le=5, strict=True)
    completeness: int = Field(ge=1, le=5, strict=True)
    format_adherence: int = Field(ge=1, le=5, strict=True)
    sql_plausibility: int = Field(ge=1, le=5, strict=True)

    # Provenance. A score that cannot be attributed to a prompt + model version
    # is not auditable and cannot be compared across runs.
    prompt_version: str
    model_version: str

    # True when this verdict was served from cache rather than the provider.
    cached: bool = False

    # False when the provider rejected temperature=0 and the run was allowed to
    # continue on the model default + fixed seed. Stamped on the VERDICT rather
    # than read off the judge afterwards, so it survives the cache — a
    # cache-served run still reports the determinism basis its scores were
    # actually produced under.
    temperature_enforced: bool = True

    # False when a seed WAS configured but never reached the provider — either
    # the deployment rejected it, or the provider has none (Anthropic). Same
    # reasoning as `temperature_enforced`: a manifest that claims a determinism
    # control it never applied is worse than a noisy one.
    seed_enforced: bool = True

    # Full prompts and raw responses are retained in the content-addressed
    # cache so a cache hit can still emit a complete per-run audit trace.
    audit_trace: list[dict[str, object]] = Field(default_factory=list, repr=False)

    @field_validator("dimension_rationales")
    @classmethod
    def _validate_dimension_rationales(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != set(DIMENSIONS):
            missing = sorted(set(DIMENSIONS) - set(value))
            extra = sorted(set(value) - set(DIMENSIONS))
            raise ValueError(
                f"dimension_rationales must contain exactly {DIMENSIONS}; "
                f"missing={missing}, extra={extra}"
            )
        if any(
            not isinstance(text, str) or not text.strip() for text in value.values()
        ):
            raise ValueError("every dimension rationale must be a non-empty string")
        return {dimension: value[dimension].strip() for dimension in DIMENSIONS}

    @property
    def rationale(self) -> str:
        """Readable aggregate retained for existing reports and console output."""
        return " ".join(
            f"[{dimension}] {self.dimension_rationales[dimension]}"
            for dimension in DIMENSIONS
        )

    @property
    def overall_score(self) -> float:
        """Unweighted mean of the four dimensions.

        Reported alongside per-dimension scores. Never blended with exact-match
        into a composite (§11.3 last line).
        """
        return sum(getattr(self, d) for d in DIMENSIONS) / len(DIMENSIONS)

    @property
    def normalized(self) -> float:
        """Map the 1-5 mean onto 0-1.

        `(x - 1) / 4`, NOT `x / 5`. Scale floor is 1, so the worst possible
        judgment must map to 0.0. Using x/5 gives a worst-possible verdict
        20% credit and silently inflates everything above it.
        """
        return (self.overall_score - 1) / 4


class PulseResponse(BaseModel):
    """One platform answer, as the judge consumes it.

    Lives here, not beside the test doubles: this is the contract the LIVE
    client returns, and the live path must not import from a module named for
    its stand-ins.
    """

    question_id: str
    answer_text: str
    generated_sql: str | None = None
    # True when the platform declined to answer and asked a clarifying question
    # instead (`clarify: true` in the TCO payload). A distinct outcome, not a
    # wrong answer — see `ClarificationVerdict`. Only the live client can
    # observe this; an offline reference_sql replay never clarifies.
    clarify: bool = False
    # Other platform-side fields (timing, record_counts, reasoning, dashboard)
    # are preserved in the run's pulse_raw/ payload rather than here — they are
    # diagnostics, not scored inputs.


# Fixed score for a declined answer, set by the Platform Owner. Deliberately a
# constant rather than a config field: it is a scoring-policy decision that has
# to read the same on every run and every scorecard, and a run that could quietly
# use a different one would not be comparable to the baseline.
CLARIFICATION_SCORE: float = 2.5


class ClarificationVerdict(BaseModel):
    """The platform asked a clarifying question instead of answering (§2e).

    Deliberately NOT a `JudgeVerdict`. All four rubric dimensions anchor on an
    answer — factual correctness against `expected_answer`, completeness and
    format against `judge_reference` — and there is no answer here to anchor on.
    Inventing four integer scores to average out at 2.5 would put numbers in the
    scorecard that no rubric produced.

    So the dimensions stay absent and `overall_score` is the fixed
    `CLARIFICATION_SCORE`. No provider call is made: the score does not depend
    on the model, so spending a judge call on it would buy nothing and cost
    ~12s per clarification.

    Read the consequence for the scorecard plainly: these rows are counted in
    `mean_overall` and NOT in `mean_per_dimension`, so the two carry different
    denominators. Both are reported.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str | None = None
    # What the platform asked for instead. Kept so a reader can see whether the
    # clarification was reasonable or, as in §2e, offered options none of which
    # were correct.
    clarification_text: str = ""

    @property
    def overall_score(self) -> float:
        return CLARIFICATION_SCORE

    @property
    def rationale(self) -> str:
        return (
            "Platform declined to answer and requested clarification; scored at "
            f"the fixed {CLARIFICATION_SCORE} rather than judged, because the "
            "rubric dimensions anchor on an answer that was never given."
        )

