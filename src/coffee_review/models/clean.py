"""The clean stage's contract: the clean tables' columns, types and checks.

M3 turns the parsed text into typed columns. This module says what a clean
table must look like, once, so the pipeline can check every run against it
before anything is written (see the M3 design, "The contract").

The contract is a pandera ``DataFrameModel`` per table, using pandera's Polars
backend. Each model lists its columns in table order, with their types and
checks; ``strict`` refuses any other column and ``ordered`` fixes the order.
The plain Polars schemas (``ROASTED_SCHEMA`` and friends) are worked out from
the models, so there's one source for both.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any, Literal, get_args

import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaErrors

if TYPE_CHECKING:
    from pandera.api.polars.types import PolarsData

# --- Rules -------------------------------------------------------------------
#
# Every change the clean stage makes to a value is logged under one of these
# names (see transforms/rules.py). The names are fixed so the report can count
# them and the contract can check them.

type CleanRule = Literal[
    "unknown_marker",  # NA, na, n/a, NR or blank: the site says "unknown"
    "not_rated",  # rating NR
    "word_score",  # a score written as a word (acidity "Low")
    "labelled_score",  # "Flavor in milk: 5" → 5
    "not_measured",  # Agtron 0
    "template_label",  # Agtron "g/wb": the template's label, not a reading
    "swapped_2002_2003",  # Agtron printed ground/whole, swapped back
    "typo_corrected",  # Agtron typo with a known pattern, corrected (6489/89 → 64)
    "unswapped_in_window",  # in the swap window but not reversed: suspect
    "suspect_value",  # kept as written, but doubtful
    "not_disclosed",  # origin "Not disclosed"
    "unplaced",  # no country found
    "multiple_origins",  # more than one country, or a broad area or blend
    "not_a_price",  # NA, Not Available, See website…
    "no_quantity",  # an amount with no quantity
    "unparsed",  # no known shape; the value is null, the text is in the log
    "unknown_currency",  # a currency spelling not in the lookup
    "ambiguous_currency",  # ¥ from a roaster outside Japan and China
    "currency_from_roaster",  # $ or ¥ read using the roaster's country
    "unknown_unit",  # a unit spelling not in the lookup
    "zero_width_removed",  # invisible zero-width characters removed
]
CLEAN_RULES: tuple[str, ...] = get_args(CleanRule.__value__)

# --- Shared value lists ------------------------------------------------------

ROAST_LEVELS = ("Light", "Medium-Light", "Medium", "Medium-Dark", "Dark", "Very Dark")
RoastLevel = pl.Enum(ROAST_LEVELS)

type CleanTable = Literal["roasted", "green", "log"]


# Field helpers. ``pa.Field`` returns ``Any`` so it fits any annotation; these
# do the same.


def _optional() -> Any:
    return pa.Field(nullable=True)


def _score() -> Any:
    return pa.Field(nullable=True, ge=1, le=10)


def _agtron() -> Any:
    return pa.Field(nullable=True, gt=0, le=7000)


def _positive() -> Any:
    return pa.Field(nullable=True, gt=0)


# --- The tables --------------------------------------------------------------
#
# Each table is written out in full, in column order, rather than built from a
# shared base class: pandera puts inherited columns first, and the roasted and
# green orders differ near the top (roasted has sold_green after delisted).


class CleanRoastedFrame(pa.DataFrameModel):
    """The clean roasted table (espresso included)."""

    slug: str = pa.Field(unique=True)
    url: str
    delisted: bool
    sold_green: bool
    title: str = _optional()
    roaster: str = _optional()
    roaster_url: str = _optional()
    review_month: dt.date = pa.Field(nullable=True)
    rating: pl.Int16 = pa.Field(nullable=True, ge=50, le=100)
    roaster_city: str = _optional()
    roaster_region: str = _optional()
    roaster_country: str = _optional()
    origin_country: str = _optional()
    origin_region: str = _optional()
    roast_level: pl.Enum = pa.Field(
        nullable=True, dtype_kwargs={"categories": list(ROAST_LEVELS)}
    )
    agtron_whole: pl.Int16 = _agtron()
    agtron_ground: pl.Int16 = _agtron()
    agtron_swapped: bool
    agtron_corrected: bool
    agtron_suspect: bool
    price_amount: float = _positive()
    price_currency: str = pa.Field(nullable=True, str_matches=r"^[A-Z]{3}$")
    price_currency_inferred: bool
    price_quantity: float = _positive()
    price_unit: str = _optional()
    price_grams: float = _positive()
    price_note: str = _optional()
    aroma: pl.Float32 = _score()
    acidity: pl.Float32 = _score()
    body: pl.Float32 = _score()
    flavor: pl.Float32 = _score()
    aftertaste: pl.Float32 = _score()
    with_milk: pl.Float32 = _score()
    blind_assessment: str = _optional()
    notes: str = _optional()
    bottom_line: str = _optional()
    extra_count: pl.Int32

    class Config:
        strict = True  # no other columns
        ordered = True  # in this order

    @pa.check("review_month")
    @classmethod
    def first_of_month(cls, data: PolarsData) -> pl.LazyFrame:
        return _first_of_month(data)


class CleanGreenFrame(pa.DataFrameModel):
    """The clean green-coffee table."""

    slug: str = pa.Field(unique=True)
    url: str
    delisted: bool
    title: str = _optional()
    roaster: str = _optional()
    roaster_url: str = _optional()
    review_month: dt.date = pa.Field(nullable=True)
    rating: pl.Int16 = pa.Field(nullable=True, ge=50, le=100)
    origin_country: str = _optional()
    origin_region: str = _optional()
    price_amount: float = _positive()
    price_currency: str = pa.Field(nullable=True, str_matches=r"^[A-Z]{3}$")
    price_currency_inferred: bool
    price_quantity: float = _positive()
    price_unit: str = _optional()
    price_grams: float = _positive()
    price_note: str = _optional()
    harvest_year: pl.Int16 = _optional()
    harvest_text: str = _optional()
    seller: str = _optional()
    variety: str = _optional()
    lot_size_kg: float = _positive()
    lot_size_text: str = _optional()
    processing_method: str = _optional()
    lot_id: str = _optional()
    blind_assessment: str = _optional()
    notes: str = _optional()
    bottom_line: str = _optional()
    extra_count: pl.Int32

    class Config:
        strict = True  # no other columns
        ordered = True  # in this order

    @pa.check("review_month")
    @classmethod
    def first_of_month(cls, data: PolarsData) -> pl.LazyFrame:
        return _first_of_month(data)


class CleanLogFrame(pa.DataFrameModel):
    """The cleaning log: one row per rule applied to a field of a review."""

    slug: str
    dataset: str = pa.Field(isin=["roasted", "green"])
    field: str
    rule: str = pa.Field(isin=list(CLEAN_RULES))
    raw_value: str = _optional()

    class Config:
        strict = True  # no other columns
        ordered = True  # in this order


def _first_of_month(data: PolarsData) -> pl.LazyFrame:
    """``review_month`` is a month, stored as its first day."""
    return data.lazyframe.select(pl.col(data.key).dt.day() == 1)


_MODELS: dict[CleanTable, type[pa.DataFrameModel]] = {
    "roasted": CleanRoastedFrame,
    "green": CleanGreenFrame,
    "log": CleanLogFrame,
}


def _polars_schema(model: type[pa.DataFrameModel]) -> pl.Schema:
    columns = model.to_schema().columns
    return pl.Schema({name: column.dtype.type for name, column in columns.items()})


ROASTED_SCHEMA = _polars_schema(CleanRoastedFrame)
GREEN_SCHEMA = _polars_schema(CleanGreenFrame)
LOG_SCHEMA = _polars_schema(CleanLogFrame)


class CleanContractError(Exception):
    """A clean table broke its contract: a bug in a transform, not bad data."""


def validate_clean(frame: pl.DataFrame, table: CleanTable) -> pl.DataFrame:
    """``frame`` unchanged if it meets its table's contract.

    Every check runs (pandera's lazy mode), so one error lists every failure.
    """
    try:
        _MODELS[table].validate(frame, lazy=True)
    except SchemaErrors as errors:
        raise CleanContractError(_describe(table, errors)) from None
    return frame


def _describe(table: CleanTable, errors: SchemaErrors) -> str:
    cases = errors.failure_cases.select("column", "check").unique(maintain_order=True)
    lines = [f"  {column}: {check}" for column, check in cases.iter_rows()]
    return f"clean {table} table broke its contract:\n" + "\n".join(lines)
