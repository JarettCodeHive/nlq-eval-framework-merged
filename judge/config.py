"""Judge configuration — Anthropic via Apple's Floodgate proxy, plus per-domain
JSON configs.

Floodgate is the only backend. Apple policy routes externally-hosted models
through it, so an OpenAI or Azure path was never usable here in practice; both
were removed rather than left as dead branches that a stray `LLM_PROVIDER`
could still select.

Credential shape is auto-detected: a Narrative certificate pair means an
unattended run (CI, a pod, a Bolt task), otherwise an AppleConnect token on a
developer's Mac.
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path
from typing import Literal, Union

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

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
# One backend, but the alias stays: every call site is written against
# "whatever the configured judge provider is", not against Floodgate
# specifically, and the two credential shapes are already a union.
LLMSettings = FloodgateSettings


class ReleaseConfig(BaseModel):
    """Which dataset and Q&A build a run of this domain scores.

    Shaped like the generation and Q&A configs it sits beside: profile-keyed path
    maps, `{domain}` / `{dataset_version}` / `{qa_version}` interpolation, and
    pointer strings to the sibling configs rather than restated paths. Those two
    pointers are READ-ONLY — the judge resolves versions out of them and never
    writes to `config/generation/` or `qa_pairs/`.

    `dataset_version` / `qa_version` are the point of the block. Left null they
    resolve to the newest build on disk, which is what the judge always did
    implicitly. Pinned to a string they select one build, so a domain carrying
    several dataset versions can be re-scored against an older one — a regression
    comparison — without regenerating anything or editing another module's config.
    """

    model_config = ConfigDict(extra="forbid")

    dataset_version: str | None = None
    qa_version: str | None = None
    qa_config_path: str = "qa_pairs/generator/{domain}/config.json"
    dataset_release_config_path: str = "config/generation/{domain}/release.json"
    qa_sources: dict[str, str] = Field(
        default_factory=lambda: {
            "dev": "tmp/generated/{domain}/dev/qa_pairs",
            "full": "release/{domain}/qa-pairs-v{qa_version}",
        }
    )
    dataset_sources: dict[str, str] = Field(
        default_factory=lambda: {
            "dev": "tmp/generated/{domain}/dev/imperfect",
            "full": "release/{domain}/{dataset_version}",
        }
    )
    input_csv_name: str = "{domain}_judge_input.csv"
    pairs_csv_name: str = "{domain}_qa_pairs.csv"
    companion_csv_name: str = "{domain}_qa_pairs_companion.csv"


class CalibrationConfig(BaseModel):
    """§10.2 acceptance thresholds and where the anchors and marker live.

    These were module constants, which made the protocol unreviewable without
    reading Python: the numbers the spec fixes (≥10 anchors, ±1 on ≥90%) were
    indistinguishable from numbers someone happened to choose.

    `require_calibration` defaults to true because §10.2 is explicit that
    uncalibrated scores must not enter a scorecard. Turning it off is therefore a
    deliberate, recorded config change per domain rather than a flag someone
    remembers to pass.
    """

    model_config = ConfigDict(extra="forbid")

    anchors_path: str = "judge/anchors/{domain}.json"
    marker_path: str = "judge/.calibration/{domain}.passed.json"
    min_anchors: int = Field(default=10, ge=1)
    agreement_pct: float = Field(default=90.0, gt=0, le=100)
    # §10.2: "never disagrees on direction ... on any anchor". Separate from the
    # ±1 threshold because it is a hard zero, not a percentage.
    allow_directional_flips: bool = False
    # Gate on SCORING: refuse to run an LLM judge against a domain with no
    # passing marker. §10.2's reading, and the default.
    require_calibration: bool = True
    # Gate on RELEASE: whether a --release scorecard additionally requires that
    # marker. Defaults FALSE by project decision — §10.2's last line says
    # uncalibrated scores must not enter a scorecard, so this is a deliberate
    # departure, taken because a baseline is needed before the calibration
    # session can be scheduled. What is NOT negotiable is the labelling: every
    # artifact still records `calibrated: false`, so a card produced this way
    # cannot be mistaken for a calibrated one after the fact.
    require_calibration_for_release: bool = False


class JudgeConfig(BaseModel):
    model: str
    domain: str = ""
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
    release: ReleaseConfig = Field(default_factory=ReleaseConfig)
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
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


def _without_notes(config: dict) -> dict:
    """Drop `_`-prefixed documentation keys, at the top level and one nesting in."""

    cleaned = {}
    for key, value in config.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if not k.startswith("_")}
        cleaned[key] = value
    return cleaned


def load_judge_config(domain: str, config_dir: Path | None = None) -> JudgeConfig:
    """Merge `default.json` with the `<domain>.json` override.

    Model selection precedence (§10.1 requires the per-domain JSON to be able to
    select the model, but the environment has to stay usable for a shared
    gateway/deployment):

      1. `model` set in `<domain>.json`  — an explicit per-domain choice wins
      2. FLOODGATE_MODEL / LLM_MODEL — the environment's model
      3. `model` in `default.json`       — the project-wide default

    On Floodgate the model is an Anthropic id with no provider prefix, e.g.
    `anthropic.claude-sonnet-5`. `FloodgateJudge` rejects anything that isn't,
    so a `default.json` model leaking into a Floodgate run fails at construction
    rather than as an opaque 400 from the proxy.
    """
    root = config_dir or CONFIGS_DIR
    base = _load_json(root / f"{DEFAULT_DOMAIN_CONFIG}.json")
    override = _load_json(root / f"{domain}.json")
    # Shallow-merging would make a domain that overrides one calibration field
    # silently lose the rest of the block, which is the sort of thing that only
    # surfaces as a weird threshold months later. Nested sections merge per key.
    merged = {**base, **override}
    for section in ("release", "calibration"):
        base_section = base.get(section)
        override_section = override.get(section)
        if isinstance(base_section, dict) and isinstance(override_section, dict):
            merged[section] = {**base_section, **override_section}
    merged.setdefault("domain", domain)
    # `_note` keys document these files, exactly as they do in
    # config/generation/ and qa_pairs/generator/. They are stripped rather than
    # allowed through, so the models can keep extra="forbid" and still catch a
    # real typo — `min_anchor` instead of `min_anchors` must fail loudly, not be
    # silently ignored as if it were a comment.
    merged = _without_notes(merged)

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
    so both read the same `.env`.

    The file lives at the repo root, because it configures the whole pipeline —
    the judge, the platform client and the Studio uploader all read it — not the
    judge alone. `.env` is still honoured as a fallback so an existing
    checkout keeps working, and warns once so it gets moved rather than
    silently diverging from the root file.

    `override=True` is deliberate: a stale credential inherited from the shell
    will silently take precedence over the .env file otherwise, and the module
    will happily send the WRONG key and get a 401. The .env file is
    authoritative for credentials — that's the whole point of dropping them
    there.
    """
    if env_file is not None:
        load_dotenv(env_file, override=True)
        return
    for root in (REPO_ROOT, MODULE_ROOT):
        for name in _ENV_CANDIDATES:
            p = root / name
            if p.is_file():
                if root is MODULE_ROOT:
                    warnings.warn(
                        f"{p} is a legacy location — move it to "
                        f"{REPO_ROOT / name}, which the whole pipeline reads.",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                load_dotenv(p, override=True)
                return
    # No candidate file — rely purely on the process environment.


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
    """Read credentials from .env / process env, return the judge's settings.

    Fails loudly with actionable messages — SDK errors from missing credentials
    surface as 401s on URLs the caller cannot see, which is miserable to debug.

    `LLM_PROVIDER` is still read, but only to reject a value that no longer
    exists: a leftover `LLM_PROVIDER=azure` in someone's environment should say
    so, not silently score through Floodgate and label the run as if it had been
    asked for.
    """
    load_env(env_file)
    requested = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if requested and requested != "floodgate":
        raise MissingCredentials(
            f"LLM_PROVIDER={requested!r} is not supported — Floodgate is the only "
            "judge backend (Apple policy routes externally-hosted models through "
            "it). Unset LLM_PROVIDER, or set it to 'floodgate'."
        )
    return _load_floodgate_settings()
