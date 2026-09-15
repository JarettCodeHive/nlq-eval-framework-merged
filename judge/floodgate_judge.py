"""LLM judge over Anthropic models served through Apple's Floodgate proxy.

Floodgate is Apple's mandatory app-level security proxy for externally-hosted
models: every request goes to `floodgate.g.apple.com`, never to
`api.anthropic.com`. This judge speaks Floodgate's **native Anthropic** interface
(`/api/anthropic`), not its OpenAI-compatible one — the compat layer is an
explicit lowest-common-denominator translation that drops prompt caching for
Anthropic models and rejects `response_format`.

Three Anthropic facts shape the class, and all three differ from `OpenAIJudge`:

* **No `seed`.** Determinism (§10.1) rests on `temperature=0` alone. The seed in
  `JudgeConfig` is not silently dropped — it warns, because a verdict that
  claims a determinism control it never applied is worse than a noisy run.
* **No JSON mode, and prefill is not guaranteed either.** Strict JSON is forced
  by prefilling the assistant turn with `{` — but Floodgate routes between AWS
  Bedrock and GCP Vertex on its own, and Vertex-served Claude rejects prefill.
  The judge negotiates it away when that happens and falls back on the prompt
  plus `malformed_output_retries`. Either way `judge.parsing` stays strict and
  repairs nothing.
* **Some models reject `temperature` outright.** The adaptive-thinking models —
  Sonnet 5, Opus 4.7 and later — answer `ValidationException: 'temperature' is
  deprecated for this model` rather than restricting its range. Since there is
  no `seed` to fall back on, dropping it leaves the run with *no* determinism
  control, so by default that is a hard failure; `require_temperature_zero=False`
  accepts it and records `temperature_enforced=False` in the manifest. Prefer a
  model that accepts temperature for a judge: `anthropic.claude-sonnet-4-6`, or
  `anthropic.claude-haiku-4-5-20251001-v1:0` for cheap high volume.

Policy, briefly, because it constrains what this judge may be pointed at:
Anthropic models through Floodgate are **internal-use only**, must not receive
InfoSec Tier 0/1 data, and since 2026-05-14 may not be used for distillation or
synthetic data generation. Scoring answers is evaluation and is fine; generating
the evaluation dataset with the same models is not.
"""

from __future__ import annotations

import asyncio
import random
import subprocess
import uuid
import warnings
from pathlib import Path

from judge.config import (
    FloodgateNarrativeSettings,
    FloodgateOIDCSettings,
    FloodgateSettings,
    JudgeConfig,
    MissingCredentials,
)
from judge.llm_judge import BaseLLMJudge
from judge.parsing import JudgeOutputError

# Floodgate's AppleConnect OAuth client id. Not a secret — it is published in
# the Floodgate auth guide and is identical for every caller.
FLOODGATE_OAUTH_CLIENT_ID = "hvys3fcwcteqrvw3qzkvtk86viuoqv"

# Re-mint the OIDC token this often rather than waiting for a 401. Floodgate
# tokens outlive this comfortably; the ceiling exists so a long run never coasts
# on a token that expires between two questions.
TOKEN_TTL_S = 600


def appleconnect_token(
    appleconnect_path: str = "/usr/local/bin/appleconnect",
    *,
    timeout_s: int = 60,
) -> str:
    """Mint a Floodgate-audience OIDC token via the AppleConnect CLI."""
    argv = [
        appleconnect_path,
        "getToken",
        "-C",
        FLOODGATE_OAUTH_CLIENT_ID,
        "--token-type=oauth",
        "--interactivity-type=none",
        "-E",
        "prod",
        "-G",
        "pkce",
        "-o",
        "openid,dsid,accountname,groups",
    ]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except FileNotFoundError as exc:
        raise MissingCredentials(
            f"AppleConnect CLI not found at {appleconnect_path!r}. OIDC auth needs a "
            "local Mac with AppleConnect installed; set FLOODGATE_NARRATIVE_CERT and "
            "FLOODGATE_NARRATIVE_KEY for servers and CI."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MissingCredentials(
            f"appleconnect getToken timed out after {timeout_s}s — are you signed in?"
        ) from exc

    if proc.returncode != 0:
        raise MissingCredentials(
            f"appleconnect getToken failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[:400]}"
        )

    # Output is `<key> <value>` lines; the token line is `id-token` on current
    # CLI versions and `oauth-id-token` on older ones. Take the last match.
    for line in reversed(proc.stdout.splitlines()):
        if "id-token" in line and len(line.split()) >= 2:
            return line.split()[-1]
    raise MissingCredentials(
        "appleconnect getToken returned no id-token line; run it by hand to see why"
    )


class FloodgateCallError(RuntimeError):
    """A Floodgate call that will not be retried at the transport layer.

    Carries the status code so the caller can distinguish a parameter
    incompatibility worth renegotiating from a genuine failure.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    def mentions(self, needle: str) -> bool:
        """Whether the proxy's error text names a request parameter."""
        return self.status_code == 400 and needle.lower() in str(self).lower()


class FloodgateJudge(BaseLLMJudge):
    """Judge backed by Anthropic Messages through Floodgate."""

    def __init__(
        self,
        settings: FloodgateSettings,
        config: JudgeConfig,
        *,
        cache_dir: Path | None = None,
        prompt_log_path: Path | None = None,
        judge_run_id: str | None = None,
        token_provider=None,
    ) -> None:
        import httpx
        from anthropic import AsyncAnthropic

        if not isinstance(
            settings, (FloodgateOIDCSettings, FloodgateNarrativeSettings)
        ):
            raise TypeError(f"unsupported settings type: {type(settings).__name__}")

        _validate_model(config.model)
        super().__init__(
            config,
            # The proxy is part of the identity of a run: the same model id
            # through a different gateway is not an interchangeable measurement,
            # and this string keys the cache and stamps every verdict.
            model_str=f"floodgate/{config.model}",
            cache_dir=cache_dir,
            prompt_log_path=prompt_log_path,
            judge_run_id=judge_run_id,
        )
        self._settings = settings
        self._api_model = config.model
        self._prefill_json = config.json_mode
        self._request_sem = asyncio.Semaphore(config.concurrency)
        # Most Anthropic models accept temperature=0, but the adaptive-thinking
        # ones (Sonnet 5, Opus 4.7+) reject `temperature` outright on Bedrock:
        # "ValidationException: `temperature` is deprecated for this model".
        # Discovered once, serially, then degraded permanently — same shape as
        # OpenAIJudge's parameter negotiation.
        self._send_temperature = True
        self._require_temp0 = config.require_temperature_zero
        self._probed = False
        self._probe_lock = asyncio.Lock()

        # Anthropic has no `seed`, so a config carrying one would otherwise
        # imply a determinism control that was never sent. Worth being loud
        # about: if this model also rejects temperature, the run has *no*
        # determinism control at all, and `temperature_enforced=false` in the
        # manifest is the only thing recording that.
        if config.seed is not None:
            self.seed_enforced = False
            warnings.warn(
                "Anthropic has no `seed` parameter, so JudgeConfig.seed is ignored on "
                "Floodgate. This run's reproducibility rests on temperature=0 alone "
                "(§10.1 'where supported'). Set seed=null in the domain config to "
                "make that explicit.",
                RuntimeWarning,
                stacklevel=2,
            )

        mtls = isinstance(settings, FloodgateNarrativeSettings)

        async def _fix_headers(request) -> None:
            # Floodgate does not accept `x-api-key`, and the Anthropic SDK adds
            # it from ANTHROPIC_API_KEY whenever that is in the environment.
            request.headers.pop("x-api-key", None)
            if mtls:
                # The certificate is the identity; a stray bearer confuses it.
                request.headers.pop("authorization", None)

        verify = settings.httpx_verify()
        if verify is True:
            # Corp machines TLS-inspect with a private root CA that only the OS
            # store knows about — the same reason pulse_client.py does this.
            try:
                import truststore

                truststore.inject_into_ssl()
            except ImportError:
                pass

        self._http = httpx.AsyncClient(
            timeout=config.timeout_s,
            verify=verify,
            event_hooks={"request": [_fix_headers]},
            **(
                {"cert": (settings.cert_path, settings.key_path)}
                if isinstance(settings, FloodgateNarrativeSettings)
                else {}
            ),
        )
        self._client = AsyncAnthropic(
            base_url=settings.base_url,
            # Placeholder. The real bearer is attached per request so a
            # short-lived OIDC token can be re-minted mid-run; on the mTLS path
            # the hook above strips it entirely.
            auth_token="floodgate",
            http_client=self._http,
            max_retries=0,  # we do our own bounded backoff, with jitter
        )

        self._token_provider = token_provider
        if self._token_provider is None and isinstance(settings, FloodgateOIDCSettings):
            self._token_provider = lambda: appleconnect_token(
                settings.appleconnect_path, timeout_s=config.timeout_s
            )
        self._token: str | None = None
        self._token_minted_at = 0.0
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.close()

    # --- internals --------------------------------------------------------

    async def _token_value(self, *, force_refresh: bool = False) -> str | None:
        if self._token_provider is None:  # mTLS: no bearer at all
            return None
        async with self._token_lock:
            now = asyncio.get_running_loop().time()
            if (
                force_refresh
                or self._token is None
                or now - self._token_minted_at >= TOKEN_TTL_S
            ):
                self._token = await asyncio.to_thread(self._token_provider)
                self._token_minted_at = now
            return self._token

    async def _headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        s = self._settings
        headers = {
            "User-Agent": s.user_agent,
            # Unique per request so the Floodgate team can trace one failure
            # rather than a whole retry storm.
            "X-Client-Request-Id": str(uuid.uuid4()),
        }
        if s.project_token:
            headers["X-Floodgate-Project-Token"] = s.project_token
        token = await self._token_value(force_refresh=force_refresh)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _build_messages(self, prompt: str) -> list[dict]:
        messages: list[dict] = [{"role": "user", "content": prompt}]
        if self._prefill_json:
            # Anthropic has no JSON mode. Prefilling the assistant turn with `{`
            # makes the model continue a JSON object rather than open with prose,
            # which judge.parsing correctly refuses to score.
            messages.append({"role": "assistant", "content": "{"})
        return messages

    async def _complete(self, prompt: str) -> str:
        # Discover the temperature incompatibility once, serially, before the
        # concurrent workers start. Otherwise every worker independently trips
        # the same 400 and races on the flag — some succeed after retry, some
        # fail, depending on ordering. Same reasoning as OpenAIJudge.
        if not self._probed:
            async with self._probe_lock:
                if not self._probed:
                    await self._complete_unbounded("probe: reply with the JSON {}")
                    self._probed = True
        async with self._request_sem:
            return await self._complete_unbounded(prompt)

    async def _complete_unbounded(self, prompt: str) -> str:
        for _ in range(3):  # bounded negotiation: temperature, prefill, give up
            try:
                resp = await self._create_with_backoff(prompt)
                break
            except FloodgateCallError as exc:
                if self._send_temperature and exc.mentions("temperature"):
                    self._degrade_temperature(exc)
                    continue
                if self._prefill_json and exc.mentions("prefill"):
                    self._degrade_prefill()
                    continue
                raise
        else:  # pragma: no cover - the loop above always breaks or raises
            raise RuntimeError(
                "floodgate judge: exhausted parameter-compatibility negotiation"
            )

        blocks = [b for b in resp.content if getattr(b, "type", None) == "text"]
        if not blocks:
            raise JudgeOutputError(
                f"judge returned no text content (stop_reason={resp.stop_reason!r})"
            )
        if resp.stop_reason == "max_tokens":
            # Distinguished from bad JSON deliberately: retrying malformed output
            # against a too-small budget just burns quota three times.
            raise RuntimeError(
                f"floodgate judge response was truncated at max_tokens="
                f"{self._config.max_tokens}, so its JSON is incomplete. Raise "
                "max_tokens in the domain config."
            )
        text = "".join(b.text for b in blocks)
        return "{" + text if self._prefill_json else text

    def _degrade_temperature(self, exc: "FloodgateCallError") -> None:
        """Handle a model that refuses `temperature` at all.

        Anthropic's adaptive-thinking models (Sonnet 5, Opus 4.7+) reject the
        parameter rather than restricting its range. Combined with the absence
        of `seed`, dropping it leaves the run with **no determinism control
        whatsoever** — which is why this is loud and why the verdict carries
        `temperature_enforced=False` all the way into the manifest.
        """
        if self._require_temp0:
            raise RuntimeError(
                f"model {self._model_str!r} rejects `temperature` entirely "
                f"({exc}). §10.1 wants temperature 0 for a certifiable judge, and "
                "Anthropic has no `seed` to fall back on — dropping it would leave "
                "this run with no determinism control at all. Use a model that "
                "accepts temperature (e.g. anthropic.claude-sonnet-4-6, "
                "anthropic.claude-haiku-4-5-20251001-v1:0), or set "
                "JUDGE_REQUIRE_TEMPERATURE_ZERO=false to accept a non-deterministic "
                "judge (recorded in the manifest)."
            ) from exc
        self._send_temperature = False
        self.temperature_enforced = False
        warnings.warn(
            f"model {self._model_str!r} rejects `temperature`; continuing without it. "
            "Anthropic has no `seed` either, so this run has NO determinism control "
            "— repeated runs may score differently. The scorecard records "
            "judge_temperature_enforced=false.",
            RuntimeWarning,
            stacklevel=2,
        )

    def _degrade_prefill(self) -> None:
        """Handle a backend that refuses an assistant prefill turn.

        Floodgate routes between AWS Bedrock and GCP Vertex on its own and gives
        you no way to pin a provider — and Vertex-served Claude rejects prefill
        outright ("the conversation must end with a user message"). So prefill is
        an optimisation, never a guarantee: without it the prompt's own
        instruction to emit JSON does the work, backed by the strict parser and
        `malformed_output_retries`. Unlike temperature this costs no determinism,
        only a slightly higher chance of a retry.
        """
        self._prefill_json = False
        warnings.warn(
            f"model {self._model_str!r} was routed to a backend that rejects "
            "assistant prefill; continuing without it. Strict JSON now rests on the "
            "prompt plus malformed_output_retries rather than a forced open brace.",
            RuntimeWarning,
            stacklevel=2,
        )

    def _build_kwargs(self, prompt: str) -> dict:
        kwargs: dict = {
            "model": self._api_model,
            "max_tokens": self._config.max_tokens,
            "messages": self._build_messages(prompt),
        }
        if self._send_temperature:
            kwargs["temperature"] = self._config.temperature
        return kwargs

    async def _create_with_backoff(self, prompt: str):
        from anthropic import APIConnectionError, APIStatusError, APITimeoutError

        attempts = self._config.max_retries + 1
        refreshed = False
        for attempt in range(attempts):
            try:
                return await self._client.messages.create(
                    **self._build_kwargs(prompt),
                    extra_headers=await self._headers(),
                )
            except (APIConnectionError, APITimeoutError, APIStatusError) as exc:
                status = getattr(exc, "status_code", None)

                # An expired OIDC token looks like any other 401. Re-mint once
                # and retry immediately rather than spending the backoff budget.
                if status == 401 and self._token_provider is not None and not refreshed:
                    refreshed = True
                    await self._token_value(force_refresh=True)
                    continue

                # 429 is our rate limit or budget; 529 is the provider's capacity,
                # shared across Apple. Both are retryable and neither consumes
                # quota when rejected.
                transient = isinstance(exc, (APIConnectionError, APITimeoutError)) or (
                    status is not None and (status == 429 or status >= 500)
                )
                if not transient or attempt == attempts - 1:
                    raise FloodgateCallError(
                        explain_floodgate_error(exc, status), status
                    ) from exc
                await asyncio.sleep(self._backoff_delay(exc, attempt))
        raise AssertionError("unreachable backoff retry state")

    def _backoff_delay(self, exc: Exception, attempt: int) -> float:
        """Exponential backoff with jitter, honouring `retry-after`.

        Floodgate blocks clients that retry hard without jitter — two of them
        destabilised the service in June 2026 — so this is not optional.
        """
        retry_after = None
        response = getattr(exc, "response", None)
        if response is not None:
            raw = response.headers.get("retry-after")
            if raw:
                try:
                    retry_after = float(raw)
                except ValueError:
                    retry_after = None
        base = (
            retry_after
            if retry_after is not None
            else self._config.backoff_base_s * (2**attempt)
        )
        return min(base, self._config.backoff_max_s) * random.uniform(0.5, 1.0)


def _validate_model(model: str) -> None:
    """Catch a non-Anthropic model id before it becomes an opaque 400.

    `config/judge/default.json` ships an OpenAI model, and a Floodgate run that
    forgets FLOODGATE_MODEL would otherwise send `gpt-4o-mini` to the Anthropic
    endpoint.
    """
    if model.startswith(("aws:", "gcp:", "openai:")):
        raise ValueError(
            f"model {model!r} carries a provider prefix. That form belongs to "
            "Floodgate's OpenAI-compatible endpoint; the Anthropic endpoint takes "
            f"the bare id, e.g. {model.split(':', 1)[1]!r}."
        )
    if not model.startswith("anthropic."):
        raise ValueError(
            f"model {model!r} is not an Anthropic model id, so Floodgate's Anthropic "
            "endpoint will reject it. Set FLOODGATE_MODEL (e.g. "
            "anthropic.claude-sonnet-5, or anthropic.claude-haiku-4-5-20251001-v1:0 "
            "for cheap high-volume scoring), or set `model` in the domain config."
        )


def explain_floodgate_error(exc: Exception, status: int | None) -> str:
    """Attach Floodgate's reading of a status code, which often differs from
    what the same code means from Anthropic directly."""
    hints = {
        401: "not onboarded, or the OIDC token expired or lacks Floodgate in its "
        "`aud` claim. Onboard at genai.apple.com/profile/onboarding — it is per "
        "model family, and a system account does not inherit your onboarding.",
        403: "access denied by an org or legal restriction.",
        429: "rate limit or daily budget. Personal budget resets at 5 AM local time; "
        "set FLOODGATE_PROJECT_TOKEN to spend against a project instead.",
        451: "content filter — the request resembled Designated Critical code. "
        "Dispute via `SEAR Floodgate | Content Filter` with the request id.",
        499: "the client gave up first. Raise timeout_s in the domain config.",
        529: "upstream provider capacity, shared across Apple. More quota will not "
        "help; retry later or use a model with provisioned capacity.",
    }
    hint = hints.get(status or 0)
    return f"floodgate judge call failed ({status}): {exc}" + (
        f" — {hint}" if hint else ""
    )
