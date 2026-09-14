from __future__ import annotations

from generators.core.progress import ProgressReporter


class _Table:
    def __init__(self) -> None:
        self.columns = ("id", "value")

    def __len__(self) -> int:
        return 3


def test_progress_reporter_formats_messages_and_table_shapes() -> None:
    messages: list[str] = []
    reporter = ProgressReporter(output=messages.append)

    reporter.report("Generating data")
    reporter.report_table("sample", _Table())

    assert messages == [
        "[progress] Generating data",
        "[progress] sample: 3 rows, 2 columns",
    ]
