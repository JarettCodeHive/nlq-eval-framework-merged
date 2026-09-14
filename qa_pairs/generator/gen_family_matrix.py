"""Regenerate taxonomy/crm_question_family_matrix.csv from families.json
(the single source of truth). Run after editing families.json.

    python generator/gen_family_matrix.py
"""

import csv
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
fams = json.loads((BASE / "generator" / "families.json").read_text())

out = BASE / "taxonomy" / "crm_question_family_matrix.csv"
with out.open("w", newline="", encoding="utf-8") as fh:
    fh.write(
        "# GENERATED from generator/families.json - do not hand-edit.\n"
        "# Regenerate: python generator/gen_family_matrix.py\n"
        "# quota = pairs shipped; scale_pairs.py asserts per-tier sums = 32/40/32/32/24.\n"
    )
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(
        [
            "family_id",
            "tier",
            "join_path_id",
            "params",
            "quota",
            "scoring_mode",
            "answer_shape",
            "question_template",
            "tables",
        ]
    )
    for f in fams:
        w.writerow(
            [
                f["family"],
                f["tier"],
                f["join_path"],
                "|".join(f["params"]),
                f["quota"],
                f["scoring_mode"],
                f["answer_shape"],
                f["question"],
                f["tables"],
            ]
        )

tiers = {}
for f in fams:
    tiers[f["tier"]] = tiers.get(f["tier"], 0) + f["quota"]
print(f"{len(fams)} families -> {out.name}  |  tier quotas {tiers} = {sum(tiers.values())}")
