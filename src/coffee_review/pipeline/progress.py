"""How the scrape reports progress, without knowing how it's displayed.

The pipeline calls a ``ScrapeProgress``; the CLI decides what that looks like
(a Rich progress bar, in ``coffee_review.progress_display``). Tests pass a
recording fake. ``NoProgress`` is the silent default.
"""

from __future__ import annotations

from typing import Literal, Protocol

# What happened to one review page, as far as progress is concerned.
type ScrapeOutcome = Literal["saved", "failed", "gone"]


class ScrapeProgress(Protocol):
    def start(self, total: int) -> None:
        """Fetching begins; ``total`` pages are planned for this run."""
        ...

    def advance(self, outcome: ScrapeOutcome) -> None:
        """One page's result has been recorded."""
        ...

    def finish(self) -> None:
        """The run is over, however it ended. Called exactly once."""
        ...


class NoProgress:
    """Reports nothing."""

    def start(self, total: int) -> None:
        pass

    def advance(self, outcome: ScrapeOutcome) -> None:
        pass

    def finish(self) -> None:
        pass
