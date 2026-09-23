"""The request bodies we build must match what the Studio UI actually sent.

The fixtures here are lifted verbatim from a captured `accounts.csv` import
against org 4104. If these pass, our create-entity and bulk-load payloads are
byte-identical to the ones the platform is known to accept — which is the only
evidence available, since the published Swagger does not cover this API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio import schema

# --- captured from the Studio UI, unmodified ---------------------------------

CAPTURED_FIELDS = [
    {
        "Name": "account_id",
        "Description": "",
        "Default": "",
        "Type": {
            "Name": "Number",
            "Class": "Number",
            "Implementation": {"Type": "float64"},
        },
        "Optional": True,
        "Format": {"NumberFormat": "UserInput"},
        "Relation": None,
        "Virtual": False,
        "DisplayOrder": 0,
        "DisplayLabel": "account_id",
    },
    {
        "Name": "account_name",
        "Description": "",
        "Default": "",
        "Type": {"Name": "Short Text", "Class": "Text", "Implementation": None},
        "Optional": True,
        "Format": None,
        "Relation": None,
        "Virtual": False,
        "DisplayOrder": 0,
        "DisplayLabel": "account_name",
    },
]

CAPTURED_FIRST_ROW = {
    "Fields": {
        "account_id": 1,
        "account_name": "Williams Ltd",
        "account_size": 24,
        "industry": "Healthcare",
        "region": "West",
        "customer_tier": "Preferred",
        "is_active": "true",
        "created_at": "2025-12-22T07:21:20",
    }
}

ACCOUNTS_COLUMNS = [
    "account_id",
    "account_name",
    "account_size",
    "industry",
    "region",
    "customer_tier",
    "is_active",
    "created_at",
]
# The UI typed exactly these two as numbers and left the rest as text — note
# that `is_active` and `created_at` are text, not boolean and timestamp.
ACCOUNTS_NUMERIC = {"account_id", "account_size"}

REPO_ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS_CSV = REPO_ROOT / "release" / "crm" / "dataset-v1.0.0" / "accounts.csv"


def test_field_definitions_match_the_captured_import() -> None:
    assert schema.field_definition("account_id", numeric=True) == CAPTURED_FIELDS[0]
    assert schema.field_definition("account_name", numeric=False) == CAPTURED_FIELDS[1]


def test_entity_definition_matches_the_captured_envelope() -> None:
    definition = schema.entity_definition(
        "accounts", ACCOUNTS_COLUMNS, ACCOUNTS_NUMERIC
    )

    assert definition["Name"] == "accounts"
    assert definition["DisplayFields"] == []
    assert definition["RelationshipDef"] is False
    assert definition["Fields"][:2] == CAPTURED_FIELDS
    # Field order follows the CSV header, as the UI's did.
    assert [f["Name"] for f in definition["Fields"]] == ACCOUNTS_COLUMNS


def test_numeric_inference_reproduces_the_uis_choices() -> None:
    rows = [
        {
            "account_id": "1",
            "account_name": "Williams Ltd",
            "account_size": "24",
            "industry": "Healthcare",
            "region": "West",
            "customer_tier": "Preferred",
            "is_active": "true",
            "created_at": "2025-12-22T07:21:20",
        }
    ]

    assert schema.numeric_columns(rows, ACCOUNTS_COLUMNS) == ACCOUNTS_NUMERIC


def test_a_nullable_numeric_column_is_still_numeric() -> None:
    """`contacts.account_id` is a number that is sometimes absent.

    Letting a blank disqualify the column would type a numeric FK as text and
    quietly change how the platform joins on it.
    """

    rows = [{"account_id": "7"}, {"account_id": ""}, {"account_id": "9"}]

    assert schema.numeric_columns(rows, ["account_id"]) == {"account_id"}


def test_an_all_blank_column_is_not_numeric() -> None:
    rows = [{"note": ""}, {"note": "  "}]

    assert schema.numeric_columns(rows, ["note"]) == set()


@pytest.mark.parametrize(
    ("value", "numeric", "expected"),
    [
        ("1", True, 1),  # integral values stay ints, as the capture shows
        ("24.5", True, 24.5),
        ("", True, None),  # a blank numeric is null, never 0
        ("", False, None),
        ("true", False, "true"),  # already lowercase in the CSV; no transform
        ("2025-12-22T07:21:20", False, "2025-12-22T07:21:20"),
        ("  padded  ", False, "padded"),
    ],
)
def test_value_encoding(value: str, numeric: bool, expected: object) -> None:
    assert schema.encode_value(value, numeric=numeric) == expected


def test_encoded_row_matches_the_captured_first_row() -> None:
    row = {
        "account_id": "1",
        "account_name": "Williams Ltd",
        "account_size": "24",
        "industry": "Healthcare",
        "region": "West",
        "customer_tier": "Preferred",
        "is_active": "true",
        "created_at": "2025-12-22T07:21:20",
    }

    encoded = schema.encode_row(row, ACCOUNTS_COLUMNS, ACCOUNTS_NUMERIC)

    assert encoded == CAPTURED_FIRST_ROW
    # Byte-level too: an int rendered as 1.0 would be a different request.
    assert json.dumps(encoded) == json.dumps(CAPTURED_FIRST_ROW)


@pytest.mark.skipif(not ACCOUNTS_CSV.is_file(), reason="CRM release not built")
def test_the_real_accounts_csv_reproduces_the_capture_end_to_end() -> None:
    """Straight from the release CSV to the captured wire format."""

    columns, rows = schema.read_csv(ACCOUNTS_CSV)
    numeric = schema.numeric_columns(rows, columns)

    assert columns == ACCOUNTS_COLUMNS
    assert numeric == ACCOUNTS_NUMERIC
    assert schema.encode_row(rows[0], columns, numeric) == CAPTURED_FIRST_ROW
    assert schema.entity_definition("accounts", columns, numeric)["Fields"][:2] == (
        CAPTURED_FIELDS
    )


def test_batching_preserves_order_and_covers_every_row() -> None:
    rows = list(range(10_001))

    batches = list(schema.batched(rows, 4800))

    assert [len(b) for b in batches] == [4800, 4800, 401]
    assert [row for batch in batches for row in batch] == rows


def test_size_aware_batching_respects_both_caps() -> None:
    """A wide table must send fewer rows, not a payload nothing has accepted."""

    wide = [{"Fields": {"x": "y" * 200}} for _ in range(50)]

    batches = list(schema.batched_within(wide, max_rows=4800, max_bytes=1000))

    assert all(len(json.dumps(b)) <= 1200 for b in batches)
    assert sum(len(b) for b in batches) == 50
    assert len(batches) > 1  # the byte cap bound first, not the row cap


def test_row_cap_still_binds_for_narrow_rows() -> None:
    narrow = [{"Fields": {"a": 1}} for _ in range(10)]

    batches = list(schema.batched_within(narrow, max_rows=4, max_bytes=10**9))

    assert [len(b) for b in batches] == [4, 4, 2]


def test_an_oversized_single_row_is_still_sent() -> None:
    """A zero-row batch would loop forever."""

    rows = [{"Fields": {"x": "y" * 5000}}, {"Fields": {"x": "z"}}]

    batches = list(schema.batched_within(rows, max_rows=4800, max_bytes=100))

    assert [len(b) for b in batches] == [1, 1]


@pytest.mark.skipif(not ACCOUNTS_CSV.is_file(), reason="CRM release not built")
def test_accounts_still_batches_exactly_as_the_ui_did() -> None:
    """The byte cap must not undercut the payload the platform accepted.

    The captured import sent 24,000 accounts rows as five 4800-row requests.
    A default tighter than that would split them differently, and we would no
    longer be replaying a request sequence known to work.
    """

    from studio.config import DEFAULT_BATCH_BYTES, DEFAULT_BATCH_ROWS

    columns, rows = schema.read_csv(ACCOUNTS_CSV)
    numeric = schema.numeric_columns(rows, columns)
    encoded = [schema.encode_row(r, columns, numeric) for r in rows]

    batches = list(
        schema.batched_within(
            encoded, max_rows=DEFAULT_BATCH_ROWS, max_bytes=DEFAULT_BATCH_BYTES
        )
    )

    assert [len(b) for b in batches] == [4800] * 5
    assert max(len(json.dumps(b)) for b in batches) <= DEFAULT_BATCH_BYTES
