from __future__ import annotations

import csv
from dataclasses import replace
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from generators.crm.export import CRMCSVExporter
from generators.crm.generator import CRMBaseEntityGenerator


class _FakeColumn:
    def __init__(self, values: list[Any], dtype: str = "object") -> None:
        self.values = values
        self.dtype = dtype

    def map(self, mapper: Any) -> list[Any]:
        if isinstance(mapper, dict):
            return [mapper[value] for value in self.values]
        return [mapper(value) for value in self.values]


class _FakeTable:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        bool_columns: set[str] | None = None,
    ) -> None:
        self.rows = [dict(row) for row in rows]
        self.columns = list(rows[0]) if rows else []
        self.bool_columns = bool_columns or set()

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, column_name: str) -> _FakeColumn:
        dtype = "bool" if column_name in self.bool_columns else "object"
        return _FakeColumn([row[column_name] for row in self.rows], dtype=dtype)

    def __setitem__(self, column_name: str, values: list[Any]) -> None:
        for row, value in zip(self.rows, values, strict=True):
            row[column_name] = value

    def copy(self, deep: bool = True) -> "_FakeTable":
        return _FakeTable(self.rows, self.bool_columns)

    def to_csv(
        self,
        path: Any,
        index: bool,
        encoding: str,
        lineterminator: str,
    ) -> None:
        lines = [",".join(self.columns)]
        for row in self.rows:
            lines.append(",".join(str(row[column]) for column in self.columns))
        path.write_text(lineterminator.join(lines) + lineterminator, encoding=encoding)


def test_crm_exporter_loads_full_profile() -> None:
    exporter = CRMCSVExporter.for_profile("full")

    assert exporter.settings.is_release_profile
    assert exporter.output_dir.name == "dataset-v1.0.0"


def test_crm_export_refuses_dev_profile() -> None:
    exporter = CRMCSVExporter.for_profile("dev")

    with pytest.raises(ValueError, match="full profile"):
        exporter.export_full_profile_csvs()


def test_export_tables_writes_engagement_tables_in_table_order(
    tmp_path: Path,
) -> None:
    exporter = CRMCSVExporter.for_profile("full")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    (tmp_path / "opportunities.csv").write_text("retired\n", encoding="utf-8")
    tables = {
        table_name: _FakeTable(
            [{"id": 1, "is_active": True}], bool_columns={"is_active"}
        )
        for table_name in exporter.settings.table_order
    }

    results = exporter.export_tables(tables)

    assert [result.table_name for result in results] == [
        "accounts",
        "contacts",
        "campaigns",
        "contact_campaigns",
        "interactions",
        "support_cases",
    ]
    assert (tmp_path / "contact_campaigns.csv").exists()
    assert {path.name for path in tmp_path.glob("*.csv")} == {
        "accounts.csv",
        "contacts.csv",
        "campaigns.csv",
        "contact_campaigns.csv",
        "interactions.csv",
        "support_cases.csv",
    }
    assert (tmp_path / "contact_campaigns.csv").read_text(
        encoding="utf-8"
    ) == "id,is_active\n1,true\n"


def test_generated_csv_headers_match_configured_field_order(tmp_path: Path) -> None:
    if not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ):
        pytest.skip("numpy, pandas, and Faker are not installed")

    exporter = CRMCSVExporter.for_profile("dev")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    tables = CRMBaseEntityGenerator.for_profile("dev").generate_tables()

    exporter.export_tables(tables)

    for table_name in exporter.settings.table_order:
        with (tmp_path / f"{table_name}.csv").open(
            encoding="utf-8", newline=""
        ) as csv_file:
            header = next(csv.reader(csv_file))
        assert header == tables[table_name].columns.tolist()


def test_dev_export_writes_all_validated_stages(tmp_path: Path) -> None:
    if not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ):
        pytest.skip("numpy, pandas, and Faker are not installed")

    exporter = CRMCSVExporter.for_profile("dev")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)

    results = exporter.export_dev_previews()

    assert list(results) == ["base", "distributed", "imperfect"]
    for stage_name, stage_results in results.items():
        assert [result.table_name for result in stage_results] == list(
            exporter.settings.table_order
        )
        assert all(
            result.path.parent == tmp_path / stage_name for result in stage_results
        )
