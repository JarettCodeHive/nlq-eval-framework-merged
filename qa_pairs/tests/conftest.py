import csv
import os
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))  # utils/
sys.path.insert(0, str(BASE / "generator" / "crm"))  # scale_pairs, gen_family_matrix

from utils.dataset_source import resolve_dataset_source  # noqa: E402
from utils.output_paths import resolve_qa_output_dir  # noqa: E402

PROFILE = "full"
QA_DIR = resolve_qa_output_dir(BASE, PROFILE, "crm")
DB = BASE / "dataset" / f"crm_{PROFILE}.duckdb"

SOURCE = resolve_dataset_source(BASE, PROFILE, "crm")
AUTH_DDL = SOURCE.ddl_path
LOAD_ORDER = [
    "accounts",
    "campaigns",
    "contacts",
    "contact_campaigns",
    "interactions",
    "support_cases",
]
COPY_OPTS = (
    "(FORMAT CSV, HEADER, DELIMITER ',', NULL '', "
    "DATEFORMAT '%Y-%m-%d', TIMESTAMPFORMAT '%Y-%m-%dT%H:%M:%S')"
)

# In release / CI mode a missing build is a hard failure, not a skip.
RELEASE = os.environ.get("QA_RELEASE") == "1"
_missing = not (QA_DIR / "crm_qa_pairs.csv").exists() or not DB.exists()

if RELEASE and _missing:
    raise RuntimeError(
        "QA_RELEASE=1 but release artifacts are missing - run the full "
        "pipeline (generate_crm -> validate_crm -> scale_pairs -> "
        "author_qa_pairs -> rephrase) for the full profile first."
    )

_needs_build = pytest.mark.skipif(
    _missing,
    reason="run generate_crm.py + scale_pairs.py --profile full first",
)


def _read(name):
    with (QA_DIR / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="session")
def pairs():
    return _read("crm_qa_pairs.csv")


@pytest.fixture(scope="session")
def companion():
    return _read("crm_qa_pairs_companion.csv")


@pytest.fixture(scope="session")
def con():
    from utils.duckdb_io import connect_typed

    c = connect_typed(DB)
    yield c
    c.close()


@pytest.fixture(scope="session")
def authoritative_con():
    """Build an in-memory DuckDB from the configured full generator output."""
    import duckdb

    for name in LOAD_ORDER:
        p = SOURCE.csv_dir / f"{name}.csv"
        if not p.exists():
            pytest.skip(f"authoritative source missing: {p}")
    if not AUTH_DDL.exists():
        pytest.skip(f"authoritative source missing: {AUTH_DDL}")

    c = duckdb.connect(":memory:")
    c.execute(AUTH_DDL.read_text(encoding="utf-8"))
    for name in LOAD_ORDER:
        csv_path = (SOURCE.csv_dir / f"{name}.csv").as_posix()
        c.execute(f"COPY {name} FROM '{csv_path}' {COPY_OPTS};")
    yield c
    c.close()


def pytest_collection_modifyitems(config, items):
    for item in items:
        fx = getattr(item, "fixturenames", ())
        if {"pairs", "companion", "con", "authoritative_con"} & set(fx):
            item.add_marker(_needs_build)
