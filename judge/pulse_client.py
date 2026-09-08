"""Real Claris / Pulse platform client (HC-4).

Every evaluation question is submitted to the platform through this client; the
DuckDB reference-SQL execution is ground-truth verification only and is never
the thing being scored (HC-4 corollary).

Same interface as the offline stand-ins in ``judge.mock_pulse``:
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

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from judge.config import load_env  # shared .env autodetection
from judge.mock_pulse import PulseResponse

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
    # Pulse answers take ~30s each — a generous default timeout.
    timeout_s: int = Field(default=180, gt=0)
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
        timeout_s=_opt_int("PULSE_TIMEOUT_S", 180),
        max_retries=_opt_int("PULSE_MAX_RETRIES", 3),
        verify_tls=_env_bool("PULSE_VERIFY_TLS", True),
        ca_bundle=(os.getenv("PULSE_CA_BUNDLE") or "").strip() or None,
    )


class PulseClient:
    """Live platform client. Submit each question through Pulse (HC-4)."""

    def __init__(
        self,
        settings: PulseSettings,
        pairs: list[dict],
        *,
        client: Any | None = None,  # httpx.Client — injectable for tests
    ) -> None:
        self._settings = settings
        self._by_id: dict[str, dict] = {p["question_id"]: p for p in pairs}
        self._chat_path = settings.chat_path_for()
        # One chat session for the whole run; a fresh message id per question.
        self._chat_session_id = str(uuid.uuid4())

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

        data = self._chat(question)
        answer = _extract_answer(data)
        sql = _extract_sql(data)
        return PulseResponse(
            question_id=question_id, answer_text=answer, generated_sql=sql
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # --- internals ------------------------------------------------------

    def _build_body(self, question: str) -> dict[str, Any]:
        return {
            "prompt": question,
            "messages": [{"content": question, "role": "user"}],
            "chat_session_id": self._chat_session_id,
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

    def _chat(self, question: str) -> dict:
        body = self._build_body(question)
        headers = self._build_headers()
        attempts = self._settings.max_retries + 1
        last_exc: Exception | None = None

        for attempt in range(attempts):
            try:
                if self._settings.stream:
                    return self._chat_sse(body, headers)
                return self._chat_json(body, headers)
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

    # `messages` transcript — last assistant turn.
    messages = data.get("messages")
    if isinstance(messages, list) and messages:
        last = messages[-1]
        if isinstance(last, dict):
            for key in ("content", "text", "message"):
                val = last.get(key)
                if isinstance(val, str) and val.strip():
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
    """Pull the platform-generated SQL, if any.

    Confirmed QA shape: ``analysis_request`` is a list of ``{"id", "sql"}``. The
    entry with id ``"primary"`` is the query that answers the question; the
    others build the dashboard. We score the primary when present, else all of
    them joined. Returns ``None`` when there is no SQL (judge scores SQL
    plausibility 1, per the rubric).
    """
    ar = data.get("analysis_request")
    if isinstance(ar, list) and ar:
        primary = [
            e["sql"].strip()
            for e in ar
            if isinstance(e, dict)
            and e.get("id") == "primary"
            and isinstance(e.get("sql"), str)
            and e["sql"].strip()
        ]
        if primary:
            return primary[0]

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
