"""Cross-domain Q&A question uniqueness check (Execution Spec v3.0, Section
9.4 rule 9 / Section 13.2 "CI checks: ... cross-domain question uniqueness
hash ...").

No natural_language_question may appear in more than one domain's released
Q&A set. Normalises text (lowercase, strip punctuation/whitespace) before
hashing, so near-identical phrasing differences don't hide a real
duplicate. Checks every domain's `full`-profile release only - that's the
one that ships.

    python scripts/check_cross_domain_uniqueness.py

Exits non-zero (and prints every colliding pair) if any question's
normalized text appears in more than one domain.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOMAINS = ["crm", "sales", "finance", "logistics", "project_management"]


def normalize(question: str) -> str:
    text = question.lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def released_csv_path(domain: str) -> Path:
    config = REPO_ROOT / "qa_pairs" / "generator" / domain / "config.json"
    import json

    cfg = json.loads(config.read_text(encoding="utf-8"))
    release = cfg["qa_release"]
    from qa_pairs.utils.release_bundle import active_release_version

    release_config = json.loads(
        (REPO_ROOT / cfg["dataset"]["release_config_path"]).read_text(encoding="utf-8")
    )
    version = active_release_version(release_config)
    relative = str(release["profile_outputs"]["full"]).format(
        domain=domain, qa_version=version, release_version=version, profile="full"
    )
    direct = REPO_ROOT / relative / f"{domain}_qa_pairs.csv"
    if direct.is_file():
        return direct
    from qa_pairs.utils.release_bundle import component_dir

    return (
        component_dir(domain, "qa_pairs", version, repo_root=REPO_ROOT)
        / f"{domain}_qa_pairs.csv"
    )


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    seen: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    missing = []
    for domain in DOMAINS:
        path = released_csv_path(domain)
        if not path.is_file():
            missing.append((domain, path))
            continue
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                norm = normalize(row["natural_language_question"])
                seen[norm].append(
                    (domain, row["question_id"], row["natural_language_question"])
                )

    if missing:
        print("Could not find a released Q&A CSV for:")
        for domain, path in missing:
            print(f"  {domain}: expected at {path}")
        print("Run `main.py qa-build --domain <domain> --profile full` for each first.")
        return 2

    duplicates = {
        norm: entries
        for norm, entries in seen.items()
        if len({d for d, _, _ in entries}) > 1
    }

    total = len(seen)
    if duplicates:
        print(f"CROSS-DOMAIN DUPLICATE QUESTIONS: {len(duplicates)} group(s)")
        for norm, entries in duplicates.items():
            print(f"  normalized: {norm!r}")
            for domain, qid, original in entries:
                print(f"    {domain:20} {qid:20} {original}")
        return 1

    print(
        f"OK: {total} unique normalized questions across {len(DOMAINS)} domains, 0 cross-domain duplicates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
