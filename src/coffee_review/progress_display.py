"""The scrape progress bar, drawn with Rich.

Implements the pipeline's ``ScrapeProgress`` Protocol. It's only drawn when the
console is a real terminal; piped or scheduled runs get the logs and summary
alone. Log lines printed through the same console appear above the bar.
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
    from rich.console import Console
    from rich.progress import Task

    from coffee_review.pipeline.progress import ScrapeOutcome


class PagesPerSecondColumn(ProgressColumn):
    """Pages finished per second, averaged by Rich over recent progress."""

    def render(self, task: Task) -> Text:
        if task.speed is None:
            return Text("–/s", style="progress.data.speed")
        return Text(f"{task.speed:.1f}/s", style="progress.data.speed")


class RichScrapeProgress:
    """A one-line bar: count, percent, pages/s, elapsed, time left, outcomes."""

    def __init__(self, console: Console) -> None:
        self._progress = Progress(
            TextColumn("scraping"),
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
        self._counts: dict[ScrapeOutcome, int] = {"saved": 0, "failed": 0, "gone": 0}

    def start(self, total: int) -> None:
        self._task = self._progress.add_task(
            "scrape", total=total, outcomes=self._outcomes()
        )
        self._progress.start()

    def advance(self, outcome: ScrapeOutcome) -> None:
        if self._task is None:
            return
        self._counts[outcome] += 1
        self._progress.update(self._task, advance=1, outcomes=self._outcomes())

    def finish(self) -> None:
        if self._task is not None:
            self._progress.stop()

    def _outcomes(self) -> str:
        c = self._counts
        return (
            f"[green]{c['saved']:,} saved[/] · [yellow]{c['failed']:,} failed[/]"
            f" · {c['gone']:,} gone"
        )
