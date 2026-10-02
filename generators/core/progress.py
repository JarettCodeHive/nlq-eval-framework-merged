"""Console progress reporting for generation and end-to-end workflows.

``ProgressReporter`` remains the small reporter used inside generators.
``PipelineReporter`` is the orchestration boundary: it gives every line emitted
by a child command the same timestamp / level / component envelope, even when
that child still uses ordinary ``print`` calls.  Keeping that normalization at
the boundary makes the end-to-end UI coherent without coupling the generator,
Q&A, Studio, and judge packages to one another.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import threading
import time
from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TextIO


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


def _elapsed(value: float) -> str:
    """Format a monotonic duration as ``HH:MM:SS.mmm``."""

    milliseconds = max(0, round(value * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


_LEGACY_PREFIX = re.compile(r"^\[(?:progress|judge)\]\s*", re.IGNORECASE)
_ERROR_WORDS = re.compile(
    r"(?:^|\b)(?:error|fail|failed|failure|unverified|mismatch)(?:\b|$)",
    re.IGNORECASE,
)
_WARNING_WORDS = re.compile(
    r"(?:^|\b)(?:warning|warn|note|uncalibrated|unconfirmed)(?:\b|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PipelineStage:
    """One visible stage in the root end-to-end pipeline."""

    number: int
    total: int
    component: str
    title: str

    @property
    def label(self) -> str:
        return f"{self.number}/{self.total}"


class _PipelineLogStream(io.TextIOBase):
    """Line-buffer arbitrary child output into a ``PipelineReporter``."""

    def __init__(
        self,
        reporter: "PipelineReporter",
        component: str,
        default_level: str,
    ) -> None:
        self._reporter = reporter
        self._component = component
        self._default_level = default_level
        self._buffer = ""
        self._lock = threading.Lock()

    @property
    def encoding(self) -> str:
        return "utf-8"

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        if not text:
            return 0
        with self._lock:
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                self._emit(line)
        return len(text)

    def flush(self) -> None:
        with self._lock:
            if self._buffer:
                self._emit(self._buffer)
                self._buffer = ""

    def _emit(self, line: str) -> None:
        message = _LEGACY_PREFIX.sub("", line.rstrip())
        if not message:
            return
        level = self._default_level
        if _ERROR_WORDS.search(message):
            level = "ERROR"
        elif _WARNING_WORDS.search(message):
            level = "WARN"
        self._reporter.log(level, self._component, message)


class PipelineReporter:
    """Uniform, dependency-free console UI for ``run-pipeline``.

    The output is intentionally plain text rather than ANSI-coloured. It stays
    readable in a terminal, CI log, redirected file, and client-demo recording.
    """

    WIDTH = 88

    def __init__(
        self,
        *,
        output: TextIO | None = None,
        error_output: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.output = output if output is not None else sys.stdout
        self.error_output = error_output if error_output is not None else sys.stderr
        self.clock = clock
        self.started_at = clock()
        self._lock = threading.Lock()

    def log(self, level: str, component: str, message: str) -> None:
        """Write one or more consistently formatted log lines."""

        target = self.error_output if level in {"WARN", "ERROR"} else self.output
        elapsed = _elapsed(self.clock() - self.started_at)
        prefix = f"{elapsed} | {level:<5} | {component:<15} | "
        lines = str(message).splitlines() or [""]
        with self._lock:
            for line in lines:
                print(prefix + line, file=target, flush=True)

    def start(self, *, domain: str, profile: str, total_stages: int) -> None:
        """Print the run identity before the first stage starts."""

        rule = "=" * self.WIDTH
        self.log("INFO", "pipeline", rule)
        self.log("INFO", "pipeline", "NLQ EVALUATION PIPELINE")
        self.log(
            "INFO",
            "pipeline",
            f"domain={domain}  profile={profile}  stages={total_stages}",
        )
        self.log("INFO", "pipeline", rule)

    @contextlib.contextmanager
    def stage(self, stage: PipelineStage) -> Iterator[None]:
        """Capture a child command and report its complete lifecycle."""

        stage_started = self.clock()
        self.log("INFO", "pipeline", f"[{stage.label}] START  {stage.title}")
        stdout = _PipelineLogStream(self, stage.component, "INFO")
        stderr = _PipelineLogStream(self, stage.component, "WARN")
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                yield
        except BaseException as exc:
            stdout.flush()
            stderr.flush()
            duration = self.clock() - stage_started
            self.log(
                "ERROR",
                "pipeline",
                f"[{stage.label}] FAILED {stage.title} "
                f"({_elapsed(duration)}) — {type(exc).__name__}: {exc}",
            )
            raise
        else:
            stdout.flush()
            stderr.flush()
            duration = self.clock() - stage_started
            self.log(
                "INFO",
                "pipeline",
                f"[{stage.label}] DONE   {stage.title} ({_elapsed(duration)})",
            )

    def finish(self, *, domain: str, profile: str, succeeded: bool) -> None:
        """Print one unambiguous terminal status for the complete run."""

        elapsed = _elapsed(self.clock() - self.started_at)
        if succeeded:
            self.log(
                "INFO",
                "pipeline",
                f"SUCCESS domain={domain} profile={profile} elapsed={elapsed}",
            )
        else:
            self.log(
                "ERROR",
                "pipeline",
                f"FAILED domain={domain} profile={profile} elapsed={elapsed}",
            )
