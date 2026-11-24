"""How a stage reports progress, without knowing how it's displayed.

A pipeline calls a ``RunProgress``; the CLI decides what that looks like (a
Rich progress bar, in ``coffee_review.progress_display``). Tests pass a
recording fake. ``NoProgress`` is the silent default.

Each stage names its own outcomes: scrape reports ``saved``, ``failed`` and
``gone``; parse reports ``roasted``, ``green`` and ``issue``.
"""

from __future__ import annotations

from typing import Literal, Protocol, override

# The scrape stage's outcomes, for its own type checking.
type ScrapeOutcome = Literal["saved", "failed", "gone"]


class RunProgress(Protocol):
    """How a stage reports progress, without knowing how it's shown.

    The CLI passes a display; tests and quiet runs pass ``NoProgress``.
    """

    def start(self, total: int) -> None:
        """Work begins; ``total`` items are planned for this run."""
        ...

    def advance(self, outcome: str) -> None:
        """One item has finished, with this outcome."""
        ...

    def finish(self) -> None:
        """The run is over, however it ended. Called exactly once."""
        ...


class NoProgress(RunProgress):
    """Reports nothing."""

    @override
    def start(self, total: int) -> None:
        pass

    @override
    def advance(self, outcome: str) -> None:
        pass

    @override
    def finish(self) -> None:
        pass
