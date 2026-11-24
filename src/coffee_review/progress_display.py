"""The progress bar for CLI runs, drawn with Rich.

Implements the pipeline's ``RunProgress`` Protocol, so any stage can use it
with its own label and outcomes. It's only drawn when the console is a real
terminal; piped or scheduled runs get the logs and summary alone. Log lines
printed through the same console appear above the bar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.text import Text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import Console
    from rich.progress import Task


class PagesPerSecondColumn(ProgressColumn):
    """Items finished per second, averaged by Rich over recent progress."""

    def render(self, task: Task) -> Text:
        """The task's speed, or a dash before there is one."""
        if task.speed is None:
            return Text("–/s", style="progress.data.speed")
        return Text(f"{task.speed:.1f}/s", style="progress.data.speed")


class RichRunProgress:
    """A one-line progress bar for a stage.

    Shows the label, count, percent, items per second, elapsed time, time left,
    and a running count for each of the stage's outcomes.
    """

    def __init__(
        self, console: Console, *, label: str, outcomes: Sequence[str]
    ) -> None:
        self.label = label
        self._progress = Progress(
            TextColumn(label),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            PagesPerSecondColumn(),
            TimeElapsedColumn(),
            TextColumn("left"),
            TimeRemainingColumn(),
            TextColumn("{task.fields[outcomes]}"),
            console=console,
            disable=not console.is_terminal,
        )
        self._task: TaskID | None = None
        self._counts: dict[str, int] = dict.fromkeys(outcomes, 0)

    def start(self, total: int) -> None:
        """Show the bar, with ``total`` items planned."""
        self._task = self._progress.add_task(
            self.label, total=total, outcomes=self._outcomes()
        )
        self._progress.start()

    def advance(self, outcome: str) -> None:
        """Count one finished item under ``outcome``.

        Raises:
            ValueError: If ``outcome`` isn't one of the bar's outcomes.
        """
        if outcome not in self._counts:
            expected = ", ".join(self._counts)
            raise ValueError(f"unknown outcome {outcome!r}; expected one of {expected}")
        if self._task is None:
            return
        self._counts[outcome] += 1
        self._progress.update(self._task, advance=1, outcomes=self._outcomes())

    def finish(self) -> None:
        """Stop the bar, leaving its last line on screen."""
        if self._task is not None:
            self._progress.stop()

    def _outcomes(self) -> str:
        return " · ".join(f"{count:,} {name}" for name, count in self._counts.items())
