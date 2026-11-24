"""Tests for coffee_review.models.reference: the reference-table contracts."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import polars as pl
import pytest

from coffee_review.models.reference import (
    CPI_SCHEMA,
    FX_SCHEMA,
    ReferenceContractError,
    validate_reference,
)

if TYPE_CHECKING:
    from coffee_review.models.reference import ReferenceTable

ROWS: dict[str, dict[str, object]] = {
    "fx": {"day": date(2020, 1, 1), "currency": "TWD", "units_per_usd": 30.1},
    "cpi": {"month": date(2020, 1, 1), "series": "CUUR0000SA0", "index_value": 257.971},
}
SCHEMAS = {"fx": FX_SCHEMA, "cpi": CPI_SCHEMA}


def frame(table: str, *rows: dict[str, object], **changes: object) -> pl.DataFrame:
    chosen = list(rows) or [ROWS[table] | changes]
    return pl.DataFrame(chosen, schema=SCHEMAS[table])


@pytest.mark.parametrize("table", ["fx", "cpi"])
def test_each_valid_frame_passes(table: ReferenceTable) -> None:
    valid = frame(table)

    assert validate_reference(valid, table).equals(valid)


@pytest.mark.parametrize(
    ("table", "bad", "column"),
    [
        pytest.param(
            "fx", frame("fx", units_per_usd=0.0), "units_per_usd", id="rate-0"
        ),
        pytest.param("fx", frame("fx", currency="twd"), "currency", id="lowercase"),
        pytest.param("fx", frame("fx", day=date(2020, 1, 2)), "day", id="not-1st"),
        pytest.param(
            "cpi", frame("cpi", index_value=-1.0), "index_value", id="cpi-neg"
        ),
        pytest.param(
            "cpi", frame("cpi", month=date(2020, 1, 15)), "month", id="cpi-1st"
        ),
    ],
)
def test_each_violation_fails(
    table: ReferenceTable, bad: pl.DataFrame, column: str
) -> None:
    with pytest.raises(ReferenceContractError, match=column):
        validate_reference(bad, table)


@pytest.mark.parametrize("table", ["fx", "cpi"])
def test_duplicate_key_fails(table: ReferenceTable) -> None:
    with pytest.raises(ReferenceContractError):
        validate_reference(frame(table, ROWS[table], ROWS[table]), table)


def test_same_day_other_currency_passes() -> None:
    valid = frame("fx", ROWS["fx"], ROWS["fx"] | {"currency": "CAD"})

    assert validate_reference(valid, "fx").equals(valid)


def test_schemas() -> None:
    assert (
        pl.Schema({"day": pl.Date, "currency": pl.String, "units_per_usd": pl.Float64})
        == FX_SCHEMA
    )
    assert (
        pl.Schema({"month": pl.Date, "series": pl.String, "index_value": pl.Float64})
        == CPI_SCHEMA
    )
