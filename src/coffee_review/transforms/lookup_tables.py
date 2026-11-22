"""The hand-kept lookup tables that ship with the code.

They're small CSVs in ``transforms/lookups/``, versioned with the code and
grown from the clean report's "to review" list, the way M2's alias table was.
They're code, not pipeline data, so reading them here doesn't break the rule
that only ``storage/`` knows data formats.

- ``countries.csv``: every country and its spellings, generated from ISO 3166
  by ``scripts/generate_countries.py``, so a country no review has mentioned
  yet is still known;
- ``places.csv``: hand-kept: other spellings, Hawaiʻi, islands, broad areas,
  US states and Canadian provinces, with each place's standard name, the
  country it's in, and what kind of place it is;
- ``currency_codes.csv``: every ISO 4217 code, generated the same way;
- ``currencies.csv``: hand-kept: other ways prices write a currency (``NT``,
  ``£``, ``Kč``), and the code each means;
- ``roaster_currencies.csv``: symbols that mean different currencies in
  different countries (``$``, ``¥``), read using the roaster's country
  (Decision 7); a ``*`` row is the symbol's meaning everywhere else;
- ``units.csv``: every way prices write a unit, its standard name, its kind
  (weight, volume or count), and grams per unit for weights.

``scripts/generate_lookup_tables.py`` writes the two generated tables.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

type PlaceKind = Literal["country", "state", "province", "island", "area"]
type UnitKind = Literal["weight", "volume", "count"]
MULTIPLE = "Multiple"  # the "country" of a broad area or blend

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʻ": "'", "`": "'", "´": "'"})
_QUOTES = re.compile(r"[\"“”]")


@dataclass(frozen=True)
class Place:
    """What one spelling of a place stands for.

    ``country`` is the place's coffee-origin country (Hawaiʻi is its own,
    Decision 6); ``roaster_country`` is the country for a roaster's address
    (Hawaiʻi is in the United States). They differ only for Hawaiʻi.
    """

    name: str
    country: str
    kind: PlaceKind
    roaster_country: str


def normalise_place(text: str) -> str:
    """A place spelling in a form that ignores case, quotes and punctuation.

    ``“Big Island” of Hawai’i.`` and ``"Big Island" of Hawai'i`` both become
    ``big island of hawai'i``.
    """
    text = _QUOTES.sub("", text.translate(_APOSTROPHES))
    return " ".join(text.split()).strip(" .,;:").lower()


@dataclass(frozen=True)
class Unit:
    """A unit a price can be quoted in."""

    name: str  # the standard name: "ounce", "fluid ounce", "capsule"
    kind: UnitKind
    grams: float | None  # grams per unit, for weights only


def normalise_symbol(text: str) -> str:
    """A currency as written, without case, spaces or full stops: ``NT $`` → ``NT$``."""
    return "".join(text.split()).replace(".", "").upper()


def normalise_unit(text: str) -> str:
    """A unit as written, ignoring case and full stops: ``oz.`` → ``oz``."""
    return " ".join(text.replace(".", " ").split()).lower()


@cache
def load_currencies() -> Mapping[str, str]:
    """Every currency spelling → its ISO 4217 code, keyed by ``normalise_symbol``.

    Every ISO code means itself; ``currencies.csv`` adds other spellings.
    Symbols whose meaning depends on the country (``$``, ``¥``) aren't here: see
    ``load_roaster_currencies``.
    """
    currencies = {row["code"]: row["code"] for row in _read_csv("currency_codes.csv")}
    for row in _read_csv("currencies.csv"):
        currencies[normalise_symbol(row["alias"])] = row["currency"]
    return MappingProxyType(currencies)


@cache
def load_roaster_currencies() -> Mapping[str, Mapping[str, str]]:
    """Symbol → {roaster country, or ``*`` for anywhere else → ISO code}."""
    by_symbol: dict[str, dict[str, str]] = {}
    for row in _read_csv("roaster_currencies.csv"):
        symbol = normalise_symbol(row["symbol"])
        by_symbol.setdefault(symbol, {})[row["roaster_country"]] = row["currency"]
    return MappingProxyType(
        {symbol: MappingProxyType(rows) for symbol, rows in by_symbol.items()}
    )


@cache
def load_units() -> Mapping[str, Unit]:
    """Every unit spelling, keyed by ``normalise_unit``."""
    return MappingProxyType(
        {
            normalise_unit(row["alias"]): Unit(
                name=row["name"],
                kind=cast("UnitKind", row["kind"]),
                grams=float(row["grams"]) if row["grams"] else None,
            )
            for row in _read_csv("units.csv")
        }
    )


@cache
def load_places() -> Mapping[str, Place]:
    """Every place spelling in both tables, keyed by ``normalise_place``."""
    places: dict[str, Place] = {}
    for row in _read_csv("countries.csv"):
        name = row["name"]
        places[normalise_place(row["alias"])] = Place(name, name, "country", name)
    for row in _read_csv("places.csv"):
        places[normalise_place(row["alias"])] = Place(
            name=row["name"],
            country=row["country"],
            kind=cast("PlaceKind", row["kind"]),
            roaster_country=row["roaster_country"] or row["country"],
        )
    return MappingProxyType(places)


def _read_csv(name: str) -> list[dict[str, str]]:
    text = (
        files("coffee_review.transforms.lookups")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )
    return list(csv.DictReader(text.splitlines()))
