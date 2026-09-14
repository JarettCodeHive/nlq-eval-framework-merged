"""Lightweight progress reporting for long-running generation commands."""

from __future__ import annotations

from collections.abc import Callable


class ProgressReporter:
    """Send concise progress messages to a configurable output function."""

    def __init__(self, output: Callable[[str], None] = print) -> None:
        self.output = output

    def report(self, message: str) -> None:
        """Emit one consistently formatted progress message."""

        self.output(f"[progress] {message}")

    def report_table(self, table_name: str, table: object) -> None:
        """Emit the generated or exported shape of a table-like object."""

        rows = len(table)  # type: ignore[arg-type]
        columns = len(table.columns)  # type: ignore[attr-defined]
        self.report(f"{table_name}: {rows:,} rows, {columns} columns")
