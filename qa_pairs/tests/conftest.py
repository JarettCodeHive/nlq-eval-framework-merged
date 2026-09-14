import csv
import os
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))  # utils/
sys.path.insert(0, str(BASE / "generator"))  # scale_pairs, gen_family_matrix

PROFILE = "full"
QA_DIR = BASE / "qa_pairs" / PROFILE
DB = BASE / "dataset" / f"crm_{PROFILE}.duckdb"

# Authoritative source bundle (Track A delivery) - the clean-room tests
# rebuild from this, never from the generated dataset/.
SOURCE = BASE / "data" / "crm_dataset_v2"
AUTH_DDL = SOURCE / "crm_ddl.sql"
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
    """A fresh in-memory DuckDB built directly from the authoritative
    `data/crm_dataset_v2/` DDL + full-profile CSVs. Ground-truth tests run
    against this so they cannot be fooled by a stale or edited
    `dataset/crm_full.duckdb`."""
    import duckdb

    for name in LOAD_ORDER + ["crm_ddl.sql"]:
        p = (
            SOURCE
            / ("full" if name in LOAD_ORDER else "")
            / (f"{name}.csv" if name in LOAD_ORDER else name)
        )
        if not p.exists():
            pytest.skip(f"authoritative source missing: {p}")

    c = duckdb.connect(":memory:")
    c.execute(AUTH_DDL.read_text(encoding="utf-8"))
    for name in LOAD_ORDER:
        csv_path = (SOURCE / "full" / f"{name}.csv").as_posix()
        c.execute(f"COPY {name} FROM '{csv_path}' {COPY_OPTS};")
    yield c
    c.close()


def pytest_collection_modifyitems(config, items):
    for item in items:
        fx = getattr(item, "fixturenames", ())
        if {"pairs", "companion", "con", "authoritative_con"} & set(fx):
            item.add_marker(_needs_build)
