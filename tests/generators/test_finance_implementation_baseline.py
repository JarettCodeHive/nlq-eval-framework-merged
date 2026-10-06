"""Protect CRM, Sales, and core behavior during Finance implementation.

Finance_Pre_Implementation_CRM_Sales_Core_Baseline.json and
scripts/capture_finance_implementation_baseline.py were both never
committed to this repository (verified via `git log --all
--diff-filter=A` for both paths). Unlike the Sales baseline, this one
isn't even reconstructable in principle: Sales and Finance were added in
the same commit (2b29ae2, "Added sales and finance dataset") - there was
never a point in this repository's history where Sales existed without
Finance, so the "post-Sales, pre-Finance" snapshot this module's name
describes never existed to capture.

Skip the whole module at collection time rather than letting the missing
import abort the entire pytest session (which is what happened before -
every other test file in the run would fail to collect alongside this
one unless `--ignore`d).
"""

from __future__ import annotations

import pytest


pytest.skip(
    "Finance_Pre_Implementation_CRM_Sales_Core_Baseline.json and "
    "scripts/capture_finance_implementation_baseline.py were never "
    "committed, and the snapshot this module is named for never existed "
    "in this repo's history (Sales and Finance shipped in the same "
    "commit, 2b29ae2) - see module docstring.",
    allow_module_level=True,
)
