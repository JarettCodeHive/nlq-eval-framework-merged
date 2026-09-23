"""Resolve a scoring run's inputs from `--domain` and `--profile` alone.

Every other stage of the pipeline takes exactly two arguments and derives the
rest from config — `build-dataset --domain crm --profile full` never asks where
to write, and `qa-build` never asks where the CSVs are. `judge` was the
exception: the same two facts already determine which Q&A package holds the
pairs, which dataset those pairs were authored against and how to label the
run, yet all of it had to be spelled out as paths on the command line. A
mistyped `--input-csv` then scores one release against another release's
calibration marker — exactly the class of mistake a derived path cannot make.

Explicit flags still win. Every function here fills a value in only where the
caller left it empty, so the full flag surface documented in `judge/README.md`
keeps working unchanged.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from judge.build_input import build as build_judge_input
from judge.config import REPO_ROOT, load_env, load_judge_config

PROFILES: tuple[str, ...] = ("dev", "full")

# The Q&A and generation stages each resolve their own outputs from a declarative
# config (`qa_pairs/utils/output_paths.py`, `qa_pairs/utils/dataset_source.py`).
# The judge reads the SAME two files rather than restating the paths, so all
# three stages move together when a release layout changes.
_QA_CONFIG_PATH = Path("qa_pairs") / "generator" / "crm" / "config.json"
_GENERATION_CONFIG = Path("config") / "generation"


class ResolutionError(RuntimeError):
    """A required input could not be derived. Carries the remedy, not just the gap."""


@dataclass(frozen=True)
class ResolvedInputs:
    """What a run was given, after the blanks were filled in."""

    domain: str
    profile: str
    input_csv: Path
    qa_release_dir: Path
    dataset_version: str
    platform_version: str
    dataset_csv_dir: Path | None
    # Flag name -> human-readable value, for the one-screen echo at run start.
    # Only holds what was DERIVED, so the operator can see it and disagree.
    derived: dict[str, str]


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ResolutionError(f"{path} is not valid JSON: {exc}") from exc
    return loaded if isinstance(loaded, dict) else {}


def _root(repo_root: Path | None) -> Path:
    return repo_root or REPO_ROOT


def _version_key(path: Path) -> tuple[int, ...]:
    """Sort `qa-pairs-v0.10.0` after `qa-pairs-v0.9.0`, which a string sort does not."""

    return tuple(int(n) for n in re.findall(r"\d+", path.name)) or (0,)


def _templated_qa_dir(domain: str, profile: str, repo_root: Path | None) -> Path | None:
    root = _root(repo_root)
    config = _load_json(root / _QA_CONFIG_PATH)
    release = config.get("qa_release") or {}
    template = (release.get("profile_outputs") or {}).get(profile)
    if not template:
        return None
    return root / str(template).format(
        domain=domain,
        qa_version=release.get("version", ""),
        profile=profile,
    )


def qa_release_dir(
    domain: str, profile: str, *, repo_root: Path | None = None
) -> Path:
    """The Q&A package a run of this domain/profile scores.

    Config first, exactly as `resolve_qa_output_dir` and `resolve_dataset_source`
    do for their own stages: the path template and the version both come from
    `qa_pairs/generator/crm/config.json`, so a release layout moves without a
    code change here. Judge wiring is CRM-only today, so that fixed path is
    the one domain this function actually resolves correctly; the filesystem
    fallback below is what keeps a `full`-profile lookup for another domain
    from silently returning the wrong (CRM) version.

    The filesystem is consulted only as a fallback, and only for `full`. That
    config declares ONE domain's Q&A version, so a domain it does not cover has
    no templated answer — for those, take the newest `qa-pairs-v*` directory
    that actually holds pairs. Newest-by-version, and "holds pairs" rather than
    "exists", so a half-created v0.4.0 cannot mask a complete v0.3.0.
    """

    root = _root(repo_root)
    templated = _templated_qa_dir(domain, profile, repo_root)
    if templated is not None and (profile != "full" or _has_pairs(templated, domain)):
        return templated
    if profile == "full":
        candidates = sorted(
            (root / "release" / domain).glob("qa-pairs-v*"),
            key=_version_key,
            reverse=True,
        )
        for candidate in candidates:
            if _has_pairs(candidate, domain):
                return candidate
    if templated is not None:
        return templated
    raise ResolutionError(
        f"cannot locate the Q&A package for domain={domain!r} profile={profile!r}: "
        f"no {root / 'release' / domain}/qa-pairs-v*/ holds pairs and "
        f"{root / _QA_CONFIG_PATH} declares no output path for that profile."
    )


def _has_pairs(directory: Path, domain: str) -> bool:
    return (directory / f"{domain}_judge_input.csv").is_file() or (
        directory / f"{domain}_qa_pairs.csv"
    ).is_file()


def judge_input_csv(
    domain: str,
    profile: str,
    *,
    repo_root: Path | None = None,
    build_if_missing: bool = True,
) -> Path:
    """The joined pair CSV the judge consumes, built on demand if it is absent.

    The Q&A release deliberately splits the §9.3 contract from the identifiers,
    so the joined file is a judge-side artifact rather than part of the sealed
    package. Building it here is the same deterministic join `judge-build-input`
    performs — same release in, byte-identical CSV out — which makes "did you
    remember to run judge-build-input first?" a question nobody has to answer.
    """

    qa_dir = qa_release_dir(domain, profile, repo_root=repo_root)
    csv_path = qa_dir / f"{domain}_judge_input.csv"
    if csv_path.is_file():
        return csv_path

    contract = qa_dir / f"{domain}_qa_pairs.csv"
    companion = qa_dir / f"{domain}_qa_pairs_companion.csv"
    if build_if_missing and contract.is_file() and companion.is_file():
        print(
            f"[judge] {csv_path.name} is missing — joining it from {qa_dir}",
            file=sys.stderr,
        )
        build_judge_input(qa_dir, domain, csv_path)
        return csv_path

    missing = [p.name for p in (contract, companion) if not p.is_file()]
    raise ResolutionError(
        f"no judge input for domain={domain!r} profile={profile!r}: {qa_dir} "
        f"is missing {', '.join(missing) or csv_path.name}.\n"
        f"Generate the pairs first: python main.py qa-build --domain {domain} "
        f"--profile {profile}"
    )


def dataset_release_config(domain: str, *, repo_root: Path | None = None) -> dict:
    """The generation-side release config, or `{}` for a judge-only domain.

    Domains can be scored without being generated here — `config/judge/` carries
    more domains than `config/generation/` does — so an absent file is a normal
    state, not an error.
    """

    return _load_json(_root(repo_root) / _GENERATION_CONFIG / domain / "release.json")


def dataset_csv_dir(
    domain: str, profile: str, *, repo_root: Path | None = None
) -> Path | None:
    """Where the generated CSVs for this domain/profile live, for `--pulse sql`.

    Returns None when the domain has no generation config — `--pulse sql` then
    stays a flag the caller must fill in, because guessing a directory of tables
    to execute reference SQL against is worse than asking.
    """

    root = _root(repo_root)
    config = dataset_release_config(domain, repo_root=repo_root)
    raw = (config.get("output_paths") or {}).get(profile)
    if raw:
        base = root / str(raw).format(
            domain=domain,
            dataset_version=config.get("dataset_version", ""),
            profile=profile,
        )
    else:
        versions = sorted(
            (root / "release" / domain).glob("dataset-v*"),
            key=_version_key,
            reverse=True,
        )
        if not versions:
            return None
        base = versions[0]
    # A dev build keeps one directory per pipeline stage. The pairs were authored
    # against the imperfect stage, so that is the only stage worth replaying.
    imperfect = base / "imperfect"
    return imperfect if imperfect.is_dir() else base


def dataset_version_label(
    domain: str, profile: str, *, repo_root: Path | None = None
) -> str:
    """The §11.1 version tag for the data this run scored.

    Two versions move independently — question templates change without a new
    dataset, and vice versa — so neither alone identifies what was scored. The
    label carries both: `dataset-v1.0.0+qa-pairs-v0.3.0`. It is a label only; no
    path is ever derived back out of it.
    """

    parts: list[str] = []
    dataset_version = str(
        dataset_release_config(domain, repo_root=repo_root).get("dataset_version", "")
    ).strip()
    if dataset_version:
        parts.append(dataset_version)
    if profile == "full":
        qa_dir = qa_release_dir(domain, profile, repo_root=repo_root)
        parts.append(qa_dir.name)
    else:
        parts.append(profile)
    return "+".join(parts)


def platform_version(domain: str) -> str:
    """The version tag of the platform under evaluation, if the environment knows.

    Environment first, then `config/judge/<domain>.json` — the reverse of how the
    judge model resolves, and deliberately so: the model is a measurement
    instrument that a checked-in config should pin, while the platform version
    describes whatever deployment this machine happens to be pointed at.

    Empty when neither declares it. That is not an error here; a PREVIEW run does
    not need it, and a `--release` run already refuses without it (§11.1).
    """

    load_env()
    from_env = (os.getenv("PLATFORM_VERSION") or "").strip()
    if from_env:
        return from_env
    return (load_judge_config(domain).platform_version or "").strip()


def apply_resolved_defaults(args, *, repo_root: Path | None = None) -> ResolvedInputs:
    """Fill in every input the run did not spell out, and report what was filled.

    Mutates `args` in place so the run manifest, the run log and the scorecard
    all record the paths that were actually used rather than the blanks that
    were passed.
    """

    domain = args.domain
    profile = getattr(args, "profile", "dev") or "dev"
    if profile not in PROFILES:
        raise ResolutionError(
            f"unsupported --profile {profile!r}; expected one of {', '.join(PROFILES)}"
        )

    derived: dict[str, str] = {}

    if not getattr(args, "input_csv", ""):
        resolved_csv = judge_input_csv(domain, profile, repo_root=repo_root)
        args.input_csv = str(resolved_csv)
        derived["--input-csv"] = str(resolved_csv)
    input_csv = Path(args.input_csv)

    qa_dir = input_csv.parent

    csv_dir = dataset_csv_dir(domain, profile, repo_root=repo_root)
    if getattr(args, "pulse", "") == "sql" and not getattr(args, "pulse_data", ""):
        if csv_dir is None or not csv_dir.is_dir():
            raise ResolutionError(
                f"--pulse sql needs the generated CSVs for domain={domain!r} "
                f"profile={profile!r}, and none were found"
                + (f" at {csv_dir}" if csv_dir else "")
                + f".\nBuild them first: python main.py build-dataset --domain "
                f"{domain} --profile {profile}"
            )
        args.pulse_data = str(csv_dir)
        derived["--pulse-data"] = str(csv_dir)

    if not getattr(args, "dataset_version", ""):
        label = dataset_version_label(domain, profile, repo_root=repo_root)
        if label:
            args.dataset_version = label
            derived["--dataset-version"] = label

    if not getattr(args, "platform_version", ""):
        version = platform_version(domain)
        if version:
            args.platform_version = version
            derived["--platform-version"] = version

    if derived:
        print(
            f"[judge] resolved from --domain {domain} --profile {profile}:",
            file=sys.stderr,
        )
        for flag, value in derived.items():
            print(f"[judge]   {flag:<18} {value}", file=sys.stderr)

    return ResolvedInputs(
        domain=domain,
        profile=profile,
        input_csv=input_csv,
        qa_release_dir=qa_dir,
        dataset_version=getattr(args, "dataset_version", ""),
        platform_version=getattr(args, "platform_version", ""),
        dataset_csv_dir=csv_dir,
        derived=derived,
    )
