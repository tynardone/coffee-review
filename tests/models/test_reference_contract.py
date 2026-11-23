"""Tests for coffee_review.models.reference: the reference-table contracts."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import polars as pl
import pytest

from coffee_review.models.reference import (
    CPI_GAPS_SCHEMA,
    CPI_SCHEMA,
    FX_GAPS_SCHEMA,
    FX_SCHEMA,
    ReferenceContractError,
    validate_reference,
)

if TYPE_CHECKING:
    from coffee_review.models.reference import ReferenceTable

ROWS: dict[str, dict[str, object]] = {
    "fx": {
        "day": date(2020, 1, 1),
        "currency": "TWD",
        "units_per_usd": 30.1,
        "source": "openexchangerates",
    },
    "cpi": {
        "month": date(2020, 1, 1),
        "series": "CUUR0000SA0",
        "index_value": 257.971,
        "source": "bls",
    },
    "fx_gaps": {
        "day": date(2020, 1, 1),
        "source": "openexchangerates",
        "said": "not_available",
    },
    "cpi_gaps": {
        "month": date(2025, 10, 1),
        "series": "CUUR0000SA0",
        "source": "bls",
        "said": "absent from a response that has later months",
    },
}
SCHEMAS = {
    "fx": FX_SCHEMA,
    "cpi": CPI_SCHEMA,
    "fx_gaps": FX_GAPS_SCHEMA,
    "cpi_gaps": CPI_GAPS_SCHEMA,
}


def frame(table: str, *rows: dict[str, object], **changes: object) -> pl.DataFrame:
    chosen = list(rows) or [ROWS[table] | changes]
    return pl.DataFrame(chosen, schema=SCHEMAS[table])


@pytest.mark.parametrize("table", ["fx", "cpi", "fx_gaps", "cpi_gaps"])
def test_each_valid_frame_passes(table: ReferenceTable) -> None:
    valid = frame(table)

    assert validate_reference(valid, table).equals(valid)


def test_fx_rejects_non_positive_rate() -> None:
    with pytest.raises(ReferenceContractError, match="units_per_usd"):
        validate_reference(frame("fx", units_per_usd=0.0), "fx")


def test_fx_rejects_lowercase_currency() -> None:
    with pytest.raises(ReferenceContractError, match="currency"):
        validate_reference(frame("fx", currency="twd"), "fx")


def test_fx_rejects_day_not_first() -> None:
    with pytest.raises(ReferenceContractError, match="day"):
        validate_reference(frame("fx", day=date(2020, 1, 2)), "fx")


def test_fx_rejects_duplicate_day_currency() -> None:
    with pytest.raises(ReferenceContractError):
        validate_reference(frame("fx", ROWS["fx"], ROWS["fx"]), "fx")


def test_fx_allows_same_day_other_currency() -> None:
    other = ROWS["fx"] | {"currency": "CAD"}
    valid = frame("fx", ROWS["fx"], other)

    assert validate_reference(valid, "fx").equals(valid)


def test_cpi_rejects_duplicate_month_series() -> None:
    with pytest.raises(ReferenceContractError):
        validate_reference(frame("cpi", ROWS["cpi"], ROWS["cpi"]), "cpi")


def test_cpi_rejects_month_not_first() -> None:
    with pytest.raises(ReferenceContractError, match="month"):
        validate_reference(frame("cpi", month=date(2020, 1, 15)), "cpi")


def test_cpi_gaps_reject_duplicate() -> None:
    row = ROWS["cpi_gaps"]
    with pytest.raises(ReferenceContractError):
        validate_reference(frame("cpi_gaps", row, row), "cpi_gaps")


def test_schemas_match_contracts() -> None:
    assert (
        pl.Schema(
            {
                "day": pl.Date,
                "currency": pl.String,
                "units_per_usd": pl.Float64,
                "source": pl.String,
            }
        )
        == FX_SCHEMA
    )
    assert (
        pl.Schema(
            {
                "month": pl.Date,
                "series": pl.String,
                "index_value": pl.Float64,
                "source": pl.String,
            }
        )
        == CPI_SCHEMA
    )
