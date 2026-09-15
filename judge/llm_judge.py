"""Provider-independent scaffolding shared by every LLM-backed judge.

Everything a judge does *around* the model call is identical whoever serves the
model: content-keyed caching, the Section 10.1 audit record, malformed-output
retries, and the combined / per_dimension scoring modes. Only the call itself
differs — OpenAI chat completions, or Anthropic messages through Floodgate.

Subclasses implement :meth:`BaseLLMJudge._complete` and :meth:`aclose`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from judge.cache import JudgeCache, cache_key
from judge.client import JudgeClient
from judge.config import JudgeConfig
from judge.contracts import DIMENSIONS, JudgeRequest, JudgeVerdict
from judge.parsing import JudgeOutputError, parse_combined, parse_dimension
from judge.prompts import prompt_version, render_combined, render_dimension

ParsedT = TypeVar("ParsedT")


class BaseLLMJudge(JudgeClient):
    def __init__(
        self,
        config: JudgeConfig,
        *,
        model_str: str,
        cache_dir: Path | None = None,
        prompt_log_path: Path | None = None,
        judge_run_id: str | None = None,
    ) -> None:
        self._config = config
        # The string that identifies this judge in verdicts, cache keys and the
        # run manifest. Must distinguish deployments and proxies, not just
        # models — two runs through different gateways are not interchangeable.
        self._model_str = model_str
        self._cache = JudgeCache(
            cache_dir or Path(".judge_cache"),
            enabled=config.cache_enabled and cache_dir is not None,
        )
        # Flips to False if the provider rejects temperature=0 and the run is
        # allowed to continue on the model default + seed. Surfaced in the run
        # manifest / scorecard.
        self.temperature_enforced = True
        # Flips to False when a configured seed never reaches the provider.
        # Subclasses set it: OpenAI when a deployment rejects `seed`, Floodgate
        # unconditionally because Anthropic has no seed parameter at all.
        self.seed_enforced = True
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

    def cache_stats(self) -> dict[str, int | bool]:
        return self._cache.stats()

    # --- the one thing providers differ on --------------------------------

    async def _complete(self, prompt: str) -> str:
        raise NotImplementedError

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
            seed_enforced=self.seed_enforced,
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
            seed_enforced=self.seed_enforced,
        )
