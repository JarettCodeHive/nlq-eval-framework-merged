"""Connection settings for the Claris Studio data plane.

Same host, org and credential as the AI service the judge already queries — but
a different route family. The judge talks to
`/api-proxy/org/{org}/ai-svc/...`; dataset management lives at plain
`/org/{org}/entity`. Both are read from the PULSE_* variables so one
`judge/.env` configures the whole pipeline, with STUDIO_* overrides for the
unusual case where the data plane sits somewhere else.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from judge.config import load_env

# Rows per bulk-load request. 4800 is what the Studio UI itself sends for a
# 24,000-row table — five requests rather than one — so it is a batch size the
# endpoint is known to accept rather than a number we picked.
DEFAULT_BATCH_ROWS = 4800
# The UI batches by row count alone: its five `accounts` batches ranged
# 1,078,022 to 1,083,529 bytes and all were accepted. That upper figure is
# therefore the largest body the platform is known to tolerate, and it is used
# here as a ceiling rather than a target. It never binds on a table as narrow
# as `accounts`, so those requests stay byte-identical to the capture; it does
# bind on `support_cases`, which would otherwise send 1.7 MiB — half again
# larger than anything observed working.
DEFAULT_BATCH_BYTES = 1_083_529


class MissingStudioCredentials(RuntimeError):
    """Raised with an actionable message rather than a bare 401 later."""


class StudioSettings(BaseModel):
    """Everything needed to reach the Studio data plane for one org."""

    base_url: str
    auth_token: str
    org_id: int
    # A bulk load of 4800 rows is a much longer request than a chat turn, and a
    # timeout mid-batch leaves a partially-loaded table.
    timeout_s: int = Field(default=300, gt=0)
    max_retries: int = Field(default=3, ge=0)
    backoff_base_s: float = Field(default=0.5, gt=0)
    backoff_max_s: float = Field(default=8.0, gt=0)
    batch_rows: int = Field(default=DEFAULT_BATCH_ROWS, gt=0)
    batch_bytes: int = Field(default=DEFAULT_BATCH_BYTES, gt=0)
    # How long to wait for one bulk-load job. A 4800-row batch should be quick,
    # but this is a shared QA org and the queue is not ours alone.
    job_timeout_s: float = Field(default=300.0, gt=0)
    verify_tls: bool = True
    ca_bundle: str | None = None  # path to a CA file (corp proxy); wins over verify_tls

    def httpx_verify(self) -> "str | bool":
        return self.ca_bundle if self.ca_bundle else self.verify_tls

    @property
    def redacted(self) -> dict[str, object]:
        """Safe for a log or manifest — no token."""

        return {
            "platform": "studio",
            "base_url_host": self.base_url.split("//")[-1].split("/")[0],
            "org_id": self.org_id,
            "batch_rows": self.batch_rows,
            "batch_bytes": self.batch_bytes,
        }


def _env_int(name: str, default: int | None = None) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise MissingStudioCredentials(f"{name} must be an integer; got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def load_studio_settings(env_file=None) -> StudioSettings:
    """Read Studio credentials from `judge/.env` or the process environment.

    Fails with a message naming the missing variable — a 401 from a bearer
    token that was never set reads like a permissions problem, which sends
    people looking in the wrong place.
    """

    load_env(env_file)

    base_url = (
        os.getenv("STUDIO_BASE_URL") or os.getenv("PULSE_BASE_URL") or ""
    ).strip().rstrip("/")
    auth_token = (
        os.getenv("STUDIO_AUTH_TOKEN") or os.getenv("PULSE_AUTH_TOKEN") or ""
    ).strip()
    org_id = _env_int("STUDIO_ORG_ID") or _env_int("PULSE_ORG_ID")

    missing = [
        name
        for name, value in (
            ("PULSE_BASE_URL (or STUDIO_BASE_URL)", base_url),
            ("PULSE_AUTH_TOKEN (or STUDIO_AUTH_TOKEN)", auth_token),
            ("PULSE_ORG_ID (or STUDIO_ORG_ID)", org_id),
        )
        if not value
    ]
    if missing:
        raise MissingStudioCredentials(
            "Missing Studio credentials: "
            + ", ".join(missing)
            + "\nThese are the same values the judge uses; copy judge/.env.example "
            "→ judge/.env and fill it in."
        )

    return StudioSettings(
        base_url=base_url,
        auth_token=auth_token,
        org_id=int(org_id),
        timeout_s=_env_int("STUDIO_TIMEOUT_S", 300),
        max_retries=_env_int("STUDIO_MAX_RETRIES", 3),
        batch_rows=_env_int("STUDIO_BATCH_ROWS", DEFAULT_BATCH_ROWS),
        batch_bytes=_env_int("STUDIO_BATCH_BYTES", DEFAULT_BATCH_BYTES),
        job_timeout_s=float(_env_int("STUDIO_JOB_TIMEOUT_S", 300)),
        verify_tls=_env_bool("STUDIO_VERIFY_TLS", _env_bool("PULSE_VERIFY_TLS", True)),
        ca_bundle=(
            os.getenv("STUDIO_CA_BUNDLE") or os.getenv("PULSE_CA_BUNDLE") or ""
        ).strip()
        or None,
    )
