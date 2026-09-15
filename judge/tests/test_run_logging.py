"""Raw-response capture and run logging.

A live Pulse query costs ~2 minutes, so a run that discards the platform's
payload cannot be re-scored offline — it has to be re-queried. These tests pin
the capture down, including the part that matters most: no credential ever
reaches disk.
"""

from __future__ import annotations

import json

import pytest

from judge.pulse_client import PulseClient, PulseSettings
from judge.run_log import NullRunLog, RunLog, redact

QID = "CRM-T2-01"
QUESTION = "How many support cases belong to West-region accounts?"
SECRET = "super-secret-jwt-value"

PAYLOAD = {
    "response": json.dumps(
        {"analysis": "A total of 29,049 support cases.", "reasoning": "why"}
    ),
    "error": False,
    "errorCode": None,
    "analysis_request": [{"id": "primary", "sql": "SELECT COUNT(*) FROM x"}],
    "record_counts": [{"table_name": "support_cases", "records": 144000}],
    "timing": {"total_ms": 116595},
}


class _Resp:
    status_code = 200
    text = json.dumps(PAYLOAD)

    def json(self):
        return PAYLOAD


class _StubHttp:
    def post(self, *a, **k):
        return _Resp()

    def close(self):
        pass


def _client(tmp_path, log):
    settings = PulseSettings(
        base_url="https://example.invalid", auth_token=SECRET, org_id="4104"
    )
    pairs = [{"question_id": QID, "natural_language_question": QUESTION}]
    return PulseClient(
        settings,
        pairs,
        client=_StubHttp(),
        raw_dir=tmp_path / "pulse_raw",
        run_log=log,
    )


def test_raw_response_is_persisted_whole(tmp_path):
    """The untouched payload lands on disk — not just the two extracted fields."""
    c = _client(tmp_path, NullRunLog())
    c.query(QID)

    raw = tmp_path / "pulse_raw" / f"{QID}.json"
    assert raw.is_file()
    env = json.loads(raw.read_text())

    # The envelope carries provenance the payload itself does not.
    assert env["question_id"] == QID
    assert "fetched_at_utc" in env and "latency_s" in env

    # Everything Pulse returned survives — these are the fields a post-mortem
    # needs and the old code threw away.
    body = env["response"]
    assert "timing" in body
    assert "record_counts" in body
    assert "analysis_request" in body
    assert "reasoning" in json.loads(body["response"])


def test_raw_capture_never_writes_the_token(tmp_path):
    log = RunLog(tmp_path / "run_log.jsonl")
    c = _client(tmp_path, log)
    c.query(QID)

    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert SECRET not in path.read_text(), f"token leaked into {path.name}"


def test_run_log_records_request_and_response(tmp_path):
    log = RunLog(tmp_path / "run_log.jsonl")
    c = _client(tmp_path, log)
    c.query(QID)

    events = [
        json.loads(line)
        for line in (tmp_path / "run_log.jsonl").read_text().splitlines()
    ]
    names = [e["event"] for e in events]
    assert "pulse.request" in names
    assert "pulse.response" in names

    resp = next(e for e in events if e["event"] == "pulse.response")
    assert resp["question_id"] == QID
    assert resp["sql_present"] is True
    assert resp["answer_chars"] > 0
    assert "latency_s" in resp


def test_failed_query_still_leaves_a_record(tmp_path):
    """A run that dies must explain itself — errors are captured, not swallowed."""

    class _Boom:
        def post(self, *a, **k):
            raise RuntimeError("connection reset")

        def close(self):
            pass

    settings = PulseSettings(
        base_url="https://example.invalid", auth_token=SECRET, org_id="4104"
    )
    log = RunLog(tmp_path / "run_log.jsonl")
    c = PulseClient(
        settings,
        [{"question_id": QID, "natural_language_question": QUESTION}],
        client=_Boom(),
        raw_dir=tmp_path / "pulse_raw",
        run_log=log,
    )
    with pytest.raises(Exception):
        c.query(QID)

    assert (tmp_path / "pulse_raw" / f"{QID}.json").is_file()
    names = [
        json.loads(line)["event"]
        for line in (tmp_path / "run_log.jsonl").read_text().splitlines()
    ]
    assert "pulse.error" in names


def test_redact_masks_credential_shaped_keys():
    out = redact({"auth_token": "abc", "org_id": 4104, "n": {"api_key": "z", "m": "x"}})
    assert out["auth_token"].startswith("<redacted")
    assert out["n"]["api_key"].startswith("<redacted")
    assert out["org_id"] == 4104
    assert out["n"]["m"] == "x"
