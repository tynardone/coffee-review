"""Tests for coffee_review.progress_display: the Rich scrape progress bar."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from rich.console import Console

from coffee_review.progress_display import RichScrapeProgress

if TYPE_CHECKING:
    from coffee_review.pipeline.progress import ScrapeProgress

# mypy checks this assignment: the display must satisfy the pipeline's Protocol.
_display: type[ScrapeProgress] = RichScrapeProgress


def terminal(width: int = 140) -> tuple[Console, io.StringIO]:
    """A Rich console that behaves like a terminal but writes to a string."""
    out = io.StringIO()
    return Console(file=out, force_terminal=True, width=width, color_system=None), out


def test_bar_shows_counts_and_outcomes() -> None:
    console, out = terminal()
    bar = RichScrapeProgress(console)

    bar.start(3)
    bar.advance("saved")
    bar.advance("saved")
    bar.advance("failed")
    bar.finish()

    text = out.getvalue()
    assert "3/3" in text
    assert "2 saved" in text
    assert "1 failed" in text
    assert "0 gone" in text


def test_nothing_drawn_when_not_a_terminal() -> None:
    out = io.StringIO()
    bar = RichScrapeProgress(Console(file=out, force_terminal=False))

    bar.start(3)
    bar.advance("saved")
    bar.finish()

    assert out.getvalue() == ""


def test_finish_without_start_is_harmless() -> None:
    console, out = terminal()

    RichScrapeProgress(console).finish()

    assert out.getvalue() == ""
