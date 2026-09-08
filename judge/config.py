"""Judge configuration — provider-agnostic (OpenAI direct or Azure OpenAI)
plus per-domain JSON configs.

Provider selection is auto-detected:
- If `AZURE_OPENAI_ENDPOINT` is set → use Azure OpenAI (`AsyncAzureOpenAI`).
- Else if `OPENAI_API_KEY` is set → use OpenAI direct (`AsyncOpenAI`).
- Else → `MissingCredentials` with an actionable message.

The `LLM_PROVIDER` env var can force selection: `LLM_PROVIDER=azure` or `openai`.
"""

from __future__ import annotations

import json
import os
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


LLMSettings = Union[OpenAISettings, AzureSettings]


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
    """Merge `default.json` with `<domain>.json` override."""
    root = config_dir or CONFIGS_DIR
    base = _load_json(root / f"{DEFAULT_DOMAIN_CONFIG}.json")
    override = _load_json(root / f"{domain}.json")
    merged = {**base, **override}
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


def _detect_provider() -> Literal["openai", "azure"]:
    forced = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if forced in ("openai", "azure"):
        return forced  # type: ignore[return-value]
    if os.getenv("AZURE_OPENAI_ENDPOINT"):
        return "azure"
    return "openai"


def load_llm_settings(env_file: Path | None = None) -> LLMSettings:
    """Read credentials from .env / process env, return OpenAI or Azure settings.

    Fails loudly with actionable messages — SDK errors from missing keys are
    404s / 401s on URLs the user can't see, which is miserable to debug.
    """
    load_env(env_file)
    provider = _detect_provider()

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
