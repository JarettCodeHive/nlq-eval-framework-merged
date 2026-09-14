from __future__ import annotations

import csv
from pathlib import Path

import pytest

from generators.core.csv_export import CSVExporter
from generators.core.csv_export import ensure_release_can_be_written
from generators.core.progress import ProgressReporter


CSV_FORMAT = {
    "encoding": "utf-8",
    "include_bom": False,
    "line_ending": "lf",
    "null_representation": "",
    "boolean_true": "true",
    "boolean_false": "false",
    "date_format": "YYYY-MM-DD",
    "timestamp_format": "YYYY-MM-DDTHH:MM:SS",
    "decimal_separator": ".",
    "thousands_separator": False,
}


def test_release_export_refuses_existing_manifest(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError, match="manifest"):
        ensure_release_can_be_written(tmp_path)


def test_csv_export_normalizes_booleans_and_nulls_when_pandas_is_installed(
    tmp_path: Path,
) -> None:
    try:
        import pandas as pd
    except ImportError:
        return

    table = pd.DataFrame(
        {
            "id": [1, 2],
            "is_active": [True, False],
            "optional_value": ["present", None],
        }
    )

    results = CSVExporter(CSV_FORMAT).export_tables(
        tables={"sample": table},
        table_order=("sample",),
        output_dir=tmp_path,
    )

    assert results[0].rows == 2
    content = (tmp_path / "sample.csv").read_text(encoding="utf-8")
    assert "True" not in content
    assert "False" not in content
    assert "true" in content
    assert "false" in content
    assert ",present\n" in content
    assert "false,\n" in content


def test_csv_export_preserves_null_like_strings_when_pandas_is_installed(
    tmp_path: Path,
) -> None:
    try:
        import pandas as pd
    except ImportError:
        return

    table = pd.DataFrame(
        {
            "id": [1, 2, 3, 4],
            "value": ["Nan", float("nan"), pd.NA, None],
        }
    )

    CSVExporter(CSV_FORMAT).export_tables(
        tables={"sample": table},
        table_order=("sample",),
        output_dir=tmp_path,
    )

    with (tmp_path / "sample.csv").open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert rows == [
        {"id": "1", "value": "Nan"},
        {"id": "2", "value": ""},
        {"id": "3", "value": ""},
        {"id": "4", "value": ""},
    ]


def test_csv_export_reports_each_completed_table(tmp_path: Path) -> None:
    try:
        import pandas as pd
    except ImportError:
        return

    messages: list[str] = []
    reporter = ProgressReporter(output=messages.append)
    table = pd.DataFrame({"id": [1, 2]})

    CSVExporter(CSV_FORMAT, progress=reporter).export_tables(
        tables={"sample": table},
        table_order=("sample",),
        output_dir=tmp_path,
    )

    assert messages == ["[progress] Exported sample.csv: 2 rows, 1 columns"]
