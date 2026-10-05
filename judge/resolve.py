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

from judge.config import REPO_ROOT, ReleaseConfig, load_env, load_judge_config

PROFILES: tuple[str, ...] = ("dev", "full")

# Every path a run needs comes from `config/judge/<domain>.json` (the `release`
# block), which is shaped like the generation and Q&A configs it sits beside.
# Those two are still read — for the version a domain was generated at — through
# the READ-ONLY pointers in that block, so all three stages move together when a
# release layout changes. What is gone is this module naming one domain's Q&A
# config: it used to point at qa_pairs/generator/crm/config.json for every
# domain, so Sales resolved through CRM's declared version and only survived on
# the filesystem fallback below.
_GENERATION_CONFIG = Path("config") / "generation"


def _release(domain: str) -> "ReleaseConfig":
    return load_judge_config(domain).release


def _fmt(template: str, **values: object) -> str:
    return str(template).format(**values)


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


def qa_version(domain: str, *, repo_root: Path | None = None) -> str:
    """The Q&A version to score: the judge's pin, else what the domain declares.

    A pin is the whole point of the `release` block — a domain carrying several
    Q&A packages can be re-scored against an older one without touching
    `qa_pairs/`, which the judge only ever reads.
    """

    release = _release(domain)
    if release.qa_version:
        return release.qa_version
    root = _root(repo_root)
    declared = _load_json(root / _fmt(release.qa_config_path, domain=domain))
    return str((declared.get("qa_release") or {}).get("version") or "").strip()


def _templated_qa_dir(domain: str, profile: str, repo_root: Path | None) -> Path | None:
    release = _release(domain)
    template = release.qa_sources.get(profile)
    if not template:
        return None
    version = qa_version(domain, repo_root=repo_root)
    if profile == "full" and not version:
        # Without a version there is no directory to name; fall through to the
        # filesystem scan rather than resolving `qa-pairs-v` with nothing after it.
        return None
    return _root(repo_root) / _fmt(
        template, domain=domain, qa_version=version, profile=profile
    )


def qa_release_dir(
    domain: str, profile: str, *, repo_root: Path | None = None
) -> Path:
    """The Q&A package a run of this domain/profile scores.

    Config first, exactly as the generation and Q&A stages resolve their own
    outputs: the path template comes from `release.qa_sources` in
    `config/judge/<domain>.json`, and the version from `release.qa_version` if it
    is pinned there, otherwise from what the domain's own Q&A config declares.

    The filesystem is consulted only as a fallback, and only for `full` — for a
    domain whose Q&A config does not exist yet, take the newest `qa-pairs-v*`
    directory that actually holds pairs. Newest-by-version, and "holds pairs"
    rather than "exists", so a half-created v0.4.0 cannot mask a complete v0.3.0.

    A PINNED version never falls back. Asking for one build and silently scoring
    another is the one failure this block exists to prevent, so a missing pin is
    an error with the version named in it.
    """

    root = _root(repo_root)
    release = _release(domain)
    templated = _templated_qa_dir(domain, profile, repo_root)
    if templated is not None and (profile != "full" or _has_pairs(templated, domain)):
        return templated
    if release.qa_version:
        raise ResolutionError(
            f"config/judge/{domain}.json pins release.qa_version="
            f"{release.qa_version!r}, but {templated} holds no pairs. Either build "
            f"that package or change the pin — falling back to a different "
            f"version would score a build nobody asked for."
        )
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
        f"config/judge/{domain}.json declares no release.qa_sources entry for it."
    )


def _has_pairs(directory: Path, domain: str) -> bool:
    release = _release(domain)
    return (
        directory / _fmt(release.input_csv_name, domain=domain)
    ).is_file() or (directory / _fmt(release.pairs_csv_name, domain=domain)).is_file()


def judge_input_csv(
    domain: str,
    profile: str,
    *,
    repo_root: Path | None = None,
) -> Path:
    """The pair CSV the judge consumes: the Q&A contract file itself.

    There used to be a derived `<domain>_judge_input.csv`, joined from the
    contract plus its companion, because the §9.3 contract carried only the seven
    answer fields and the companion was the only source of `question_id` and
    `tier`. Since the Q&A side put both into the contract — every domain's
    generator now emits them — the join added exactly two columns, `family` and
    `scoring_mode`, and neither is read anywhere in `judge/` or `scorecard/`.

    So the derived file is gone and this resolves the contract directly. A
    pre-joined file is still honoured where one exists, so an older release
    package keeps working untouched.
    """

    release = _release(domain)
    qa_dir = qa_release_dir(domain, profile, repo_root=repo_root)

    joined = qa_dir / _fmt(release.input_csv_name, domain=domain)
    if joined.is_file():
        # An older package that still ships the derived file. Preferred over the
        # contract so a run against it is byte-for-byte what it always was.
        return joined

    contract = qa_dir / _fmt(release.pairs_csv_name, domain=domain)
    if contract.is_file():
        return contract

    raise ResolutionError(
        f"no pair set for domain={domain!r} profile={profile!r}: {qa_dir} "
        f"is missing {contract.name}.\n"
        f"Generate the pairs first: python main.py qa-build --domain {domain} "
        f"--profile {profile}"
    )


def dataset_release_config(domain: str, *, repo_root: Path | None = None) -> dict:
    """The generation-side release config, or `{}` for a judge-only domain.

    Read through `release.dataset_release_config_path`, and only ever read — the
    judge does not own `config/generation/`. Domains can be scored without being
    generated here (`config/judge/` carries more domains than
    `config/generation/` does), so an absent file is a normal state, not an error.
    """

    pointer = _fmt(_release(domain).dataset_release_config_path, domain=domain)
    return _load_json(_root(repo_root) / pointer)


def dataset_version(domain: str, *, repo_root: Path | None = None) -> str:
    """The dataset version for this domain, resolved the way generation does.

    `NLQ_DATASET_VERSION` → `release.dataset_version` in this domain's judge
    config → what the domain's generation config declares. The pin is the
    middle step and the reason the block exists: it lets a run score an older
    build for a regression comparison without touching `config/generation/`.
    Unpinned, the judge follows generation, so a version bump cannot leave the
    scorer pointed at a build nobody generated. Returns `""` for a judge-only
    domain with no generation config at all.
    """

    override = (os.getenv("NLQ_DATASET_VERSION") or "").strip()
    if override:
        return override
    pinned = _release(domain).dataset_version
    if pinned:
        return pinned
    release = dataset_release_config(domain, repo_root=repo_root)
    if not release:
        # Judge-only domain: there is no dataset here to have a version. The
        # shared default would otherwise let a run label claim one.
        return ""
    declared = str(release.get("dataset_version") or "").strip()
    if declared:
        return declared
    base = _load_json(_root(repo_root) / _GENERATION_CONFIG / "base.json")
    return str(base.get("dataset_version") or "").strip()


def dataset_csv_dir(
    domain: str, profile: str, *, repo_root: Path | None = None
) -> Path | None:
    """Where the generated CSVs for this domain/profile live, for `--pulse sql`.

    Returns None when the domain has no generation config — `--pulse sql` then
    stays a flag the caller must fill in, because guessing a directory of tables
    to execute reference SQL against is worse than asking.
    """

    root = _root(repo_root)
    release = _release(domain)
    version = dataset_version(domain, repo_root=repo_root)
    template = release.dataset_sources.get(profile)
    if template and (version or "{dataset_version}" not in template):
        base = root / _fmt(
            template, domain=domain, dataset_version=version, profile=profile
        )
    else:
        # No version to interpolate — a judge-only domain, or one generated
        # outside this repo. Take the newest build present.
        if release.dataset_version:
            raise ResolutionError(
                f"config/judge/{domain}.json pins release.dataset_version="
                f"{release.dataset_version!r}, but no template can place it. "
                f"Check release.dataset_sources[{profile!r}]."
            )
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
    version = dataset_version(domain, repo_root=repo_root)
    if version:
        parts.append(version)
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
