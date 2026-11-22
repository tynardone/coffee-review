"""Small helpers for transform tests: one raw column in, the result out."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def run(
    transform: Callable[[pl.LazyFrame], pl.LazyFrame],
    field: str,
    values: Sequence[str | None],
    **other_columns: Sequence[object],
) -> pl.DataFrame:
    """Run ``transform`` over a frame with ``raw_<field>`` set to ``values``."""
    data: dict[str, Sequence[object]] = {
        "slug": [f"s{i}" for i in range(len(values))],
        f"raw_{field}": values,
        **other_columns,
    }
    text = {name: pl.String for name in data if name.startswith("raw_")}
    frame = pl.DataFrame(data, schema_overrides=text)  # parsed fields are text
    return transform(frame.lazy()).collect()
