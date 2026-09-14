"""
Generate the Q&A release from three declarative files + Jinja templates:

  config.json     domain, id_prefix, tier quotas
  catalog.json    param name -> SQL (or literal list) of allowed values
  families.json   question families (template, params, quota, question, ...)

Adding a family = one families.json entry + one .sql.j2. No code change.
Nothing here is CRM-specific except the file contents.

    python generator/scale_pairs.py --profile dev|full
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from utils import build_stamp, verify  # noqa: E402
from utils.duckdb_io import connect_typed, distinct  # noqa: E402
from utils.labels import label  # noqa: E402
from utils.labels import load as load_labels  # noqa: E402
from utils.output_paths import resolve_qa_output_dir  # noqa: E402
from utils.sampling import combos, even_sample  # noqa: E402
from utils.sql import jinja_env  # noqa: E402

GEN = BASE / "generator"
CONFIG = json.loads((GEN / "config.json").read_text())
CATALOG_SPEC = json.loads((GEN / "catalog.json").read_text())
FAMILIES = json.loads((GEN / "families.json").read_text())
LABELS = load_labels(GEN / "labels.json")
SCORING = CONFIG["scoring"]
ENV = jinja_env(GEN / "templates")

CONTRACT = [
    "natural_language_question",
    "expected_answer",
    "reference_sql",
    "reference_tables",
    "reference_fields",
    "judge_reference",
    "derivation_rationale",
]
COMPANION = [
    "question_id",
    "tier",
    "family",
    "join_path_id",
    "sql_source",
    "scoring_mode",
    "answer_schema",
    "numeric_components",
    "judge_rubric_version",
    "judge_prompt_version",
    "scorer_status",
    "param_values",
    "result_hash",
    "natural_language_question",
]

_NUMERIC_TYPES = {"int", "decimal", "float"}
_ORDER_BY = re.compile(r"order\s+by\s+(.+?)(?:\s+limit\b.*)?;?\s*$", re.I | re.S)


def _answer_schema(fam: dict, res, sql: str) -> str:
    cols = list(zip(res.columns or [], res.col_types or []))
    parts = "|".join(f"{c}:{t}" for c, t in cols)
    m = _ORDER_BY.search(sql.strip())
    order = re.sub(r"\s+", " ", m.group(1).strip()) if m else "unordered"
    nrows = len(res.rows)
    return (
        f"shape={fam['answer_shape']}; cols={parts}; "
        f"order={order}; rows={'1' if nrows == 1 else str(nrows)}"
    )


def _numeric_components(res) -> str:
    names = [c for c, t in zip(res.columns or [], res.col_types or []) if t in _NUMERIC_TYPES]
    return "|".join(names) if names else "(none)"


def build_catalog(con) -> dict[str, list]:
    out = {}
    for name, spec in CATALOG_SPEC.items():
        if name.startswith("_"):
            continue
        out[name] = list(spec) if isinstance(spec, list) else distinct(con, spec)
    return out


def family_combos(fam: dict, catalog: dict) -> list[dict]:
    space = combos({p: catalog[p] for p in fam["params"]})
    return even_sample(space, fam["quota"])


def check_template_vars(fam: dict) -> None:
    from jinja2 import meta

    src = ENV.loader.get_source(ENV, fam["template"])[0]
    missing = meta.find_undeclared_variables(ENV.parse(src)) - set(fam["params"])
    if missing:
        raise SystemExit(f"{fam['family']} {fam['template']}: unbound vars {sorted(missing)}")


def generate_pairs(profile: str) -> None:
    """Generate and verify the complete Q&A pair release for a profile."""

    quota, prefix = CONFIG["tier_quota"], CONFIG["id_prefix"]
    for tier, want in quota.items():
        got = sum(f["quota"] for f in FAMILIES if f["tier"] == tier)
        if got != want:
            raise SystemExit(f"{tier} family quotas sum to {got}, need {want}")

    manifest = json.loads((BASE / "dataset" / f"manifest_{profile}.json").read_text())
    dv = manifest["dataset_version"]
    out = resolve_qa_output_dir(BASE, profile)
    con = connect_typed(BASE / "dataset" / f"crm_{profile}.duckdb")
    build_stamp.require_fresh_db(con, manifest, BASE, profile)
    catalog = build_catalog(con)

    # Build the whole release in memory first. Nothing is written - and the
    # previous release is not deleted - until every per-tier quota is met, so a
    # failed run never leaves a partial or half-updated answer key on disk.
    pairs, comp, misses = [], [], []
    log_entries = []  # (name, payload) flushed on success
    for tier in quota:
        seq = 0
        for fam in [f for f in FAMILIES if f["tier"] == tier]:
            check_template_vars(fam)
            for ctx in family_combos(fam, catalog):
                sql = ENV.get_template(fam["template"]).render(**ctx).strip()
                res = verify.run(con, sql)
                if res.status != "ok":
                    slug = "-".join(map(str, ctx.values())).replace("/", "_")
                    log_entries.append(
                        (
                            f"{fam['family']}__{slug}.blocked",
                            verify.log_payload(
                                question_id="",
                                tier=tier,
                                dataset_version=dv,
                                profile=profile,
                                sql=sql,
                                result=res,
                                domain=CONFIG["domain"],
                                family=fam["family"],
                                param_values=ctx,
                            ),
                        )
                    )
                    continue
                seq += 1
                qid = f"{prefix}-{fam['family']}-{seq:02d}"
                log_entries.append(
                    (
                        qid,
                        verify.log_payload(
                            question_id=qid,
                            tier=tier,
                            dataset_version=dv,
                            profile=profile,
                            sql=sql,
                            result=res,
                            domain=CONFIG["domain"],
                            family=fam["family"],
                            param_values=ctx,
                        ),
                    )
                )
                q = fam["question"].format(**{k: label(v, LABELS) for k, v in ctx.items()})
                pairs.append(
                    {
                        "natural_language_question": q,
                        "expected_answer": res.answer,
                        "reference_sql": sql,
                        "reference_tables": fam["tables"],
                        "reference_fields": fam["fields"],
                        "judge_reference": fam["judge"],
                        "derivation_rationale": fam["rationale"],
                    }
                )
                mode = fam["scoring_mode"]
                judged = mode == "judge_plus_exact"
                comp.append(
                    {
                        "question_id": qid,
                        "tier": tier,
                        "family": fam["family"],
                        "join_path_id": fam["join_path"],
                        "sql_source": fam["template"],
                        "scoring_mode": mode,
                        "answer_schema": _answer_schema(fam, res, sql),
                        "numeric_components": _numeric_components(res),
                        "judge_rubric_version": SCORING["judge_rubric_version"] if judged else "",
                        "judge_prompt_version": SCORING["judge_prompt_version"] if judged else "",
                        "scorer_status": SCORING["scorer_status"],
                        "param_values": ";".join(f"{k}={v}" for k, v in ctx.items()),
                        "result_hash": res.result_hash,
                        "natural_language_question": q,
                    }
                )
        if seq != quota[tier]:
            misses.append(f"{tier} {seq}/{quota[tier]}")
        print(
            f"[{profile}] {tier}: {seq} / {quota[tier]}"
            f"{'  <-- UNDER QUOTA' if seq != quota[tier] else ''}"
        )

    if misses:
        raise SystemExit("QUOTA FAILURE (nothing written): " + ", ".join(misses))

    if out.exists():
        shutil.rmtree(out)
    logs = out / "verification_logs"
    for name, payload in log_entries:
        verify.write_payload(logs, name, payload)
    _write(out / "crm_qa_pairs.csv", CONTRACT, pairs)
    _write(
        out / "crm_qa_pairs_companion.csv", COMPANION, sorted(comp, key=lambda r: r["question_id"])
    )
    print(f"[{profile}] {len(pairs)} pairs / {sum(quota.values())}")
    print(f"[{profile}] Q&A output: {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["dev", "full"], default="dev")
    generate_pairs(ap.parse_args().profile)


def _write(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        w.writeheader()
        w.writerows({k: r[k] for k in fields} for r in rows)


if __name__ == "__main__":
    main()
