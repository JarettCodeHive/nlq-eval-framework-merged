"""Independent-review check in miniature: re-execute every reference_sql
against the typed DuckDB and confirm it still produces expected_answer,
and that the verification log's result_hash matches (scope doc 9.6)."""

import hashlib
import json
from pathlib import Path

from utils.output_paths import resolve_qa_output_dir
from utils.serialization import serialize

QA_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = resolve_qa_output_dir(QA_ROOT, "full") / "verification_logs"


def _rows(con, r):
    cur = con.execute(r["reference_sql"])
    return cur.fetchall(), [d[0] for d in cur.description]


def test_reference_sql_reproduces_expected_answer(pairs, companion, con):
    """Re-executed for all 179 rows - the 160 release pairs AND the 19
    rephrase variants (they reuse a base's reference_sql verbatim, but
    re-verifying proves that hasn't drifted). The result_hash cross-check
    against the companion is only meaningful for the 160 - the companion
    does not carry rows for the variants."""
    comp = {c["natural_language_question"]: c for c in companion}
    for r in pairs:
        result, cols = _rows(con, r)
        assert result, f"zero rows now: {r['natural_language_question']}"
        answer = serialize(result, cols)
        assert answer == r["expected_answer"], f"answer drift: {r['natural_language_question']}"
        if r["is_release_160"] != "true":
            continue
        # result_hash = SHA-256 of the canonical serialized answer (portable)
        c = comp[r["natural_language_question"]]
        assert hashlib.sha256(answer.encode()).hexdigest() == c["result_hash"]


def test_every_question_has_a_verification_log(companion):
    for c in companion:
        p = LOG_DIR / f"{c['question_id']}.json"
        assert p.exists(), f"missing log {p.name}"
        log = json.loads(p.read_text())
        assert log["execution_status"] == "ok"
        assert log["result_hash"] == c["result_hash"]


def test_no_shipped_pair_returns_zero_or_null(con, pairs):
    for r in pairs:
        result, cols = _rows(con, r)
        assert result
        if len(result) == 1 and len(cols) == 1:
            assert result[0][0] is not None


def test_reference_sql_reproduces_expected_answer_from_authoritative_source(
    pairs, companion, authoritative_con
):
    """Rebuild from the configured full release and re-verify every pair."""
    comp = {c["natural_language_question"]: c for c in companion}
    for r in pairs:
        result, cols = _rows(authoritative_con, r)
        assert result, f"zero rows now: {r['natural_language_question']}"
        answer = serialize(result, cols)
        assert (
            answer == r["expected_answer"]
        ), f"answer drift vs authoritative source: {r['natural_language_question']}"
        if r["is_release_160"] != "true":
            continue
        c = comp[r["natural_language_question"]]
        assert hashlib.sha256(answer.encode()).hexdigest() == c["result_hash"]
