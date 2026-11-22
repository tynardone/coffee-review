"""Tests for coffee_review.progress_display: the Rich scrape progress bar."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from coffee_review.progress_display import RichRunProgress

if TYPE_CHECKING:
    from coffee_review.pipeline.progress import RunProgress

# mypy checks this assignment: the display must satisfy the pipeline's Protocol.
_display: type[RunProgress] = RichRunProgress


def terminal(width: int = 140) -> tuple[Console, io.StringIO]:
    """A Rich console that behaves like a terminal but writes to a string."""
    out = io.StringIO()
    return Console(file=out, force_terminal=True, width=width, color_system=None), out


def scrape_bar(console: Console) -> RichRunProgress:
    return RichRunProgress(
        console, label="scraping", outcomes=("saved", "failed", "gone")
    )


def test_bar_shows_counts_and_outcomes() -> None:
    console, out = terminal()
    bar = scrape_bar(console)

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
    bar = scrape_bar(Console(file=out, force_terminal=False))

    bar.start(3)
    bar.advance("saved")
    bar.finish()

    assert out.getvalue() == ""


def test_finish_without_start_is_harmless() -> None:
    console, out = terminal()

    scrape_bar(console).finish()

    assert out.getvalue() == ""


def test_custom_label_and_outcomes_shown() -> None:
    console, out = terminal()
    bar = RichRunProgress(
        console, label="parsing", outcomes=("roasted", "green", "issue")
    )

    bar.start(3)
    bar.advance("roasted")
    bar.advance("roasted")
    bar.advance("green")
    bar.finish()

    text = out.getvalue()
    assert "parsing" in text
    assert "2 roasted" in text
    assert "1 green" in text
    assert "0 issue" in text


def test_unknown_outcome_rejected() -> None:
    console, _ = terminal()
    bar = scrape_bar(console)
    bar.start(1)

    with pytest.raises(ValueError, match="roasted"):
        bar.advance("roasted")
    bar.finish()
