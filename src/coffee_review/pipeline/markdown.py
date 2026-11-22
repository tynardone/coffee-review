"""Small helpers shared by the Markdown reports (parse and clean)."""

from __future__ import annotations


def cell(text: str) -> str:
    """Text safe inside a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")
