"""An origin's growing region: one standard name below the country.

``origin_region`` keeps the page's own text (``Gedeb, Gedeo``,
``Sul de Minas, southern Minas Gerais``). This names the growing region it
means, from the hand-kept ``origin_regions.csv``, so reviews can be grouped by
region: ``Yirgacheffe``, ``Minas Gerais``.

- The table covers the 15 countries with the most reviews, one level per
  country: Ethiopia's trade regions, Colombia's departments, Kenya's counties,
  Guatemala's and Costa Rica's official coffee regions, Brazil's states,
  Indonesia's islands, and so on. Other countries get no growing region.
- Each row is a spelling (``alias``) and the region it means. A row with no
  region marks a name broader than a region (``Oromia``): on its own it names
  nothing.
- Aliases match as whole words, ignoring case, accents and apostrophes, longest
  first: ``Valle del Cauca`` is one region, not also Cauca.
- Text naming two regions is ``"Multiple"``. Text naming nothing known, in a
  covered country, is logged ``unknown_region`` and listed in the report, so a
  new spelling can be added to the table.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from coffee_review.transforms.lookup_tables import MULTIPLE

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from coffee_review.models.clean import CleanRule

# Country → (folded alias, region, or None for a broader name), longest first.
type GrowingRegions = Mapping[str, tuple[tuple[str, str | None], ...]]

_APOSTROPHES = str.maketrans(dict.fromkeys("'’‘ʻ`´", ""))
_NOT_WORD = re.compile(r"[^a-z0-9]+")
_MATCHED = " | "  # replaces a match, so a shorter alias can't match inside it


@dataclass(frozen=True)
class RegionResult:
    """The growing region named by an origin's region text.

    Attributes:
        region: The standard region, ``"Multiple"``, or None.
        rules: The cleaning rules applied on the way.
    """

    region: str | None
    rules: tuple[CleanRule, ...]


def fold_region(text: str) -> str:
    """A region spelling without case, accents or punctuation: ``Kaʻū`` → ``kau``."""
    ascii_text = (
        unicodedata.normalize("NFKD", text.translate(_APOSTROPHES))
        .encode("ascii", "ignore")
        .decode()
        .lower()
    )
    return " ".join(_NOT_WORD.sub(" ", ascii_text).split())


def build_growing_regions(
    rows: Iterable[tuple[str, str, str | None]],
) -> GrowingRegions:
    """The lookup ``name_growing_region`` reads, from (country, alias, region) rows.

    Raises:
        ValueError: If an alias is empty, or one alias, folded, means two
            different things in one country.
    """
    tables: dict[str, dict[str, str | None]] = {}
    for country, alias, region in rows:
        table = tables.setdefault(country, {})
        key = fold_region(alias)
        if not key:
            msg = f"origin_regions.csv: {country} has an empty alias"
            raise ValueError(msg)
        if table.setdefault(key, region) != region:
            msg = (
                f"origin_regions.csv: {country} alias {key!r} means both"
                f" {table[key]!r} and {region!r}"
            )
            raise ValueError(msg)
    return MappingProxyType(
        {
            country: tuple(sorted(table.items(), key=lambda row: -len(row[0])))
            for country, table in tables.items()
        }
    )


def name_growing_region(
    country: str | None, region: str | None, regions: GrowingRegions
) -> RegionResult:
    """The growing region an origin's region text names, with the rule logged.

    Args:
        country: The origin country, as ``place_origin`` gave it.
        region: The origin's region text, as ``place_origin`` gave it.
        regions: The region lookup (``load_growing_regions``).

    Returns:
        No region and no rule for a country the table doesn't cover or no
        region text; otherwise the region, ``"Multiple"``, or nothing, with
        the rule saying which.
    """
    aliases = regions.get(country or "")
    if aliases is None or region is None:
        return RegionResult(None, ())
    text = f" {fold_region(region)} "
    found: set[str | None] = set()
    for alias, name in aliases:
        target = f" {alias} "
        while target in text:
            text = text.replace(target, _MATCHED, 1)
            found.add(name)
    named = found - {None}
    if len(named) > 1:
        return RegionResult(MULTIPLE, ("multiple_regions",))
    if named:
        (name,) = named
        return RegionResult(name, () if name == region else ("growing_region",))
    return RegionResult(None, ("broad_region",) if found else ("unknown_region",))
