"""A review's price, split into amount, currency, quantity and unit.

``NT $700/200 grams`` → 700, TWD, 200, gram (and 200 grams). The parse stage
kept prices as text; converting to US dollars is M5's job, with exchange rates.

- **Currency** comes from the symbol as written (``NT $``, ``CAD $``, ``£``,
  any ISO code). A symbol that means different currencies in different
  countries (``$``, ``¥``) is read using the roaster's country, and flagged
  ``price_currency_inferred`` (Decision 7).
- **Quantity and unit** come from the text after the slash: ``12 ounces``,
  ``12-ounce can``, ``10 capsules``. A missing quantity is 1 (``$8.99/can``).
  Grams are worked out for weights only.
- Text in brackets, after a semicolon, or left over from the unit
  (``12 oz. instant``) is kept as ``price_note``.

Like places, each distinct price is split once in plain Python and joined back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from coffee_review.transforms.lookup_tables import normalise_symbol
from coffee_review.transforms.rules import raw, rule_column
from coffee_review.transforms.units import find_unit, to_grams

if TYPE_CHECKING:
    from collections.abc import Mapping

    from coffee_review.models.clean import CleanRule
    from coffee_review.transforms.lookup_tables import Unit

_NUMBER = r"\d[\d,]*(?:\.\d+)?"
# <currency><amount><currency> / <quantity> <unit> (<note>), in its spellings:
# "$13.50/12 ounces", "NT $700/200 grams", "619 Kč/227 grams", "£6.50 for 250g".
_PRICE = re.compile(
    rf"""^(?:price:\s*)?
    (?P<pre>[^\d]*?)\s*(?P<amount>{_NUMBER})\s*(?P<post>[^\d\s/()]*)
    \s*(?:/+|\s+for\s+|\s+per\s+)\s*
    (?P<quantity>{_NUMBER})?\s*-?\s*
    (?P<unit>[^()]*?)
    \s*(?:\((?P<note>[^)]*)\)?)?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
# An amount with nothing after it: "$15.00".
_BARE = re.compile(
    rf"""^(?:price:\s*)?
    (?P<pre>[^\d]*?)\s*(?P<amount>{_NUMBER})\s*(?P<post>[^\d\s/()]*)$""",
    re.IGNORECASE | re.VERBOSE,
)
_ANY_DIGIT = re.compile(r"\d")
ANYWHERE = "*"  # roaster_currencies.csv: the symbol's meaning in any other country


@dataclass(frozen=True)
class PriceParts:
    """A price split into its written parts, before anything is looked up."""

    amount: float
    symbol: str | None  # the currency as written: "$", "NT $", "Kč"
    quantity: float | None
    unit_text: str | None  # the unit as written: "ounces", "ounce can"
    note: str | None


def split_price(text: str) -> PriceParts | None:
    """The parts of a price, or None when it isn't in a known shape."""
    text = text.strip()
    if match := _PRICE.match(text):
        unit, _, extra = match["unit"].partition(";")
        notes = [n.strip() for n in (match["note"], extra) if n and n.strip()]
        return PriceParts(
            amount=_number(match["amount"]),
            symbol=_symbol(match["pre"], match["post"]),
            quantity=_number(match["quantity"]) if match["quantity"] else None,
            unit_text=unit.strip() or None,
            note="; ".join(notes) or None,
        )
    if match := _BARE.match(text):
        return PriceParts(
            _number(match["amount"]),
            _symbol(match["pre"], match["post"]),
            None,
            None,
            None,
        )
    return None


def resolve_currency(
    symbol: str | None,
    roaster_country: str | None,
    currencies: Mapping[str, str],
    by_roaster: Mapping[str, Mapping[str, str]],
) -> tuple[str | None, bool, tuple[CleanRule, ...]]:
    """(ISO code, inferred from the roaster?, rules) for a currency as written."""
    if symbol is None:
        return None, False, ("unknown_currency",)
    key = normalise_symbol(symbol)
    if key in by_roaster:
        countries = by_roaster[key]
        if roaster_country is not None and roaster_country in countries:
            return countries[roaster_country], True, ("currency_from_roaster",)
        if ANYWHERE in countries:
            return countries[ANYWHERE], False, ()
        return None, False, ("ambiguous_currency",)
    for candidate in (key, key.replace("$", "")):  # "NT$" or "NT", "CAD$" or "CAD"
        if candidate in currencies:
            return currencies[candidate], False, ()
    return None, False, ("unknown_currency",)


def clean_price(
    frame: pl.LazyFrame,
    currencies: Mapping[str, str],
    by_roaster: Mapping[str, Mapping[str, str]],
    units: Mapping[str, Unit],
) -> pl.LazyFrame:
    """``raw_price`` → the seven ``price_*`` columns, using ``roaster_country``.

    Needs ``roaster_country`` (from ``clean_roaster_location``; the pipeline
    gives green reviews a null one).
    """
    source, rules_column = raw("price"), rule_column("price")
    pairs = (
        frame.select(source, "roaster_country")
        .drop_nulls(source)
        .unique()
        .collect()
        .iter_rows()
    )
    rows = [
        {
            source: text,
            "roaster_country": country,
            **_price_row(text, country, currencies, by_roaster, units),
        }
        for text, country in pairs
    ]
    results = pl.DataFrame(rows, schema=_RESULT_SCHEMA | {source: pl.String()}).lazy()
    joined = frame.join(
        results,
        on=[source, "roaster_country"],
        how="left",
        nulls_equal=True,
        maintain_order="left",
    )
    return joined.with_columns(
        pl.col("price_currency_inferred").fill_null(False),
        pl.col(rules_column).fill_null([]),
    )


_RESULT_SCHEMA = pl.Schema(
    {
        "roaster_country": pl.String(),
        "price_amount": pl.Float64(),
        "price_currency": pl.String(),
        "price_currency_inferred": pl.Boolean(),
        "price_quantity": pl.Float64(),
        "price_unit": pl.String(),
        "price_grams": pl.Float64(),
        "price_note": pl.String(),
        rule_column("price"): pl.List(pl.String()),
    }
)


def _price_row(
    text: str,
    roaster_country: str | None,
    currencies: Mapping[str, str],
    by_roaster: Mapping[str, Mapping[str, str]],
    units: Mapping[str, Unit],
) -> dict[str, object]:
    """One price's clean columns and rules."""
    empty: dict[str, object] = dict.fromkeys(_RESULT_SCHEMA.names()[1:-1])
    empty["price_currency_inferred"] = False
    if not _ANY_DIGIT.search(text):  # "NA", "Not Available", "See website…"
        return empty | {rule_column("price"): ["not_a_price"]}
    parts = split_price(text)
    if parts is None:
        return empty | {rule_column("price"): ["unparsed"]}
    currency, inferred, rules = resolve_currency(
        parts.symbol, roaster_country, currencies, by_roaster
    )
    row = empty | {
        "price_amount": parts.amount,
        "price_currency": currency,
        "price_currency_inferred": inferred,
        "price_note": parts.note,
    }
    rules_list: list[CleanRule] = list(rules)
    if parts.unit_text is None:
        rules_list.append("no_quantity")
    elif found := find_unit(parts.unit_text, units):
        quantity = parts.quantity if parts.quantity is not None else 1.0
        notes = [n for n in (found.leftover, parts.note) if n]
        row |= {
            "price_quantity": quantity,
            "price_unit": found.unit.name,
            "price_grams": to_grams(quantity, found.unit),
            "price_note": "; ".join(notes) or None,
        }
        if found.close:
            rules_list.append("close_spelling")
    else:
        rules_list.append("unknown_unit")
    return row | {rule_column("price"): rules_list}


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _symbol(before: str, after: str) -> str | None:
    """The currency as written, from either side of the amount: "$ NTD"."""
    return " ".join(p.strip() for p in (before, after) if p.strip()) or None
