"""Judge configuration — provider-agnostic (OpenAI direct, Azure OpenAI, or
Anthropic via Apple's Floodgate proxy) plus per-domain JSON configs.

Provider selection is auto-detected:
- If `LLM_PROVIDER=floodgate`, or a Floodgate credential is present → Floodgate.
- Else if `AZURE_OPENAI_ENDPOINT` is set → use Azure OpenAI (`AsyncAzureOpenAI`).
- Else if `OPENAI_API_KEY` is set → use OpenAI direct (`AsyncOpenAI`).
- Else → `MissingCredentials` with an actionable message.

The `LLM_PROVIDER` env var can force selection: `azure`, `openai`, or `floodgate`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal, Union

from dotenv import load_dotenv
from pydantic import BaseModel, Field

MODULE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = MODULE_ROOT.parent
# Judge configs live at config/judge/ per Jarett's convention (parallel to
# config/generation/, config/scorecard/, config/orchestrator/). Per-domain
# files carry only overrides; every field falls through to default.json.
CONFIGS_DIR = REPO_ROOT / "config" / "judge"
DEFAULT_DOMAIN_CONFIG = "default"

# Candidate env files, tried in order. First existing file wins. `.env` is the
# canonical name; the others exist so a user who dropped creds into a
# differently-named file (e.g. `aurecreds.env`) doesn't have to rename it.
_ENV_CANDIDATES: tuple[str, ...] = (".env", "aurecreds.env", "azurecreds.env")

_TRUST_STORE_STATE: dict[str, bool] = {}


def trust_os_ca_store() -> bool:
    """Make Python trust the OS certificate store. Returns True if it took.

    Every machine running this is corporate-managed, so the TLS-inspection proxy's
    root CA is installed in the OS trust store by MDM — and `certifi`, which
    Python uses by default, has never heard of it. `truststore` bridges that gap,
    which is why no certificate has to be exported or configured by hand.

    A missing `truststore` is therefore always a broken install on this fleet, not
    a supported configuration. It used to be swallowed in three separate places,
    so the symptom surfaced later as CERTIFICATE_VERIFY_FAILED from whichever
    request happened to run first — which reads like a platform outage. Say it
    once, plainly, at the point the fallback happens.

    Idempotent and safe to call repeatedly; the warning is emitted once.
    """

    if "ok" in _TRUST_STORE_STATE:
        return _TRUST_STORE_STATE["ok"]
    try:
        import truststore

        truststore.inject_into_ssl()
        _TRUST_STORE_STATE["ok"] = True
    except ImportError:
        print(
            "[judge] WARNING: `truststore` is not installed, so TLS will be "
            "verified against certifi and will FAIL behind a corporate "
            "inspection proxy. Fix with `pip install -r requirements.txt`, or set "
            "PULSE_CA_BUNDLE / FLOODGATE_CA_BUNDLE to the corp CA as a fallback.",
            file=sys.stderr,
        )
        _TRUST_STORE_STATE["ok"] = False
    return _TRUST_STORE_STATE["ok"]


class MissingCredentials(RuntimeError):
    """Raised with an actionable message rather than letting the SDK fail opaquely."""


class OpenAISettings(BaseModel):
    """OpenAI direct API settings."""

    provider: Literal["openai"] = "openai"
    api_key: str
    model: str = "gpt-4o-mini"
    base_url: str | None = None
    organization: str | None = None

    @property
    def redacted(self) -> dict[str, str | None]:
        """Safe to write into a run manifest."""
        return {
            "provider": "openai",
            "model": self.model,
            "base_url": self.base_url,
            "organization": self.organization,
        }


class AzureSettings(BaseModel):
    """Azure OpenAI settings.

    Azure calls the model a `deployment`. The rest of the app uses `model` as a
    display string so scorecard rows can be compared across providers.
    """

    provider: Literal["azure"] = "azure"
    endpoint: str
    api_key: str
    api_version: str
    deployment: str

    @property
    def model(self) -> str:
        """Uniform accessor across providers — used as `model_version` in verdicts."""
        return f"azure/{self.deployment}"

    @property
    def redacted(self) -> dict[str, str]:
        return {
            "provider": "azure",
            "endpoint_host": self.endpoint.split("//")[-1].split("/")[0],
            "api_version": self.api_version,
            "deployment": self.deployment,
        }


class _FloodgateSettings(BaseModel):
    """Common half of the two Floodgate credential shapes.

    Floodgate is Apple's mandatory proxy for externally-hosted models: Anthropic
    traffic goes to floodgate.g.apple.com, never api.anthropic.com. We use its
    *native* Anthropic interface rather than its OpenAI-compatible one, because
    the compat layer silently drops prompt caching for Anthropic models and does
    not accept `response_format`.
    """

    provider: Literal["floodgate"] = "floodgate"
    # The Anthropic SDK appends `/v1/messages`, so the base URL stops here.
    base_url: str = "https://floodgate.g.apple.com/api/anthropic"
    # Deliberately NOT the frontier pair. Sonnet 5 and Opus 5 use adaptive
    # thinking and reject `temperature` outright through Floodgate
    # ("ValidationException: `temperature` is deprecated for this model"), and
    # Anthropic has no `seed` to fall back on — so they leave a judge with no
    # determinism control at all. Sonnet 4.6 and Haiku 4.5 accept temperature=0,
    # which is what §10.1 asks for. Verified against Floodgate on 2026-09-09.
    model: str = "anthropic.claude-sonnet-4-6"
    # Floodgate treats User-Agent as mandatory — it is how spend is attributed
    # per tool in the quota dashboards.
    user_agent: str = "nlq-judge/1.0"
    # Spend against a project budget rather than the caller's personal daily
    # quota. This is the project *token* (a secret), not the project id (a UUID).
    # A full evaluation run is exactly the non-interactive, high-volume workload
    # Floodgate projects exist for.
    project_token: str | None = None
    # TLS: corp machines TLS-inspect with a private root CA that only the OS
    # store knows about. Same escape hatches as the Pulse client.
    verify_tls: bool = True
    ca_bundle: str | None = None  # path to a CA file; wins over verify_tls

    def httpx_verify(self) -> "str | bool":
        return self.ca_bundle if self.ca_bundle else self.verify_tls


class FloodgateOIDCSettings(_FloodgateSettings):
    """AppleConnect bearer token. Local Macs with the AppleConnect CLI only."""

    auth: Literal["oidc"] = "oidc"
    appleconnect_path: str = "/usr/local/bin/appleconnect"

    @property
    def redacted(self) -> dict[str, str | None]:
        return {
            "provider": "floodgate",
            "auth": "appleconnect-oidc",
            "base_url": self.base_url,
            "model": self.model,
            "user_agent": self.user_agent,
            "project_token_set": str(bool(self.project_token)),
        }


class FloodgateNarrativeSettings(_FloodgateSettings):
    """Narrative mTLS certificate for a system account. CI and unattended runs.

    The certificate is the identity, so no bearer token is sent. Paths are
    platform-specific — `/tls/tls.crt` + `/tls/tls.key` on Kubernetes,
    `$BOLT_NARRATIVE_DIR/turi/{chain,private}.pem` on Bolt.
    """

    auth: Literal["narrative"] = "narrative"
    cert_path: str
    key_path: str

    @property
    def redacted(self) -> dict[str, str | None]:
        return {
            "provider": "floodgate",
            "auth": "narrative-mtls",
            "base_url": self.base_url,
            "model": self.model,
            "user_agent": self.user_agent,
            "cert_path": self.cert_path,
            "project_token_set": str(bool(self.project_token)),
        }


FloodgateSettings = Union[FloodgateOIDCSettings, FloodgateNarrativeSettings]
LLMSettings = Union[OpenAISettings, AzureSettings, FloodgateSettings]


class JudgeConfig(BaseModel):
    model: str
    temperature: float = 0.0
    seed: int | None = 42
    max_tokens: int = 1024
    timeout_s: int = 60
    max_retries: int = 3
    backoff_base_s: float = Field(default=0.5, gt=0)
    backoff_max_s: float = Field(default=8.0, gt=0)
    malformed_output_retries: int = Field(default=2, ge=0)
    concurrency: int = Field(default=4, ge=1)
    mode: str = "combined"  # combined | per_dimension
    json_mode: bool = True
    cache_enabled: bool = True
    # Where a scoring run writes its artifacts, mirroring how the dataset and
    # Q&A stages resolve their own release paths from config. `{domain}` is
    # interpolated; the path is relative to the repository root. Run outputs are
    # per-run and append-only, unlike the sealed single-version dataset and Q&A
    # packages that sit beside them under release/.
    run_output_root: str = "release/{domain}/eval-runs"
    # §11.1 version tag for the platform under evaluation. Empty by default and
    # overridden by PLATFORM_VERSION or --platform-version — unlike the model,
    # this describes whichever deployment a machine is pointed at, so the
    # environment outranks the checked-in file. A --release run still refuses
    # when all three are empty.
    platform_version: str = ""
    # §10.1 wants temperature 0. Some deployments (GPT-5 / o1 family) only allow
    # the default temperature. When False, such a rejection degrades to
    # "model default + fixed seed" with a loud warning instead of failing the
    # run — determinism then rests on the seed. Set JUDGE_REQUIRE_TEMPERATURE_ZERO
    # =false to opt in; the scorecard records which mode a run used.
    require_temperature_zero: bool = True


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_judge_config(domain: str, config_dir: Path | None = None) -> JudgeConfig:
    """Merge `default.json` with the `<domain>.json` override.

    Model selection precedence (§10.1 requires the per-domain JSON to be able to
    select the model, but the environment has to stay usable for a shared
    gateway/deployment):

      1. `model` set in `<domain>.json`  — an explicit per-domain choice wins
      2. FLOODGATE_MODEL / OPENAI_MODEL / LLM_MODEL — the environment's model
      3. `model` in `default.json`       — the project-wide default

    On Azure this value is display-only: the SDK routes on the DEPLOYMENT name
    from `AZURE_OPENAI_DEPLOYMENT`, which is per-engineer and must never come
    from shared JSON, or everyone with a differently-named deployment gets a
    404 DeploymentNotFound.

    On Floodgate the model is an Anthropic id with no provider prefix, e.g.
    `anthropic.claude-sonnet-5`. `FloodgateJudge` rejects anything that isn't,
    so a `default.json` model leaking into a Floodgate run fails at construction
    rather than as an opaque 400 from the proxy.
    """
    root = config_dir or CONFIGS_DIR
    base = _load_json(root / f"{DEFAULT_DOMAIN_CONFIG}.json")
    override = _load_json(root / f"{domain}.json")
    merged = {**base, **override}

    env_model = (
        os.getenv("FLOODGATE_MODEL")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("LLM_MODEL")
        or ""
    ).strip()
    merged["model"] = override.get("model") or env_model or base.get("model")

    if "require_temperature_zero" not in merged:
        raw = (os.getenv("JUDGE_REQUIRE_TEMPERATURE_ZERO") or "true").strip().lower()
        merged["require_temperature_zero"] = raw not in ("0", "false", "no", "off")
    return JudgeConfig(**merged)


def load_env(env_file: Path | None = None) -> None:
    """Load the first existing candidate env file. Explicit `env_file` wins.

    Shared by `load_llm_settings` and `judge.pulse_client.load_pulse_settings`
    so both read the same `judge/.env`.

    `override=True` is deliberate: a stale `OPENAI_API_KEY` (or similar) inherited
    from the shell will silently take precedence over the .env file otherwise, and
    the module will happily send the WRONG key to Azure and get a 401. The .env
    file is authoritative for judge creds — that's the whole point of dropping
    them there.
    """
    if env_file is not None:
        load_dotenv(env_file, override=True)
        return
    for name in _ENV_CANDIDATES:
        p = MODULE_ROOT / name
        if p.is_file():
            load_dotenv(p, override=True)
            return
    # No candidate file — rely purely on the process environment.


def _detect_provider() -> Literal["openai", "azure", "floodgate"]:
    forced = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if forced in ("openai", "azure", "floodgate"):
        return forced  # type: ignore[return-value]
    # A Narrative certificate or a project token is only ever set for Floodgate,
    # so its presence is unambiguous. An OIDC run has no distinguishing env var
    # — that one has to be forced with LLM_PROVIDER=floodgate.
    if os.getenv("FLOODGATE_NARRATIVE_CERT") or os.getenv("FLOODGATE_PROJECT_TOKEN"):
        return "floodgate"
    if os.getenv("AZURE_OPENAI_ENDPOINT"):
        return "azure"
    return "openai"


def _load_floodgate_settings() -> FloodgateSettings:
    """Build Floodgate settings from the environment.

    A certificate pair means an unattended run (CI, a pod, a Bolt task);
    otherwise fall back to an AppleConnect token on a developer's Mac.
    """
    common: dict[str, object] = {}
    for key, env in (
        ("base_url", "FLOODGATE_BASE_URL"),
        ("model", "FLOODGATE_MODEL"),
        ("user_agent", "FLOODGATE_USER_AGENT"),
        ("project_token", "FLOODGATE_PROJECT_TOKEN"),
        ("ca_bundle", "FLOODGATE_CA_BUNDLE"),
    ):
        value = (os.getenv(env) or "").strip()
        if value:
            common[key] = value
    raw_verify = (os.getenv("FLOODGATE_VERIFY_TLS") or "").strip().lower()
    if raw_verify:
        common["verify_tls"] = raw_verify not in ("0", "false", "no", "off")

    cert = (os.getenv("FLOODGATE_NARRATIVE_CERT") or "").strip()
    key = (os.getenv("FLOODGATE_NARRATIVE_KEY") or "").strip()
    if cert or key:
        if not (cert and key):
            raise MissingCredentials(
                "Floodgate mTLS needs both FLOODGATE_NARRATIVE_CERT and "
                "FLOODGATE_NARRATIVE_KEY; only one is set. On Kubernetes these are "
                "/tls/tls.crt and /tls/tls.key."
            )
        return FloodgateNarrativeSettings(cert_path=cert, key_path=key, **common)

    appleconnect = (
        os.getenv("FLOODGATE_APPLECONNECT") or "/usr/local/bin/appleconnect"
    ).strip()
    if not Path(appleconnect).is_file():
        raise MissingCredentials(
            f"LLM_PROVIDER=floodgate but the AppleConnect CLI is not at {appleconnect!r}.\n"
            "OIDC auth needs a local Mac with AppleConnect installed. For servers and "
            "CI, set FLOODGATE_NARRATIVE_CERT and FLOODGATE_NARRATIVE_KEY instead, or "
            "point FLOODGATE_APPLECONNECT at the binary."
        )
    return FloodgateOIDCSettings(appleconnect_path=appleconnect, **common)


def load_llm_settings(env_file: Path | None = None) -> LLMSettings:
    """Read credentials from .env / process env, return the provider's settings.

    Fails loudly with actionable messages — SDK errors from missing keys are
    404s / 401s on URLs the user can't see, which is miserable to debug.
    """
    load_env(env_file)
    provider = _detect_provider()

    if provider == "floodgate":
        return _load_floodgate_settings()

    if provider == "azure":
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        api_version = os.getenv("AZURE_OPENAI_API_VERSION")
        # Azure OpenAI key may live under AZURE_OPENAI_API_KEY or (commonly)
        # under the plain OPENAI_API_KEY. Prefer the explicit name.
        api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        missing = [
            name
            for name, val in [
                ("AZURE_OPENAI_ENDPOINT", endpoint),
                ("AZURE_OPENAI_DEPLOYMENT", deployment),
                ("AZURE_OPENAI_API_VERSION", api_version),
                ("AZURE_OPENAI_API_KEY or OPENAI_API_KEY", api_key),
            ]
            if not val
        ]
        if missing:
            raise MissingCredentials(
                "LLM_PROVIDER=azure but missing: "
                + ", ".join(missing)
                + "\nCopy .env.example → .env and fill it in, or set LLM_PROVIDER=openai."
            )
        return AzureSettings(
            endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            deployment=deployment,
        )

    # OpenAI direct
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise MissingCredentials(
            "Missing OPENAI_API_KEY (and no AZURE_OPENAI_ENDPOINT to fall back to Azure).\n"
            "Copy .env.example → .env in the judge/ folder and fill in your key."
        )
    return OpenAISettings(
        api_key=api_key,
        model=os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL") or "gpt-4o-mini",
        base_url=os.getenv("OPENAI_BASE_URL"),
        organization=os.getenv("OPENAI_ORG"),
    )


# Backwards-compatible name; some code paths still call this.
def load_openai_settings(env_file: Path | None = None) -> LLMSettings:
    return load_llm_settings(env_file)
