"""The economic stage's contracts: exchange rates and CPI (M4).

- **What a parser returns** (``FxParsed``, ``CpiParsed``): rows in project
  terms.
- **A saved CPI response's name** (``CpiFile``): when it was fetched and the
  years it asked for.
- **The tidy tables' contracts**: a pandera ``DataFrameModel`` per table, as
  in ``models/clean.py``, checked before anything is written.
"""

from __future__ import annotations

import datetime as dt
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaErrors

from coffee_review.models.common import FrozenModel, UtcDatetime
from coffee_review.models.frames import first_of_month

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pandera.api.polars.types import PolarsData

# --- What a parser returns ---------------------------------------------------


class FxRate(FrozenModel):
    """Units of one currency that one US dollar bought on a day."""

    day: dt.date
    currency: str
    units_per_usd: float


class CpiMonth(FrozenModel):
    """One month of a CPI series."""

    month: dt.date
    series: str
    index_value: float


class ParseProblem(FrozenModel):
    """A value a parser dropped, and why. The rest of the response is kept."""

    where: str
    value: str
    reason: str


class FxParsed(FrozenModel):
    """One day's response. ``gap`` is what the source said when it has no data
    for the day (``"not_available"``); then ``rates`` is empty."""

    day: dt.date
    rates: tuple[FxRate, ...]
    gap: str | None = None
    problems: tuple[ParseProblem, ...] = ()


class CpiParsed(FrozenModel):
    """One response's months for one series.

    ``unavailable`` are months the source says it will never have (BLS
    footnote X). ``messages`` are what the source said alongside the data.
    """

    series: str
    months: tuple[CpiMonth, ...]
    unavailable: tuple[dt.date, ...] = ()
    messages: tuple[str, ...] = ()
    problems: tuple[ParseProblem, ...] = ()


class EconomicParseError(Exception):
    """A response that isn't a valid answer: not saved, asked for again."""


class CpiFile(FrozenModel):
    """A saved CPI response: when it was fetched (UTC) and the years it asked
    for. A month can arrive in several; the newest wins."""

    fetched_at: UtcDatetime
    start_year: int
    end_year: int


# --- The tidy tables ---------------------------------------------------------

type EconomicTable = Literal["fx", "cpi"]


class FxFrame(pa.DataFrameModel):
    """Exchange rates: one row per day and currency."""

    day: dt.date = pa.Field(
        description="The day the rate is for, in UTC: always the 1st of a month."
    )
    currency: str = pa.Field(
        description="ISO 4217 currency code.", str_matches=r"^[A-Z]{3}$"
    )
    units_per_usd: float = pa.Field(
        description="Units of the currency one US dollar bought that day.", gt=0
    )

    class Config:
        strict = True
        ordered = True
        unique = ["day", "currency"]  # noqa: RUF012 (pandera reads a list)

    @pa.check("day")
    @classmethod
    def first_of_month(cls, data: PolarsData) -> pl.LazyFrame:
        return first_of_month(data)


class CpiFrame(pa.DataFrameModel):
    """CPI: one row per series and month."""

    month: dt.date = pa.Field(description="The month, stored as its 1st.")
    series: str = pa.Field(
        description=(
            "The series id (CUUR0000SA0: CPI-U, all items, US city average, not"
            " seasonally adjusted)."
        )
    )
    index_value: float = pa.Field(
        description="The index for the month (1982–84 = 100 for CUUR0000SA0).",
        gt=0,
    )

    class Config:
        strict = True
        ordered = True
        unique = ["month", "series"]  # noqa: RUF012

    @pa.check("month")
    @classmethod
    def first_of_month(cls, data: PolarsData) -> pl.LazyFrame:
        return first_of_month(data)


_MODELS: Mapping[EconomicTable, type[pa.DataFrameModel]] = MappingProxyType(
    {"fx": FxFrame, "cpi": CpiFrame}
)


def _polars_schema(model: type[pa.DataFrameModel]) -> pl.Schema:
    columns = model.to_schema().columns
    return pl.Schema({name: column.dtype.type for name, column in columns.items()})


FX_SCHEMA = _polars_schema(FxFrame)
CPI_SCHEMA = _polars_schema(CpiFrame)


class EconomicContractError(Exception):
    """An economic table broke its contract: a bug, since parsers drop bad values."""


def validate_economic(frame: pl.DataFrame, table: EconomicTable) -> pl.DataFrame:
    """``frame`` unchanged if it meets its table's contract.

    Every check runs (pandera's lazy mode), so one error lists every failure.
    """
    try:
        _MODELS[table].validate(frame, lazy=True)
    except SchemaErrors as errors:
        cases = errors.failure_cases.select("column", "check").unique(
            maintain_order=True
        )
        lines = [f"  {column}: {check}" for column, check in cases.iter_rows()]
        message = f"economic {table} table broke its contract:\n" + "\n".join(lines)
        raise EconomicContractError(message) from None
    return frame
