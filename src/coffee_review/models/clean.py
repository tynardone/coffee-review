"""The clean stage's contract: the clean tables' columns, types and checks.

The clean stage turns the parsed text into typed columns. This module says what a clean
table must look like, once, so the pipeline can check every run against it
before anything is written.

The contract is a pandera ``DataFrameModel`` per table, using pandera's Polars
backend. Each model lists its columns in table order, with their types and
checks; ``strict`` refuses any other column and ``ordered`` fixes the order.
The plain Polars schemas (``ROASTED_SCHEMA`` and friends) are worked out from
the models, so there's one source for both.
"""

from __future__ import annotations

import datetime as dt
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, get_args

import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaErrors

from coffee_review.models.frames import first_of_month

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pandera.api.polars.types import PolarsData

# --- Rules -------------------------------------------------------------------
#
# Every change the clean stage makes to a value is logged under one of these
# names (see transforms/rules.py). The names are fixed so the report can count
# them and the contract can check them.

type CleanRule = Literal[
    "unknown_marker",
    "not_rated",
    "word_score",
    "labelled_score",
    "not_measured",
    "template_label",
    "swapped_2002_2003",
    "typo_corrected",
    "unswapped_in_window",
    "suspect_value",
    "not_disclosed",
    "unplaced",
    "multiple_origins",
    "close_spelling",
    "spelling_variant",
    "listed_name",
    "possible_match",
    "not_a_price",
    "no_quantity",
    "unparsed",
    "unknown_currency",
    "ambiguous_currency",
    "currency_from_roaster",
    "unknown_unit",
    "zero_width_removed",
]
CLEAN_RULES: tuple[str, ...] = get_args(CleanRule.__value__)

# What each rule means, for the data dictionary and the report's readers.
RULE_DESCRIPTIONS: Mapping[str, str] = MappingProxyType(
    {
        "unknown_marker": (
            'The site\'s own "unknown": NA, na, n/a, NR or blank. The value is empty.'
        ),
        "not_rated": "Rating NR: the coffee wasn't rated.",
        "word_score": 'A score written as a word (acidity "Low"). The value is empty.',
        "labelled_score": 'A score written with a label ("Flavor in milk: 5" → 5).',
        "not_measured": "Agtron 0: not measured. The reading is empty.",
        "template_label": (
            'Agtron "g/wb": the template\'s label, printed where the numbers should be.'
        ),
        "swapped_2002_2003": (
            "Agtron printed ground-first (June 2002 – March 2003), swapped back."
        ),
        "typo_corrected": (
            "Agtron typo with a known pattern, corrected (6489/89 → 64/89)."
        ),
        "unswapped_in_window": (
            "In the Agtron swap window but not reversed: kept, flagged suspect."
        ),
        "suspect_value": (
            "Kept as written but doubtful, or out of range and left empty."
        ),
        "not_disclosed": '"Not disclosed": the site doesn\'t say. The value is empty.',
        "unplaced": (
            "No known place found. The value is empty; listed in the report to review."
        ),
        "multiple_origins": (
            'More than one country, or a blend: origin_country is "Multiple".'
        ),
        "close_spelling": (
            "Matched by a close spelling of a known place or unit (Calfornia → "
            "California), or merged into a very close roaster name (Lamill → "
            "La Mill); listed in the report to review."
        ),
        "spelling_variant": (
            "Another spelling of the same roaster name (case, accents, punctuation, "
            "words like Coffee Roasters). The kept spelling replaces it."
        ),
        "listed_name": "A roaster name set by hand in roasters.csv.",
        "possible_match": (
            "A roaster name close to another one, not merged: listed for a person "
            "to decide."
        ),
        "not_a_price": (
            "Not a price (NA, Not Available, See website…). The price is empty."
        ),
        "no_quantity": 'An amount with no quantity ("$15.00").',
        "unparsed": (
            "No known shape. The value is empty; the text is in the log and the report."
        ),
        "unknown_currency": (
            "A currency spelling not in the lookup tables. The currency is empty."
        ),
        "ambiguous_currency": (
            "A symbol whose currency the roaster's country can't settle (¥ outside "
            "Japan and China)."
        ),
        "currency_from_roaster": (
            "A currency read from the roaster's country ($ outside the US, ¥, E, "
            "pesos)."
        ),
        "unknown_unit": (
            'A unit not in the lookup tables (multipacks: "six 8-ounce cans"). '
            "Quantity and unit are empty."
        ),
        "zero_width_removed": "Invisible zero-width characters removed from the text.",
    }
)

# --- Shared value lists ------------------------------------------------------

ROAST_LEVELS = ("Light", "Medium-Light", "Medium", "Medium-Dark", "Dark", "Very Dark")
RoastLevel = pl.Enum(ROAST_LEVELS)

type CleanTable = Literal["roasted", "green", "log"]


# Field helpers. ``pa.Field`` returns ``Any`` so it fits any annotation; these
# do the same.


def _optional(description: str) -> Any:
    return pa.Field(description=description, nullable=True)


def _score(description: str) -> Any:
    return pa.Field(description=description, nullable=True, ge=1, le=10)


def _agtron(description: str) -> Any:
    return pa.Field(description=description, nullable=True, gt=0, le=7000)


def _positive(description: str) -> Any:
    return pa.Field(description=description, nullable=True, gt=0)


# --- The tables --------------------------------------------------------------
#
# Each table is written out in full, in column order, rather than built from a
# shared base class: pandera puts inherited columns first, and the roasted and
# green orders differ near the top (roasted has sold_green after delisted).


class CleanRoastedFrame(pa.DataFrameModel):
    """The clean roasted table (espresso included)."""

    slug: str = pa.Field(
        description="The review's id: the last part of its URL. Unique.", unique=True
    )
    url: str = pa.Field(description="The review page's address.")
    delisted: bool = pa.Field(
        description="True when the review is no longer in the site's sitemaps."
    )
    sold_green: bool = pa.Field(
        description="True when the title says the coffee was sold green (1997–1999)."
    )
    title: str = _optional("The coffee's name as reviewed.")
    roaster: str = _optional(
        "The roaster (green reviews: the importer or seller), one spelling per "
        "roaster; the cleaning log has the name as written."
    )
    roaster_url: str = _optional("The link on the roaster's logo.")
    review_month: dt.date = pa.Field(
        description=(
            "The month of the review, always stored as its 1st day (the site gives"
            " only month and year)."
        ),
        nullable=True,
    )
    rating: pl.Int16 = pa.Field(
        description="Overall score, 50–100. Empty when not rated (NR).",
        nullable=True,
        ge=50,
        le=100,
    )
    roaster_city: str = _optional("The roaster's city, as written.")
    roaster_region: str = _optional(
        "The roaster's state or province, by its standard name."
    )
    roaster_country: str = _optional(
        "The roaster's country (Hawaiʻi roasters: United States)."
    )
    origin_country: str = _optional(
        "Where the coffee was grown: one country (Hawaiʻi and Puerto Rico count as"
        ' their own), "Multiple" for a blend, empty if not disclosed.'
    )
    origin_region: str = _optional(
        "The growing region(s), comma-separated, without filler words; direction "
        'words kept ("Guji, Oromia", "North Sumatra").'
    )
    roast_level: pl.Enum = pa.Field(
        description=(
            "Roast level, ordered light to dark: Light, Medium-Light, Medium, "
            "Medium-Dark, Dark, Very Dark."
        ),
        nullable=True,
        dtype_kwargs={"categories": list(ROAST_LEVELS)},
    )
    agtron_whole: pl.Int16 = _agtron(
        "Agtron reading of the whole bean: higher is lighter."
    )
    agtron_ground: pl.Int16 = _agtron(
        "Agtron reading of the ground coffee: higher is lighter."
    )
    agtron_swapped: bool = pa.Field(
        description=(
            "True when the readings were printed ground-first (June 2002 – March 2003)"
            " and swapped back."
        )
    )
    agtron_corrected: bool = pa.Field(
        description=(
            "True when a typo with a known pattern was corrected (6489/89 → 64/89)."
        )
    )
    agtron_suspect: bool = pa.Field(
        description=(
            "True when a reading is kept as written but doubtful (a decimal, above "
            "150, or not reversed in the swap window)."
        )
    )
    price_amount: float = _positive("The price in its own currency (not converted).")
    price_currency: str = pa.Field(
        description="ISO 4217 currency code (USD, TWD, CAD…).",
        nullable=True,
        str_matches=r"^[A-Z]{3}$",
    )
    price_currency_inferred: bool = pa.Field(
        description=(
            "True when the currency was read from the roaster's country ($ outside the"
            " US, ¥, E, pesos), not written plainly."
        )
    )
    price_quantity: float = _positive(
        "How much the price buys, in price_unit. 1 when the page gives none."
    )
    price_unit: str = _optional(
        "The unit: ounce, gram, kilogram, pound, fluid ounce, millilitre, or a "
        "count (capsule, pod, bag…)."
    )
    price_grams: float = _positive("The quantity in grams, for weights only.")
    price_note: str = _optional(
        "Anything else in the price text: brackets, a second price, a packaging word."
    )
    aroma: pl.Float32 = _score("Aroma, 1–10 (decimals in 1997–1999).")
    acidity: pl.Float32 = _score("Acidity, 1–10 (decimals in 1997–1999).")
    body: pl.Float32 = _score("Body, 1–10 (decimals in 1997–1999).")
    flavor: pl.Float32 = _score("Flavor, 1–10 (decimals in 1997–1999).")
    aftertaste: pl.Float32 = _score("Aftertaste, 1–10. Missing on many older reviews.")
    with_milk: pl.Float32 = _score("Espresso only: the score in milk, 1–10.")
    blind_assessment: str = _optional("The reviewers' tasting notes, written blind.")
    notes: str = _optional("Background on the coffee, the farm and the roaster.")
    bottom_line: str = _optional("The summary judgement.")
    extra_count: pl.Int32 = pa.Field(
        description=(
            "How many unrecognised labels the parsed page had (see the parsed "
            "extra_fields table)."
        )
    )

    class Config:
        strict = True  # no other columns
        ordered = True  # in this order

    @pa.check("review_month")
    @classmethod
    def first_of_month(cls, data: PolarsData) -> pl.LazyFrame:
        """Check the column holds months, stored as their 1st day."""
        return first_of_month(data)


class CleanGreenFrame(pa.DataFrameModel):
    """The clean green-coffee table."""

    slug: str = pa.Field(
        description="The review's id: the last part of its URL. Unique.", unique=True
    )
    url: str = pa.Field(description="The review page's address.")
    delisted: bool = pa.Field(
        description="True when the review is no longer in the site's sitemaps."
    )
    title: str = _optional("The coffee's name as reviewed.")
    roaster: str = _optional(
        "The roaster (green reviews: the importer or seller), one spelling per "
        "roaster; the cleaning log has the name as written."
    )
    roaster_url: str = _optional("The link on the roaster's logo.")
    review_month: dt.date = pa.Field(
        description=(
            "The month of the review, always stored as its 1st day (the site gives"
            " only month and year)."
        ),
        nullable=True,
    )
    rating: pl.Int16 = pa.Field(
        description="Overall score, 50–100. Empty when not rated (NR).",
        nullable=True,
        ge=50,
        le=100,
    )
    origin_country: str = _optional(
        "Where the coffee was grown: one country (Hawaiʻi and Puerto Rico count as"
        ' their own), "Multiple" for a blend, empty if not disclosed.'
    )
    origin_region: str = _optional(
        "The growing region(s), comma-separated, without filler words; direction "
        'words kept ("Guji, Oromia", "North Sumatra").'
    )
    price_amount: float = _positive("The price in its own currency (not converted).")
    price_currency: str = pa.Field(
        description="ISO 4217 currency code (USD, TWD, CAD…).",
        nullable=True,
        str_matches=r"^[A-Z]{3}$",
    )
    price_currency_inferred: bool = pa.Field(
        description=(
            "True when the currency was read from the roaster's country ($ outside the"
            " US, ¥, E, pesos), not written plainly."
        )
    )
    price_quantity: float = _positive(
        "How much the price buys, in price_unit. 1 when the page gives none."
    )
    price_unit: str = _optional(
        "The unit: ounce, gram, kilogram, pound, fluid ounce, millilitre, or a "
        "count (capsule, pod, bag…)."
    )
    price_grams: float = _positive("The quantity in grams, for weights only.")
    price_note: str = _optional(
        "Anything else in the price text: brackets, a second price, a packaging word."
    )
    harvest_year: pl.Int16 = _optional("The (last) year of the harvest.")
    harvest_text: str = _optional(
        'The harvest as written ("November 2022 to March 2023").'
    )
    seller: str = _optional("Who sells the green coffee.")
    variety: str = _optional("The coffee variety (cultivar).")
    lot_size_kg: float = _positive(
        "The lot size in kilograms, when it starts with a plain weight."
    )
    lot_size_text: str = _optional("The lot size as written.")
    processing_method: str = _optional("How the coffee fruit was processed.")
    lot_id: str = _optional("The seller's lot number.")
    blind_assessment: str = _optional("The reviewers' tasting notes, written blind.")
    notes: str = _optional("Background on the coffee, the farm and the roaster.")
    bottom_line: str = _optional("The summary judgement.")
    extra_count: pl.Int32 = pa.Field(
        description=(
            "How many unrecognised labels the parsed page had (see the parsed "
            "extra_fields table)."
        )
    )

    class Config:
        strict = True  # no other columns
        ordered = True  # in this order

    @pa.check("review_month")
    @classmethod
    def first_of_month(cls, data: PolarsData) -> pl.LazyFrame:
        """Check the column holds months, stored as their 1st day."""
        return first_of_month(data)


class CleanLogFrame(pa.DataFrameModel):
    """The cleaning log: one row per rule applied to a field of a review."""

    slug: str = pa.Field(description="The review the rule was applied to.")
    dataset: str = pa.Field(description="roasted or green.", isin=["roasted", "green"])
    field: str = pa.Field(
        description="The parsed field the rule was applied to (rating, agtron, price…)."
    )
    rule: str = pa.Field(
        description="The rule: one of the cleaning rules.", isin=list(CLEAN_RULES)
    )
    raw_value: str = _optional("The field's parsed text, before cleaning.")

    class Config:
        strict = True  # no other columns
        ordered = True  # in this order


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

# The roaster review sheet (data/clean/roaster_review.csv): one row per roaster
# pair waiting for a person; every column is text, since people edit it.
REVIEW_SHEET_COLUMNS: tuple[str, ...] = (
    "kind",
    "spelling",
    "reviews",
    "places",
    "first",
    "last",
    "candidate",
    "candidate_reviews",
    "candidate_places",
    "candidate_first",
    "candidate_last",
    "decision",
    "name",
)
REVIEW_SHEET_SCHEMA: dict[str, type[pl.String]] = dict.fromkeys(
    REVIEW_SHEET_COLUMNS, pl.String
)


class CleanContractError(Exception):
    """A clean table broke its contract: a bug in a transform, not bad data."""


def validate_clean(frame: pl.DataFrame, table: CleanTable) -> pl.DataFrame:
    """Check a clean table against its contract.

    Every check runs (pandera's lazy mode), so one error lists every failure.

    Args:
        frame: The table to check.
        table: Which table it is.

    Returns:
        ``frame``, unchanged.

    Raises:
        CleanContractError: If any check fails; the message lists each one.
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
