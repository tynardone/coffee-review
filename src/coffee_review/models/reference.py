"""The reference stage's contracts: exchange rates and CPI (M4).

Three kinds of thing live here:

- **What a parser returns** (``FxParsed``, ``CpiParsed``): rows in project
  terms, whatever the source. A swapped source returns the same models.
- **The identity of a saved response** (``FxResponse``, ``CpiResponse``): what
  the raw store files a response under.
- **The tidy tables' contracts**: a pandera ``DataFrameModel`` per table, as
  in ``models/clean.py``, checked before anything is written.
"""

from __future__ import annotations

import datetime as dt
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

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
    """One month of a CPI series. ``final`` is False when the source marks the
    value preliminary, so it's asked for again later."""

    month: dt.date
    series: str
    index_value: float
    final: bool


class ParseProblem(FrozenModel):
    """A value a parser dropped, and why. The rest of the response is kept."""

    where: str
    value: str
    reason: str
    # The period the value was for, when known: a dropped value's month is
    # missing, not a gap, so it's asked for again.
    period: dt.date | None = None


class FxParsed(FrozenModel):
    """One day's response. ``gap`` is what the source said when it has no data
    for the day (``"not_available"``); then ``rates`` is empty."""

    day: dt.date
    rates: tuple[FxRate, ...]
    gap: str | None = None
    problems: tuple[ParseProblem, ...] = ()


class CpiUnavailable(FrozenModel):
    """A month the source says it doesn't have, in its own words (BLS marks
    October 2025 with ``-`` and footnote X: no data was collected)."""

    month: dt.date
    series: str
    said: str


class CpiParsed(FrozenModel):
    """One response's months for one series, and the months it says are
    unavailable."""

    series: str
    months: tuple[CpiMonth, ...]
    problems: tuple[ParseProblem, ...] = ()
    unavailable: tuple[CpiUnavailable, ...] = ()
    # What the source said alongside the data (BLS ``message``). A response
    # with a message may be partial, so no gaps are worked out from it.
    messages: tuple[str, ...] = ()


class ReferenceParseError(Exception):
    """A response that isn't a valid answer: not saved, asked for again."""


# --- Saved responses ---------------------------------------------------------


class FxResponse(FrozenModel):
    """A saved exchange-rate response: one per source and day."""

    source: str
    day: dt.date


class CpiResponse(FrozenModel):
    """A saved CPI response: one per request, named by when it was made (UTC)
    and the years it asked for, since a month can arrive in several."""

    source: str
    series: str
    fetched_at: UtcDatetime
    start_year: int
    end_year: int


# --- The tidy tables ---------------------------------------------------------

type ReferenceTable = Literal["fx", "cpi", "fx_gaps", "cpi_gaps"]

SOURCE = "Which source the row came from (its raw folder's name)."


def _text(description: str) -> Any:
    return pa.Field(description=description)


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
    source: str = _text(SOURCE)

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
    series: str = _text(
        "The series id (CUUR0000SA0: CPI-U, all items, US city average, not"
        " seasonally adjusted)."
    )
    index_value: float = pa.Field(
        description="The index for the month (1982–84 = 100 for CUUR0000SA0).",
        gt=0,
    )
    source: str = _text(SOURCE)

    class Config:
        strict = True
        ordered = True
        unique = ["month", "series"]  # noqa: RUF012

    @pa.check("month")
    @classmethod
    def first_of_month(cls, data: PolarsData) -> pl.LazyFrame:
        return first_of_month(data)


class FxGapFrame(pa.DataFrameModel):
    """Days with no rates in ``fx``: a source answered, but had none. Never
    asked for again. A gap row always means "no value in the table"."""

    day: dt.date = pa.Field(description="The day, in UTC: the 1st of a month.")
    source: str = _text(SOURCE)
    said: str = _text("What the source said (e.g. not_available).")

    class Config:
        strict = True
        ordered = True
        unique = ["day", "source"]  # noqa: RUF012

    @pa.check("day")
    @classmethod
    def first_of_month(cls, data: PolarsData) -> pl.LazyFrame:
        return first_of_month(data)


class CpiGapFrame(pa.DataFrameModel):
    """Months with no value in ``cpi``: the source will never have them
    (e.g. October 2025, not collected). A gap row always means "no value in
    the table"."""

    month: dt.date = pa.Field(description="The month, stored as its 1st.")
    series: str = _text("The series id.")
    source: str = _text(SOURCE)
    said: str = _text(
        "What the source said (BLS: its footnote), or 'absent from a response that"
        " has later months'."
    )

    class Config:
        strict = True
        ordered = True
        unique = ["month", "series", "source"]  # noqa: RUF012

    @pa.check("month")
    @classmethod
    def first_of_month(cls, data: PolarsData) -> pl.LazyFrame:
        return first_of_month(data)


_MODELS: Mapping[ReferenceTable, type[pa.DataFrameModel]] = MappingProxyType(
    {"fx": FxFrame, "cpi": CpiFrame, "fx_gaps": FxGapFrame, "cpi_gaps": CpiGapFrame}
)


def _polars_schema(model: type[pa.DataFrameModel]) -> pl.Schema:
    columns = model.to_schema().columns
    return pl.Schema({name: column.dtype.type for name, column in columns.items()})


FX_SCHEMA = _polars_schema(FxFrame)
CPI_SCHEMA = _polars_schema(CpiFrame)
FX_GAPS_SCHEMA = _polars_schema(FxGapFrame)
CPI_GAPS_SCHEMA = _polars_schema(CpiGapFrame)
SCHEMAS: Mapping[ReferenceTable, pl.Schema] = MappingProxyType(
    {
        "fx": FX_SCHEMA,
        "cpi": CPI_SCHEMA,
        "fx_gaps": FX_GAPS_SCHEMA,
        "cpi_gaps": CPI_GAPS_SCHEMA,
    }
)


class ReferenceContractError(Exception):
    """A reference table broke its contract: a bug, since parsers drop bad values."""


def validate_reference(frame: pl.DataFrame, table: ReferenceTable) -> pl.DataFrame:
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
        message = f"reference {table} table broke its contract:\n" + "\n".join(lines)
        raise ReferenceContractError(message) from None
    return frame
