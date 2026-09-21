"""CSV export helpers for released dataset artifacts."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from generators.core.progress import ProgressReporter


@dataclass(frozen=True)
class CSVExportResult:
    """Result for one exported CSV file."""

    table_name: str
    path: Path
    rows: int
    columns: int


class CSVExporter:
    """Export pandas DataFrames using the v3 CSV contract."""

    def __init__(
        self,
        csv_format: dict[str, Any],
        progress: ProgressReporter | None = None,
    ) -> None:
        self.csv_format = csv_format
        self.progress = progress

    def export_tables(
        self,
        tables: dict[str, Any],
        table_order: tuple[str, ...],
        output_dir: Path,
    ) -> list[CSVExportResult]:
        """Export tables in configured order and return file metadata."""

        output_dir.mkdir(parents=True, exist_ok=True)
        results: list[CSVExportResult] = []
        for table_name in table_order:
            table = tables[table_name]
            output_path = output_dir / f"{table_name}.csv"
            normalized = self._normalize_table(table)
            tmp_path = output_path.with_suffix(".csv.tmp")
            normalized.to_csv(
                tmp_path,
                index=False,
                encoding=self._encoding(),
                lineterminator="\n",
            )
            tmp_path.replace(output_path)
            results.append(
                CSVExportResult(
                    table_name=table_name,
                    path=output_path,
                    rows=len(normalized),
                    columns=len(normalized.columns),
                )
            )
            if self.progress is not None:
                self.progress.report(
                    f"Exported {table_name}.csv: "
                    f"{len(normalized):,} rows, {len(normalized.columns)} columns"
                )
        return results

    def _normalize_table(self, table: Any) -> Any:
        normalized = table.copy(deep=True)
        null_value = self.csv_format["null_representation"]
        true_value = self.csv_format["boolean_true"]
        false_value = self.csv_format["boolean_false"]

        for column_name in normalized.columns:
            column = normalized[column_name]
            if str(column.dtype) == "bool":
                normalized[column_name] = column.map(
                    {True: true_value, False: false_value}
                )
            else:
                normalized[column_name] = column.map(
                    lambda value: null_value if _is_null(value) else value
                )
        return normalized

    def _encoding(self) -> str:
        if self.csv_format.get("include_bom", False):
            return "utf-8-sig"
        return self.csv_format["encoding"]


def ensure_release_can_be_written(output_dir: Path) -> None:
    """Refuse export if immutable release marker files already exist."""

    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        raise FileExistsError(
            f"Refusing to modify immutable release with manifest: {manifest_path}"
        )


def count_csv_rows(path: Path) -> int:
    """Count CSV data records while respecting quoted multiline fields."""

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"CSV has no header row: {path}") from exc
        if not header:
            raise ValueError(f"CSV has an empty header row: {path}")
        return sum(1 for _row in reader)


def _is_null(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return False
    try:
        return bool(value != value)
    except (TypeError, ValueError):
        return str(value) in {"NaT", "<NA>"}
