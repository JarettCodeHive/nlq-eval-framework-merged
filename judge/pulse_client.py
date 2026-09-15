"""Real Claris / Pulse platform client (HC-4).

Every evaluation question is submitted to the platform through this client; the
DuckDB reference-SQL execution is ground-truth verification only and is never
the thing being scored (HC-4 corollary).

Same interface as the DuckDB verification harness in ``judge.sql_pulse``:
``query(question_id) -> PulseResponse`` (sync), plus ``mode`` and
``available_ids()``. Constructed with the pair list so it can resolve a
``question_id`` to the natural-language question the API wants.

QA endpoint (verified against org 4104 on 2026-09-07):

    POST {base}/api-proxy/org/{org_id}/ai-svc/v2/tco/chat
    base = https://api-qa.platform.claris.com
    Authorization: Bearer <jwt>         (the "Bearer " prefix is REQUIRED — a raw
                                         JWT returns 401 "failed to parse token")
    X-Claris-Features: TCOApiV2Feature
    body: {prompt, messages:[{content,role}], chat_session_id,
           message_session_id, model_name, is_quick_prompt, stream,
           stream_thinking}

``stream=false`` returns one JSON body shaped like:

    {
      "response": "<stringified JSON: {analysis, dashboard, reasoning}>",
      "error": false, "errorCode": null,
      "analysis_request": [{"id": "primary", "sql": "SELECT ..."}, ...],
      "extraction_request": {"sources": [{"table_name": ...}]},
      "record_counts": [{"table_name": "accounts", "records": 100}],
      "timing": {...}, "messages": [...]
    }

  - the natural-language answer is ``json.loads(response)["analysis"]``
  - the generated SQL is ``analysis_request`` — the entry with id "primary" is
    the one that answers the question; the rest build the dashboard.

``stream=true`` returns an SSE event stream — also supported (``PULSE_STREAM``),
but non-stream is the default since it carries the SQL and answer in one body.

TLS: a corporate inspection proxy is common on client machines (self-signed cert
in the chain). Set ``PULSE_CA_BUNDLE`` to the corp CA file, or install the
``truststore`` package (auto-used) to trust the OS store, or last-resort
``PULSE_VERIFY_TLS=false``.

Setup: copy ``judge/.env.example`` → ``judge/.env`` and fill the ``PULSE_*`` vars.
Staging/QA only (HC-6).
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from judge.config import load_env  # shared .env autodetection
from judge.contracts import PulseResponse

__all__ = [
    "PulseClient",
    "PulseResponse",
    "PulseSettings",
    "MissingPulseCredentials",
    "PulseResponseError",
    "load_pulse_settings",
]

MODULE_ROOT = Path(__file__).resolve().parent

DEFAULT_CHAT_PATH = "/api-proxy/org/{org_id}/ai-svc/v2/tco/chat"
DEFAULT_MODEL_NAME = "claude-sonnet-5"
DEFAULT_FEATURES = "TCOApiV2Feature"

# CONFIRM-SCHEMA: candidate keys for the natural-language answer, best first.
_ANSWER_KEYS: tuple[str, ...] = (
    "answer_text",
    "answer",
    "final_answer",
    "content",
    "text",
    "response",
    "summary",
    "analysis_summary",
    "message",
    "output",
    "result",
)
# CONFIRM-SCHEMA: where the generated SQL might live.
_SQL_CONTAINER_KEYS: tuple[str, ...] = (
    "analysis_request",
    "query_plan",
    "plan",
    "analysis",
    "data",
    "result",
)
_SQL_SCALAR_KEYS: tuple[str, ...] = ("generated_sql", "sql")

# SSE events that carry a text delta we accumulate as a fallback answer.
_SSE_TEXT_EVENTS: tuple[str, ...] = ("content", "thinking", "message", "delta")


class MissingPulseCredentials(RuntimeError):
    """Raised with an actionable message rather than a bare 401/404 later."""


class PulseResponseError(RuntimeError):
    """The Pulse response did not carry a field we need, or was an API error."""


class PulseSettings(BaseModel):
    """Connection settings for the live platform API."""

    # `model_name` is the platform's own field name (ChatRequest.model_name).
    model_config = ConfigDict(protected_namespaces=())

    base_url: str
    auth_token: str
    org_id: int
    model_name: str = DEFAULT_MODEL_NAME
    chat_path: str = DEFAULT_CHAT_PATH  # may contain "{org_id}"
    # X-Claris-Features header value; the QA endpoint needs TCOApiV2Feature.
    features: str | None = DEFAULT_FEATURES
    stream: bool = False
    # is_quick_prompt: the QA sample sends true, but for arbitrary evaluation
    # questions we want the full query path. Override with PULSE_QUICK_PROMPT.
    quick_prompt: bool = False
    # Measured against org 4104 on crm_dataset_v2/full: 54s, 122s, 140s,
    # 169s. 180s left almost no margin, and a question that overruns is
    # treated as transient and retried — 4 x 180s = 12 min burned silently
    # on ONE question. 420s gives real headroom over the observed tail.
    timeout_s: int = Field(default=420, gt=0)
    max_retries: int = Field(default=3, ge=0)
    backoff_base_s: float = Field(default=0.5, gt=0)
    backoff_max_s: float = Field(default=8.0, gt=0)
    verify_tls: bool = True
    ca_bundle: str | None = None  # path to a CA file (corp proxy); wins over verify_tls

    def httpx_verify(self) -> "str | bool":
        return self.ca_bundle if self.ca_bundle else self.verify_tls

    def chat_path_for(self) -> str:
        return self.chat_path.format(org_id=self.org_id)

    @property
    def redacted(self) -> dict[str, Any]:
        """Safe for a run manifest — no token."""
        host = self.base_url.split("//")[-1].split("/")[0]
        return {
            "platform": "pulse",
            "base_url_host": host,
            "org_id": self.org_id,
            "model_name": self.model_name,
            "stream": self.stream,
            "features": self.features,
        }


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def load_pulse_settings(env_file: Path | None = None) -> PulseSettings:
    """Read ``PULSE_*`` credentials from .env / process env. Fails loudly."""
    load_env(env_file)

    base_url = (os.getenv("PULSE_BASE_URL") or "").strip().rstrip("/")
    auth_token = (os.getenv("PULSE_AUTH_TOKEN") or "").strip()
    org_id_raw = (os.getenv("PULSE_ORG_ID") or "").strip()

    missing = [
        name
        for name, val in (
            ("PULSE_BASE_URL", base_url),
            ("PULSE_AUTH_TOKEN", auth_token),
            ("PULSE_ORG_ID", org_id_raw),
        )
        if not val
    ]
    if missing:
        raise MissingPulseCredentials(
            "live Pulse requires: "
            + ", ".join(missing)
            + "\nFill the PULSE_* block in judge/.env — QA base URL "
            "https://api-qa.platform.claris.com, the Authorization token from "
            "Chrome DevTools, and the integer org id. See docs/judge_runbook.md."
        )
    try:
        org_id = int(org_id_raw)
    except ValueError as exc:
        raise MissingPulseCredentials(
            f"PULSE_ORG_ID must be an integer, got {org_id_raw!r}"
        ) from exc

    def _opt_int(name: str, default: int) -> int:
        raw = (os.getenv(name) or "").strip()
        return int(raw) if raw else default

    features_raw = os.getenv("PULSE_FEATURES")
    features = (
        DEFAULT_FEATURES if features_raw is None else (features_raw.strip() or None)
    )

    return PulseSettings(
        base_url=base_url,
        auth_token=auth_token,
        org_id=org_id,
        model_name=(os.getenv("PULSE_MODEL_NAME") or "").strip() or DEFAULT_MODEL_NAME,
        chat_path=(os.getenv("PULSE_CHAT_PATH") or "").strip() or DEFAULT_CHAT_PATH,
        features=features,
        stream=_env_bool("PULSE_STREAM", False),
        quick_prompt=_env_bool("PULSE_QUICK_PROMPT", False),
        timeout_s=_opt_int("PULSE_TIMEOUT_S", 420),
        max_retries=_opt_int("PULSE_MAX_RETRIES", 3),
        verify_tls=_env_bool("PULSE_VERIFY_TLS", True),
        ca_bundle=(os.getenv("PULSE_CA_BUNDLE") or "").strip() or None,
    )


def token_expiry(auth_token: str) -> "datetime | None":
    """Read ``exp`` out of a Cognito ID JWT without verifying it.

    Purely informational — the server is the authority. We use it to fail a run
    *before* it spends two minutes per question against a token that will die
    mid-flight. Returns None when the token is not a readable JWT.
    """
    try:
        part = auth_token.split(".")[1]
        part += "=" * (-len(part) % 4)
        exp = json.loads(base64.urlsafe_b64decode(part)).get("exp")
        return datetime.fromtimestamp(float(exp), tz=timezone.utc) if exp else None
    except Exception:
        return None


def check_token_headroom(settings: PulseSettings, needed_s: float) -> "str | None":
    """Return a human-readable warning when the token cannot cover the run.

    ``needed_s`` is the caller's estimate of how long the run will take. None
    means "no readable expiry, or enough headroom".
    """
    exp = token_expiry(settings.auth_token)
    if exp is None:
        return None
    left = (exp - datetime.now(timezone.utc)).total_seconds()
    if left <= 0:
        return (
            f"PULSE_AUTH_TOKEN expired {-left / 60:.0f} min ago — refresh judge/.env."
        )
    if left < needed_s:
        return (
            f"PULSE_AUTH_TOKEN has {left / 60:.0f} min left but this run needs about "
            f"{needed_s / 60:.0f} min. It will expire mid-run and later questions "
            f"will fail with 401. Refresh judge/.env first."
        )
    return None


class PulseClient:
    """Live platform client. Submit each question through Pulse (HC-4)."""

    def __init__(
        self,
        settings: PulseSettings,
        pairs: list[dict],
        *,
        client: Any | None = None,  # httpx.Client — injectable for tests
        raw_dir: Any | None = None,  # Path — dump every raw response here
        run_log: Any | None = None,  # judge.run_log.RunLog
    ) -> None:
        self._settings = settings
        self._raw_dir = Path(raw_dir) if raw_dir else None
        if self._raw_dir:
            self._raw_dir.mkdir(parents=True, exist_ok=True)
        if run_log is None:
            from judge.run_log import NullRunLog

            run_log = NullRunLog()
        self._log = run_log
        self._by_id: dict[str, dict] = {p["question_id"]: p for p in pairs}
        self._chat_path = settings.chat_path_for()

        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            import httpx

            verify = settings.httpx_verify()
            if verify is True:
                # Corp machines often TLS-inspect with a private root CA that
                # only the OS store knows about. Use it when available.
                try:
                    import truststore

                    truststore.inject_into_ssl()
                except ImportError:
                    pass
            self._client = httpx.Client(
                base_url=settings.base_url,
                timeout=settings.timeout_s,
                verify=verify,
            )
            self._owns_client = True

    # --- interface parity with the Pulse stand-ins -----------------------

    @property
    def mode(self) -> str:
        return "live-stream" if self._settings.stream else "live"

    def available_ids(self) -> list[str]:
        return sorted(self._by_id)

    def query(self, question_id: str) -> PulseResponse:
        pair = self._by_id.get(question_id)
        if pair is None:
            raise KeyError(f"PulseClient: unknown question_id={question_id!r}")
        question = (pair.get("natural_language_question") or "").strip()
        if not question:
            raise KeyError(
                f"PulseClient: pair {question_id!r} has no natural_language_question"
            )

        self._log.event("pulse.request", question_id=question_id, question=question)
        t0 = time.time()
        wire_ids: dict[str, str] = {}
        try:
            data, wire_ids = self._chat(question)
        except Exception as exc:
            elapsed = time.time() - t0
            self._dump_raw(
                question_id,
                {"__error__": str(exc), "__question__": question},
                elapsed,
                wire_ids,
            )
            self._log.event(
                "pulse.error",
                question_id=question_id,
                latency_s=round(elapsed, 3),
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
            raise
        elapsed = time.time() - t0
        self._dump_raw(question_id, data, elapsed, wire_ids)

        answer = _extract_answer(data)
        sql = _extract_sql(data)
        timing = data.get("timing") if isinstance(data, dict) else None
        self._log.event(
            "pulse.response",
            question_id=question_id,
            latency_s=round(elapsed, 3),
            answer_chars=len(answer or ""),
            sql_present=bool(sql),
            server_timing=timing if isinstance(timing, dict) else None,
            record_counts=data.get("record_counts") if isinstance(data, dict) else None,
        )
        return PulseResponse(
            question_id=question_id, answer_text=answer, generated_sql=sql
        )

    def _dump_raw(
        self,
        question_id: str,
        data: Any,
        elapsed: float,
        wire_ids: dict[str, str] | None = None,
    ) -> None:
        """Persist the untouched platform payload, so a run can be re-scored
        offline instead of re-queried (a live query costs ~2 minutes)."""
        if self._raw_dir is None:
            return
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", question_id) or "unknown"
        path = self._raw_dir / f"{safe}.json"
        envelope = {
            "question_id": question_id,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "latency_s": round(elapsed, 3),
            "org_id": self._settings.org_id,
            "model_name": self._settings.model_name,
            # The ids that were on the wire for THIS question. Recording a
            # client-lifetime placeholder here made every file in a run show one
            # shared session, which reads as cross-question contamination that
            # never happened.
            **(wire_ids or {}),
            "response": data,
        }
        try:
            path.write_text(
                json.dumps(envelope, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except Exception as exc:  # never let logging kill a run
            self._log.event(
                "pulse.raw_dump_failed", question_id=question_id, error=str(exc)[:300]
            )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # --- internals ------------------------------------------------------

    def _new_session_id(self) -> str:
        """A fresh chat session per question.

        Evaluation questions must be independent. Reusing one session makes
        each question a turn in a growing conversation, so an answer can be
        coloured by earlier questions — a confound that would be invisible in
        the scorecard and impossible to reconstruct afterwards.
        """
        return str(uuid.uuid4())

    def _build_body(self, question: str) -> dict[str, Any]:
        # `prompt` alone carries the question. Sending it again inside
        # `messages` made every transcript read ["user", "user", "assistant"] —
        # the platform saw each question twice, which is not what a real client
        # sends and is not the input we mean to be measuring.
        #
        # `chat_session_id` is per-question (see _new_session_id): evaluation
        # questions must be independent, and a shared session makes each one a
        # turn in a growing conversation where later answers can be coloured by
        # earlier ones.
        return {
            "prompt": question,
            "messages": [],
            "chat_session_id": self._new_session_id(),
            "message_session_id": str(uuid.uuid4()),
            "model_name": self._settings.model_name,
            "is_quick_prompt": self._settings.quick_prompt,
            "stream": self._settings.stream,
            "stream_thinking": False,
        }

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": _bearer(self._settings.auth_token),
            "Content-Type": "application/json",
            "X-Request-ID": str(uuid.uuid4()),
        }
        if self._settings.features:
            headers["X-Claris-Features"] = self._settings.features
        return headers

    def _chat(self, question: str) -> tuple[dict, dict[str, str]]:
        """Returns (payload, wire_ids). `wire_ids` is what was actually sent —
        the session ids are minted per request, so nothing else can report
        them faithfully after the fact."""
        body = self._build_body(question)
        wire_ids = {
            "chat_session_id": body["chat_session_id"],
            "message_session_id": body["message_session_id"],
        }
        headers = self._build_headers()
        attempts = self._settings.max_retries + 1
        last_exc: Exception | None = None

        for attempt in range(attempts):
            try:
                if self._settings.stream:
                    return self._chat_sse(body, headers), wire_ids
                return self._chat_json(body, headers), wire_ids
            except _RetryablePulseError as exc:
                last_exc = exc
                if attempt == attempts - 1:
                    raise PulseResponseError(str(exc)) from exc
                self._sleep(attempt)
            except Exception as exc:  # transport error (timeout, connection)
                last_exc = exc
                if not _is_transient(exc) or attempt == attempts - 1:
                    if isinstance(exc, PulseResponseError):
                        raise
                    raise PulseResponseError(
                        f"Pulse request failed after {attempt + 1} attempt(s): {exc}"
                    ) from exc
                self._sleep(attempt)
        raise PulseResponseError(  # pragma: no cover
            f"Pulse request failed: {last_exc}"
        )

    def _chat_json(self, body: dict, headers: dict) -> dict:
        resp = self._client.post(self._chat_path, json=body, headers=headers)
        _raise_for_status(resp.status_code, resp.text)
        try:
            data = resp.json()
        except ValueError as exc:
            raise PulseResponseError(
                f"Pulse returned a non-JSON 200 body: {resp.text[:300]!r}"
            ) from exc
        if not isinstance(data, dict):
            raise PulseResponseError(
                f"Pulse 200 body was {type(data).__name__}, expected an object"
            )
        return data

    def _chat_sse(self, body: dict, headers: dict) -> dict:
        """Consume the SSE stream and fold it back into a single dict."""
        events: list[dict] = []
        text_parts: list[str] = []
        with self._client.stream(
            "POST", self._chat_path, json=body, headers=headers
        ) as resp:
            if resp.status_code != 200:
                resp.read()
                _raise_for_status(resp.status_code, resp.text)
            current_event = "message"
            for raw in resp.iter_lines():
                line = raw.strip() if isinstance(raw, str) else raw.decode().strip()
                if not line:
                    current_event = "message"
                    continue
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except ValueError:
                    text_parts.append(payload)
                    continue
                if isinstance(obj, dict):
                    obj.setdefault("_event", current_event)
                    events.append(obj)
                    if current_event == "error" or obj.get("error"):
                        raise PulseResponseError(f"Pulse SSE error event: {obj!r}")
                    if current_event in _SSE_TEXT_EVENTS:
                        for key in ("content", "text", "delta", "message"):
                            val = obj.get(key)
                            if isinstance(val, str):
                                text_parts.append(val)
                                break

        if not events and not text_parts:
            raise PulseResponseError("Pulse SSE stream produced no events")

        # Prefer the terminal 'done'/'analysis_complete' payload; fall back to a
        # merge of every event, plus the accumulated text under 'content'.
        merged: dict[str, Any] = {}
        for obj in events:
            merged.update({k: v for k, v in obj.items() if k != "_event"})
        for obj in events:
            if obj.get("_event") in ("done", "analysis_complete", "result"):
                merged.update({k: v for k, v in obj.items() if k != "_event"})
        # The accumulated text-event stream is the real answer; per-event values
        # are deltas, so the join wins over whatever a single event left behind.
        if text_parts:
            merged["content"] = "".join(text_parts).strip()
        merged.setdefault("_sse_events", [o.get("_event") for o in events])
        return merged

    def _sleep(self, attempt: int) -> None:
        delay = min(
            self._settings.backoff_base_s * (2**attempt),
            self._settings.backoff_max_s,
        )
        time.sleep(delay)


class _RetryablePulseError(RuntimeError):
    """Internal: a 429/5xx that the retry loop should back off and retry."""


def _raise_for_status(status_code: int, text: str) -> None:
    if status_code == 200:
        return
    snippet = (text or "")[:500]
    if status_code == 429 or status_code >= 500:
        raise _RetryablePulseError(f"Pulse {status_code}: {snippet!r}")
    raise PulseResponseError(f"Pulse {status_code}: {snippet!r}")


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__
    return "Timeout" in name or "Connect" in name or "Network" in name


_AUTH_SCHEMES = ("bearer ", "basic ", "token ", "jwt ")


def _bearer(token: str) -> str:
    """Add the ``Bearer`` scheme unless the token already carries one.

    The QA gateway 401s a raw JWT with "failed to parse token: EOF".
    """
    t = token.strip()
    if t.lower().startswith(_AUTH_SCHEMES):
        return t
    return f"Bearer {t}"


def _maybe_json(value: object) -> object:
    """Pulse nests its real payload as a JSON *string* under ``response``."""
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in ("{", "["):
            try:
                return json.loads(s)
            except ValueError:
                return value
    return value


def _walk_strings(obj: Any, keys: tuple[str, ...]) -> str | None:
    """Depth-first search for the first non-empty string under any of `keys`."""
    if isinstance(obj, dict):
        for key in keys:
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for val in obj.values():
            found = _walk_strings(val, keys)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _walk_strings(item, keys)
            if found:
                return found
    return None


def _extract_answer(data: dict) -> str:
    """Pull the natural-language answer out of a TCO ChatResponse.

    Confirmed QA shape: ``{"response": "<json string>", ...}`` where the parsed
    string has an ``analysis`` field. Falls back to a broader search so a schema
    tweak doesn't silently break scoring.
    """
    # Loud API-level errors first.
    err = data.get("error")
    if err not in (None, False, "", 0) or data.get("errorCode"):
        raise PulseResponseError(
            f"Pulse returned an error: error={err!r} errorCode={data.get('errorCode')!r}"
        )

    if isinstance(data.get("refusal"), str) and data["refusal"].strip():
        return data["refusal"].strip()  # a refusal is still a scored response

    payload = _maybe_json(data.get("response"))
    if isinstance(payload, dict):
        for key in ("analysis", "answer", "summary", "content", "text"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    elif isinstance(payload, str) and payload.strip():
        return payload.strip()

    # `messages` transcript — last ASSISTANT turn.
    #
    # The turn's content is itself stringified JSON ({analysis, dashboard,
    # reasoning}), so returning it raw hands the judge a ~24KB blob instead of
    # the ~1KB answer — and leaks the agent's own reasoning into the text being
    # scored. Parse it and take the answer field, exactly as the primary path
    # does. Only fall back to the raw string when it is not JSON at all.
    messages = data.get("messages")
    if isinstance(messages, list) and messages:
        assistant_turns = [
            m
            for m in messages
            if isinstance(m, dict) and m.get("role") not in ("user", "system")
        ]
        for turn in reversed(
            assistant_turns or [m for m in messages if isinstance(m, dict)]
        ):
            for key in ("content", "text", "message"):
                val = turn.get(key)
                if not isinstance(val, str) or not val.strip():
                    continue
                inner = _maybe_json(val)
                if isinstance(inner, dict):
                    for akey in ("analysis", "answer", "summary", "content", "text"):
                        got = inner.get(akey)
                        if isinstance(got, str) and got.strip():
                            return got.strip()
                    continue  # JSON, but no answer field — keep looking
                return val.strip()

    found = _walk_strings(data, _ANSWER_KEYS)
    if found:
        return found

    raise PulseResponseError(
        "no answer field found in Pulse response; keys seen: "
        f"{sorted(data)!r}. Update _extract_answer (CONFIRM-SCHEMA)."
    )


def _collect_sql(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        # AnalysisSql shape: {"id": "...", "sql": "..."}
        for key in ("sql", *_SQL_SCALAR_KEYS):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                out.append(val.strip())
        for val in obj.values():
            out.extend(_collect_sql(val))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_collect_sql(item))
    return out


def _extract_sql(data: dict) -> str | None:
    """Pull the platform-generated SQL, if any — ALL of it.

    ``analysis_request`` is a list of ``{"id", "sql"}``. We used to keep only
    the entry tagged ``"primary"``, on the assumption that it answers the
    question and the rest merely build the dashboard. That assumption is false
    and it corrupted scoring.

    Observed on CRM-T5-01-SEED-01 ("which three campaign types drove the most
    engagement?"): Pulse issued five statements. ``primary`` was a bare
    ``SELECT COUNT(*)`` with no GROUP BY — the headline metric. The statement
    that actually answered the question (GROUP BY campaign_type ORDER BY ...
    LIMIT 3) was a *different* entry, which we discarded. The judge was then
    shown only the scalar count and penalised the platform for "not grouping by
    campaign type, ordering, or limiting to three" — every one of which the
    discarded statement did.

    So: return every statement, ``primary`` first and labelled, and let the
    judge assess whether the platform's SQL *taken together* supports the
    answer. Returns ``None`` when there is no SQL (judge scores SQL
    plausibility 1, per the rubric).
    """
    ar = data.get("analysis_request")
    if isinstance(ar, list) and ar:
        entries: list[tuple[str, str]] = []
        for e in ar:
            if (
                isinstance(e, dict)
                and isinstance(e.get("sql"), str)
                and e["sql"].strip()
            ):
                entries.append((str(e.get("id") or "?"), e["sql"].strip()))
        if entries:
            entries.sort(key=lambda kv: kv[0] != "primary")  # primary first
            if len(entries) == 1:
                return entries[0][1]
            parts = []
            for i, (eid, sql) in enumerate(entries, 1):
                label = "primary" if eid == "primary" else f"statement {i}"
                parts.append(f"-- [{label}]\n{sql}")
            return "\n\n".join(parts)

    seen: list[str] = []
    for key in _SQL_CONTAINER_KEYS:
        if key in data:
            seen.extend(_collect_sql(data[key]))
    if not seen:
        seen.extend(_collect_sql(data))

    unique = list(dict.fromkeys(seen))  # de-dup, preserve order
    if not unique:
        return None
    return ";\n".join(unique)
