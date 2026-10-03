from __future__ import annotations

import io

import pytest

from generators.core.progress import PipelineReporter
from generators.core.progress import PipelineStage
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


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def test_pipeline_reporter_normalizes_child_output() -> None:
    output = io.StringIO()
    errors = io.StringIO()
    clock = _Clock()
    reporter = PipelineReporter(output=output, error_output=errors, clock=clock)

    reporter.start(domain="crm", profile="dev", total_stages=5)
    with reporter.stage(PipelineStage(1, 5, "dataset", "Build dataset")):
        print("[progress] Step 1/6: Validate configuration")
        print("ALL CHECKS PASSED")
        clock.value += 1.25
    reporter.finish(domain="crm", profile="dev", succeeded=True)

    text = output.getvalue()
    assert "00:00:00.000 | INFO  | pipeline" in text
    assert "[1/5] START  Build dataset" in text
    assert "dataset         | Step 1/6: Validate configuration" in text
    assert "dataset         | ALL CHECKS PASSED" in text
    assert "[1/5] DONE   Build dataset (00:00:01.250)" in text
    assert "SUCCESS domain=crm profile=dev elapsed=00:00:01.250" in text
    assert "[progress]" not in text
    assert errors.getvalue() == ""


def test_pipeline_reporter_marks_and_reraises_stage_failures() -> None:
    output = io.StringIO()
    errors = io.StringIO()
    clock = _Clock()
    reporter = PipelineReporter(output=output, error_output=errors, clock=clock)
    stage = PipelineStage(4, 5, "platform.upload", "Upload dataset")

    with pytest.raises(RuntimeError, match="network unavailable"):
        with reporter.stage(stage):
            print("WARNING: retry exhausted")
            clock.value += 2
            raise RuntimeError("network unavailable")

    assert "WARN  | platform.upload | WARNING: retry exhausted" in errors.getvalue()
    assert "ERROR | pipeline" in errors.getvalue()
    assert "[4/5] FAILED Upload dataset (00:00:02.000)" in errors.getvalue()
