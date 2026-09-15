"""LLM judge (OpenAI direct OR Azure OpenAI), with content-keyed caching.

The class is provider-agnostic — construct with either `OpenAISettings` or
`AzureSettings`; the client (AsyncOpenAI / AsyncAzureOpenAI) is selected at
construction. The `chat.completions.create()` surface is identical across the
two clients, so nothing downstream branches on provider.

Determinism (§10.1): temperature 0, fixed seed, JSON-mode. If the deployment
rejects temperature 0 it fails loudly, unless `JudgeConfig.require_temperature_zero`
is False — then it degrades to "model default + seed" and the verdict records
`temperature_enforced=False`.
"""

from __future__ import annotations

import asyncio
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAzureOpenAI,
    AsyncOpenAI,
)

from judge.cache import JudgeCache, cache_key
from judge.client import JudgeClient
from judge.config import AzureSettings, JudgeConfig, LLMSettings, OpenAISettings
from judge.contracts import DIMENSIONS, JudgeRequest, JudgeVerdict
from judge.parsing import JudgeOutputError, parse_combined, parse_dimension
from judge.prompts import prompt_version, render_combined, render_dimension

ParsedT = TypeVar("ParsedT")


class OpenAIJudge(JudgeClient):
    """Judge backed by an OpenAI-compatible chat completions endpoint.

    Provider auto-selected from the `settings` type — pass `AzureSettings` for
    Azure OpenAI, `OpenAISettings` for OpenAI direct. Same class handles both.
    """

    def __init__(
        self,
        settings: LLMSettings,
        config: JudgeConfig,
        *,
        cache_dir: Path | None = None,
        prompt_log_path: Path | None = None,
        judge_run_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._config = config
        self._json_mode = config.json_mode
        # Azure: the OpenAI SDK sends `model=` as the DEPLOYMENT name in the
        # request URL path. Deployment is per-engineer (AZURE_OPENAI_DEPLOYMENT
        # in .env), never a shared per-domain JSON value — otherwise every user
        # of a differently-named deployment gets 404 DeploymentNotFound.
        #
        # OpenAI direct / compatible gateways: use `config.model`, which
        # `load_judge_config` has already resolved as
        #   <domain>.json  →  OPENAI_MODEL/LLM_MODEL  →  default.json
        # so a domain can select its own model (§10.1) without stopping the
        # environment from naming the gateway's actual model.
        if isinstance(settings, AzureSettings):
            self._api_model = settings.deployment
            self._model_str = f"azure/{settings.deployment}"
        else:
            self._api_model = config.model
            self._model_str = config.model
        # gpt-5.x / o1-family deployments renamed `max_tokens` → `max_completion_tokens`
        # and reject `max_tokens`. We start with the modern param and fall back on rejection.
        self._max_tokens_param = "max_completion_tokens"
        self._send_seed = config.seed is not None
        self._send_temperature = True
        self._require_temp0 = config.require_temperature_zero
        # Flips to False if the deployment rejects temperature=0 and the run is
        # allowed to continue on the model default + seed. Surfaced in the run
        # manifest / scorecard.
        self.temperature_enforced = True
        self._cache = JudgeCache(
            cache_dir or Path(".judge_cache"),
            enabled=config.cache_enabled and cache_dir is not None,
        )
        self._request_sem = asyncio.Semaphore(config.concurrency)
        # Serial probe state — see `_complete()` for why this exists.
        self._probed = False
        self._probe_lock = asyncio.Lock()
        self._prompt_log_path = prompt_log_path
        self._judge_run_id = judge_run_id
        if prompt_log_path is not None:
            if not judge_run_id:
                raise ValueError(
                    "judge_run_id is required when prompt logging is enabled"
                )
            prompt_log_path.parent.mkdir(parents=True, exist_ok=True)
            # Truncate at construction so a fresh run doesn't accrete history.
            prompt_log_path.write_text("", encoding="utf-8")

        # Actual model name sent to the API — differs between providers.
        # OpenAI: real model name like `gpt-4o-mini`.
        # Azure : DEPLOYMENT name (deployment maps to a model under the hood).
        if isinstance(settings, AzureSettings):
            self._client = AsyncAzureOpenAI(
                azure_endpoint=settings.endpoint,
                api_key=settings.api_key,
                api_version=settings.api_version,
                timeout=config.timeout_s,
                max_retries=0,
            )
        elif isinstance(settings, OpenAISettings):
            self._client = AsyncOpenAI(
                api_key=settings.api_key,
                base_url=settings.base_url,
                organization=settings.organization,
                timeout=config.timeout_s,
                max_retries=0,
            )
        else:
            raise TypeError(f"unsupported settings type: {type(settings).__name__}")

    # --- public API -------------------------------------------------------

    async def judge(self, req: JudgeRequest) -> JudgeVerdict:
        key = cache_key(
            req,
            prompt_version=prompt_version(),
            model_version=self._model_str,
            mode=self._config.mode,
        )
        cached = self._cache.get(key)
        if cached is not None:
            for trace in cached.audit_trace:
                self._write_audit_record(req.question_id, trace, cached=True)
            return cached

        if self._config.mode == "per_dimension":
            verdict = await self._judge_per_dimension(req)
        else:
            verdict = await self._judge_combined(req)

        self._cache.put(key, verdict)
        return verdict

    async def aclose(self) -> None:
        await self._client.close()

    def cache_stats(self) -> dict[str, int | bool]:
        return self._cache.stats()

    # --- internals --------------------------------------------------------

    def _write_audit_record(
        self,
        question_id: str | None,
        trace: dict[str, object],
        *,
        cached: bool,
    ) -> None:
        """Write one complete Section 10.1 audit record."""
        if self._prompt_log_path is None:
            return
        import json

        record = {
            "judge_run_id": self._judge_run_id,
            "question_id": question_id,
            "mode": self._config.mode,
            "prompt_version": prompt_version(),
            "model_version": self._model_str,
            "cached": cached,
            **trace,
        }
        with self._prompt_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def _score_prompt(
        self,
        *,
        req: JudgeRequest,
        prompt: str,
        dimension: str | None,
        parser: Callable[[str], ParsedT],
    ) -> tuple[ParsedT, list[dict[str, object]]]:
        """Call, audit, validate, and retry malformed output before failing."""
        traces: list[dict[str, object]] = []
        attempts = self._config.malformed_output_retries + 1
        for attempt in range(1, attempts + 1):
            raw = await self._complete(prompt)
            trace: dict[str, object] = {
                "dimension": dimension,
                "attempt": attempt,
                "prompt": prompt,
                "raw_response": raw,
                "parse_error": None,
            }
            try:
                parsed = parser(raw)
            except JudgeOutputError as exc:
                trace["parse_error"] = f"{type(exc).__name__}: {exc}"
                traces.append(trace)
                self._write_audit_record(req.question_id, trace, cached=False)
                if attempt == attempts:
                    raise JudgeOutputError(
                        f"malformed judge output after {attempts} attempt(s): {exc}"
                    ) from exc
                continue
            traces.append(trace)
            self._write_audit_record(req.question_id, trace, cached=False)
            return parsed, traces
        raise AssertionError("unreachable malformed-output retry state")

    async def _complete(self, prompt: str) -> str:
        # Discover parameter incompatibilities (max_tokens rename, temperature
        # restriction, json_mode support) once, serially, before letting the
        # concurrent workers loose. Otherwise each worker independently trips
        # the same 400 and races on the flag mutation, producing some
        # succeed-after-retry and some failed responses depending on ordering.
        if not self._probed:
            async with self._probe_lock:
                if not self._probed:
                    await self._complete_unbounded("probe: reply with the JSON {}")
                    self._probed = True
        async with self._request_sem:
            return await self._complete_unbounded(prompt)

    def _build_kwargs(self, prompt: str) -> dict:
        kwargs: dict = {
            "model": self._api_model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._max_tokens_param == "max_tokens":
            # Old-style — the SDK signature knows this one.
            kwargs["max_tokens"] = self._config.max_tokens
        else:
            # `max_completion_tokens` is required by gpt-5.x / o1-family but the
            # pinned openai SDK doesn't accept it as a named kwarg. Route it
            # through `extra_body`, which the SDK forwards verbatim.
            kwargs["extra_body"] = {"max_completion_tokens": self._config.max_tokens}
        if self._send_temperature:
            kwargs["temperature"] = self._config.temperature
        if self._send_seed and self._config.seed is not None:
            kwargs["seed"] = self._config.seed
        if self._json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return kwargs

    async def _complete_unbounded(self, prompt: str) -> str:
        # Retry loop for well-known API parameter incompatibilities. Each branch
        # degrades permanently for this instance and retries — never a repeated
        # retry storm, and never a silent success under a wrong config.
        for _ in range(4):  # bounded compatibility negotiation, then fail
            kwargs = self._build_kwargs(prompt)
            try:
                resp = await self._create_with_backoff(kwargs)
                break
            except APIStatusError as exc:
                if self._json_mode and _is_json_mode_unsupported(exc):
                    self._json_mode = False
                    continue
                if (
                    self._max_tokens_param == "max_completion_tokens"
                    and _is_max_tokens_rename(exc)
                ):
                    self._max_tokens_param = "max_tokens"
                    continue
                if self._send_seed and _is_seed_unsupported(exc):
                    self._send_seed = False
                    warnings.warn(
                        f"model {self._model_str!r} does not support a fixed seed; "
                        "continuing under the Section 10.1 'where supported' exception",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    continue
                if self._send_temperature and _is_temperature_unsupported(exc):
                    if self._require_temp0:
                        raise RuntimeError(
                            f"model {self._model_str!r} rejected temperature=0; "
                            "§10.1 wants temperature 0 for a certifiable judge. "
                            "Use a deployment that accepts it, or set "
                            "JUDGE_REQUIRE_TEMPERATURE_ZERO=false to run on the "
                            "model default + fixed seed (recorded in the manifest)."
                        ) from exc
                    self._send_temperature = False
                    self.temperature_enforced = False
                    warnings.warn(
                        f"model {self._model_str!r} rejects temperature=0; "
                        "continuing on the model default + fixed seed. This run's "
                        "reproducibility rests on the seed, not temperature (§10.1).",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    continue
                raise
        else:
            raise RuntimeError(
                "openai_judge: exhausted parameter-compatibility retries. "
                "This should be unreachable — please report the actual APIStatusError above."
            )

        content = resp.choices[0].message.content
        if content is None:
            finish = resp.choices[0].finish_reason
            raise JudgeOutputError(
                f"judge returned no content (finish_reason={finish!r})"
            )
        return content

    async def _create_with_backoff(self, kwargs: dict):
        """Retry transient transport/rate-limit failures with bounded backoff."""
        attempts = self._config.max_retries + 1
        for attempt in range(attempts):
            try:
                return await self._client.chat.completions.create(**kwargs)
            except (APIConnectionError, APITimeoutError, APIStatusError) as exc:
                if not _is_transient(exc) or attempt == attempts - 1:
                    raise
                delay = min(
                    self._config.backoff_base_s * (2**attempt),
                    self._config.backoff_max_s,
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable backoff retry state")

    async def _judge_combined(self, req: JudgeRequest) -> JudgeVerdict:
        prompt = render_combined(req)
        parsed, traces = await self._score_prompt(
            req=req,
            prompt=prompt,
            dimension=None,
            parser=parse_combined,
        )
        return JudgeVerdict(
            **parsed,
            prompt_version=prompt_version(),
            model_version=self._model_str,
            audit_trace=traces,
            temperature_enforced=self.temperature_enforced,
        )

    async def _judge_per_dimension(self, req: JudgeRequest) -> JudgeVerdict:
        """Four independent calls — no halo effect between dimensions."""
        prompts = {d: render_dimension(req, d) for d in DIMENSIONS}

        async def score_dimension(dimension: str):
            return await self._score_prompt(
                req=req,
                prompt=prompts[dimension],
                dimension=dimension,
                parser=lambda raw: parse_dimension(raw, dimension),
            )

        parsed_dimensions = await asyncio.gather(
            *(score_dimension(dimension) for dimension in DIMENSIONS)
        )
        scores: dict[str, int] = {}
        rationales: dict[str, str] = {}
        traces: list[dict[str, object]] = []
        for dimension, ((score, rationale), dimension_traces) in zip(
            DIMENSIONS, parsed_dimensions, strict=True
        ):
            scores[dimension] = score
            rationales[dimension] = rationale
            traces.extend(dimension_traces)
        return JudgeVerdict(
            dimension_rationales=rationales,
            **scores,
            prompt_version=prompt_version(),
            model_version=self._model_str,
            audit_trace=traces,
            temperature_enforced=self.temperature_enforced,
        )


def _error_body_contains(exc: APIStatusError, *needles: str) -> bool:
    """Case-insensitive check across the structured body and the message."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            hay = " ".join(
                str(error.get(k) or "") for k in ("param", "message", "code")
            ).lower()
            if any(n.lower() in hay for n in needles):
                return True
    try:
        if any(n.lower() in str(exc.response.json()).lower() for n in needles):
            return True
    except Exception:  # noqa: BLE001
        pass
    blob = f"{getattr(exc, 'message', '') or ''} {exc}".lower()
    return any(n.lower() in blob for n in needles)


def _is_max_tokens_rename(exc: APIStatusError) -> bool:
    """gpt-5.x / o1 family rejects `max_tokens` and requires `max_completion_tokens`."""
    if exc.status_code != 400:
        return False
    return _error_body_contains(exc, "max_tokens", "max_completion_tokens")


def _is_temperature_unsupported(exc: APIStatusError) -> bool:
    """Some reasoning models restrict `temperature` to the default value."""
    if exc.status_code != 400:
        return False
    return _error_body_contains(exc, "temperature")


def _is_seed_unsupported(exc: APIStatusError) -> bool:
    """Providers may omit seed support; Section 10.1 permits that exception."""
    if exc.status_code != 400:
        return False
    return _error_body_contains(exc, "seed")


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    return isinstance(exc, APIStatusError) and (
        exc.status_code == 429 or exc.status_code >= 500
    )


def _is_json_mode_unsupported(exc: APIStatusError) -> bool:
    """Distinguish 'this model doesn't support response_format' from real errors."""
    if exc.status_code not in (400, 404):
        return False

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            if "response_format" in str(error.get("param") or ""):
                return True
            if "response_format" in str(error.get("message") or "").lower():
                return True

    try:
        if "response_format" in str(exc.response.json()).lower():
            return True
    except Exception:  # noqa: BLE001 — body may be empty or non-JSON
        pass

    blob = f"{getattr(exc, 'message', '') or ''} {exc}".lower()
    return "response_format" in blob or "json_object" in blob
