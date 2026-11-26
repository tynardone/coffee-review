"""Checks shared by the pandera table contracts (``clean.py``, ``economic.py``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from pandera.api.polars.types import PolarsData


def first_of_month(data: PolarsData) -> pl.LazyFrame:
    """The column is a month, stored as its first day."""
    return data.lazyframe.select(pl.col(data.key).dt.day() == 1)
