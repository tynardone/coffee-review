"""Where a coffee was grown, and where its roaster is.

**Origin** becomes ``origin_country`` and ``origin_region``, so origins can be
compared by country and growing region:

- ``Guji Zone, Oromia Region, southern Ethiopia`` → Ethiopia, ``Guji, Oromia``;
- an island names its country and stays as detail: ``Sumatra`` → Indonesia,
  region ``Sumatra``; Hawaiʻi and Puerto Rico are their own countries;
- more than one country, or a broad area or blend → ``"Multiple"``;
- ``Not disclosed`` and ``NA`` → nothing, logged (for roasters too).

New spellings in future scrapes are handled three ways: the country list is
complete (``countries.csv``); a country written as the last word of a part is
found ("Huila Colombia"); and a misspelling of a known place is matched by
close spelling, logged as ``close_spelling`` so it shows in the report.

**Roaster location** becomes city, region (state or province) and country.
For roasters, Hawaiʻi is a US state.

Both work on one text at a time in plain Python (the steps are procedural),
applied once to each *distinct* value and joined back onto the frame.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import get_close_matches
from typing import TYPE_CHECKING

import polars as pl

from coffee_review.transforms.lookup_tables import (
    MULTIPLE,
    load_area_countries,
    normalise_place,
)
from coffee_review.transforms.rules import raw, rule_column

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from coffee_review.models.clean import CleanRule
    from coffee_review.transforms.lookup_tables import Place

_SPLIT_ORIGIN = re.compile(r"\s*[,;&/]\s*(?:and\s+)?|\s+and\s+", re.IGNORECASE)
_DIRECTIONS = re.compile(
    r"^(?:(?:far|extreme|upper|lower)\s+)?"
    r"(?:north|south|east|west|central|northern|southern|eastern|western"
    r"|north-?east(?:ern)?|north-?west(?:ern)?|south-?east(?:ern)?|south-?west(?:ern)?"
    r"|(?:north|south|east|west)-central)\s+",
    re.IGNORECASE,
)
_FILLER = re.compile(
    r"\s+(?:growing region|growing area|growing district|growing zone|growing"
    r"|coffee region|region|zone|department|district|county|state|province)$",
    re.IGNORECASE,
)
_ASIDE = re.compile(r"\s*\([^)]*\)")  # "Sidamo (also Sidama)": the aside is dropped
_THE = re.compile(r"^the\s+", re.IGNORECASE)
# Words that say a coffee is a blend, whatever else the origin names.
_BLEND_WORDS = re.compile(r"\b(?:other|various|blend|several)\b", re.IGNORECASE)
_QUOTES = re.compile(r"[\"“”]")
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s*%")  # blend shares: "Kenya 40%"

# Close spellings: how alike two spellings must be (difflib's ratio, 0–1), and
# the shortest spelling worth matching ("Pery" could be too many places).
CLOSE_CUTOFF = 0.8  # the last resort, when nothing else matched
VERY_CLOSE_CUTOFF = 0.9  # tried before splitting a part into words
CLOSE_MIN_LENGTH = 5
# Two-letter codes (IL, CA, "in"…) only match a whole part, never a word inside
# one: "Grown in" isn't Indiana.
SHORTEST_INNER = 2
# Inside a longer part, only these kinds may match a first or last word: a
# country or state names itself, but "Las Flores" isn't the island of Flores
# and "Mount Kenya Blend" isn't a blend.
INNER_KINDS = frozenset({"country", "state", "province"})
# An island or region may match inside a part only by a name of two or more
# words ("Apaneca Ilamatepec mountain range"); one word ("Flores") is too easily
# a town. When nothing else matched, any kind may (``any_kind``): "Espresso
# Blend" and "Various Latin American origins" are blends.
NAMED_KINDS = frozenset({"island", "region"})
# Territories a roaster address puts inside a country: "Kowloon, Hong Kong,
# China" is in Hong Kong. Anything else before the country is a city or region
# ("Armenia, Colombia" is a city).
TERRITORIES: Mapping[str, str] = {"Hong Kong": "China", "Macao": "China"}
UNKNOWN_MARKERS = ("na", "n/a")


@dataclass(frozen=True)
class OriginResult:
    """Where a coffee was grown, as placed from its origin text.

    Attributes:
        country: The origin country, ``"Multiple"`` for a blend, or None.
        region: The growing region, or None.
        rules: The cleaning rules applied on the way.
    """

    country: str | None
    region: str | None
    rules: tuple[CleanRule, ...]


@dataclass(frozen=True)
class RoasterResult:
    """Where a roaster is, as placed from its location text.

    Attributes:
        city: The city, or None.
        region: The state or province, or None.
        country: The country, or None.
        rules: The cleaning rules applied on the way.
    """

    city: str | None
    region: str | None
    country: str | None
    rules: tuple[CleanRule, ...]


def place_origin(
    text: str,
    places: Mapping[str, Place],
    areas: Mapping[str, frozenset[str]] | None = None,
) -> OriginResult:
    """Place a coffee's origin: its country and growing region.

    Args:
        text: The origin as written.
        places: The place lookup table.
        areas: The countries each broad area holds (default: ``areas.csv``), so
            "Guatemala, Central America" is Guatemala but "Indonesia; Central and
            South America" is a blend.

    Returns:
        The country, region and the rules applied.
    """
    areas = load_area_countries() if areas is None else areas
    if marker := _marker(text):
        return OriginResult(None, None, (marker,))
    whole = _exact(text, places) or _exact(text := _ASIDE.sub("", text), places)
    if whole is not None and whole.kind != "area":
        # The whole text is one place: "São Tomé and Príncipe" isn't two.
        region = None if whole.kind == "country" else _with_directions(text, whole.name)
        return OriginResult(whole.country, region, ())
    parts = [_PERCENT.sub("", p).strip() for p in _SPLIT_ORIGIN.split(text)]
    parts = [p for p in parts if p]
    # Close spellings only when nothing matched exactly: "Chinas, Huila,
    # Colombia" is a town in Colombia, not a blend with China.
    matches = [_find(part, places, very_close=False) for part in parts]
    if not any(matches):
        matches = [_find(part, places, very_close=True) for part in parts]
    if not any(matches):
        matches = [
            _find(part, places, very_close=True, any_kind=True) for part in parts
        ]
    rules: tuple[CleanRule, ...] = ()
    if any(m is not None and m.close for m in matches):
        rules = ("close_spelling",)
    if not any(matches):
        # Still nothing: the last resort, a less close spelling, last part first.
        for i in reversed(range(len(parts))):
            if place := _close(parts[i], places):
                matches[i], rules = _Match(place, None), ("close_spelling",)
                break
    found = {
        m.place.country for m in matches if m is not None and m.place.kind != "area"
    }
    countries: set[str] = set()
    region_parts: list[str] = []
    for part, match in zip(parts, matches, strict=True):
        if match is None:
            if len(found) == 1 and _garbled_copy(part, next(iter(found)), places):
                rules = ("close_spelling",)  # "Guatemala.e": the country, garbled
            else:
                region_parts.append(_tidy_region(part))
            continue
        if match.place.kind == "area" and _is_context(match.place, found, text, areas):
            continue  # "Guatemala, Central America": the area is only context
        countries.add(match.place.country)
        if match.leftover:
            region_parts.append(match.leftover)
        if match.place.kind != "country":  # an island, region or state is detail
            # Its standard name, keeping any direction words as written:
            # "North Sumatra" is a province and "West Guji" a zone, so the
            # direction is part of the name.
            region_parts.append(_with_directions(match.text or part, match.place.name))
    if not countries:
        return OriginResult(None, None, ("unplaced",))
    if MULTIPLE in countries or len(countries) > 1:
        return OriginResult(MULTIPLE, None, (*rules, "multiple_origins"))
    region = ", ".join(dict.fromkeys(p for p in region_parts if p)) or None
    return OriginResult(countries.pop(), region, rules)


def place_roaster(text: str, places: Mapping[str, Place]) -> RoasterResult:
    """Place a roaster: its city, state or province, and country.

    Args:
        text: The roaster location as written.
        places: The place lookup table.

    Returns:
        The city, region, country and the rules applied.
    """
    if marker := _marker(text):
        return RoasterResult(None, None, None, (marker,))
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return RoasterResult(None, None, None, ("unplaced",))
    city = _tidy(parts[0]) if len(parts) > 1 else None
    match = _find(parts[-1], places, very_close=True)
    rules: tuple[CleanRule, ...] = ("close_spelling",) if match and match.close else ()
    if match is None and (close := _close(parts[-1], places)):
        match, rules = _Match(close, None), ("close_spelling",)
    if match is None or match.place.kind == "area":
        return RoasterResult(_tidy(parts[0]), None, None, ("unplaced",))
    if city is None and match.leftover and match.place.kind in ("state", "province"):
        city = match.leftover  # "Scottsdale Arizona", written without a comma
    last = match.place
    if last.kind == "country" and last.roaster_country == last.country:
        # A country before the last part is the real one: "Kowloon, Hong Kong,
        # China" is in Hong Kong, and Hong Kong has its own dollar.
        for i in reversed(range(len(parts) - 1)):
            inner = places.get(normalise_place(parts[i]))
            if inner and TERRITORIES.get(inner.name) == last.country:
                city = _tidy(parts[0]) if i > 0 else None
                return RoasterResult(city, None, inner.roaster_country, rules)
        middle = ", ".join(_region_name(p, places) for p in parts[1:-1]) or None
        return RoasterResult(city, middle, last.country, rules)
    # A state or province, or Hawaiʻi: the place is the region.
    region = last.country if last.roaster_country != last.country else last.name
    return RoasterResult(city, region, last.roaster_country, rules)


def clean_origin(frame: pl.LazyFrame, places: Mapping[str, Place]) -> pl.LazyFrame:
    """Turn ``raw_coffee_origin`` into ``origin_country`` and ``origin_region``.

    Args:
        frame: A table with ``raw_coffee_origin``.
        places: The place lookup table.

    Returns:
        The frame with the origin columns and their rule column added.
    """
    return _join_results(
        frame,
        "coffee_origin",
        lambda text: _origin_row(place_origin(text, places)),
        {"origin_country": pl.String(), "origin_region": pl.String()},
    )


def clean_roaster_location(
    frame: pl.LazyFrame, places: Mapping[str, Place]
) -> pl.LazyFrame:
    """Turn ``raw_roaster_location`` into city, region and country columns.

    Args:
        frame: A table with ``raw_roaster_location``.
        places: The place lookup table.

    Returns:
        The frame with ``roaster_city``, ``roaster_region``, ``roaster_country``
        and their rule column added.
    """
    return _join_results(
        frame,
        "roaster_location",
        lambda text: _roaster_row(place_roaster(text, places)),
        {
            "roaster_city": pl.String(),
            "roaster_region": pl.String(),
            "roaster_country": pl.String(),
        },
    )


@dataclass(frozen=True)
class _Match:
    """The place a part names, and any words before it that are region detail."""

    place: Place
    leftover: str | None
    close: bool = False  # matched by close spelling, not exactly
    text: str | None = None  # the words that matched, if not the whole part


def _find(
    part: str,
    places: Mapping[str, Place],
    *,
    very_close: bool,
    any_kind: bool = False,
) -> _Match | None:
    """The place one part of a location names, if any.

    Tried in turn:

    - the whole part (``_exact``: as written, then shedding direction words one
      at a time, so "South Sudan" is found before "Sudan"; with or without a
      filler word like "Zone");
    - if ``very_close``, a very close spelling of the whole part ("British
      Colombia" is British Columbia, not Colombia), before the rules below
      split it up;
    - what follows an "of", leftmost first ("Kivu region of the Democratic
      Republic of the Congo"), keeping what's before as detail ("Kivu");
    - its last one to three words ("Huila Colombia"), keeping the words before
      them as detail ("Huila");
    - its first one to three words ("Kenya Gatugi AB"), keeping the rest.

    The last two accept a country, state or province, or a named island or
    region (``INNER_KINDS``, ``NAMED_KINDS``); with ``any_kind``, anything.
    """
    if (place := _exact(part, places)) is not None:
        return _Match(place, None)
    if very_close and (close := _close(part, places, cutoff=VERY_CLOSE_CUTOFF)):
        return _Match(close, None, close=True)
    pieces = re.split(r"\s+of\s+", part, flags=re.IGNORECASE)
    for i in range(1, len(pieces)):
        after = " of ".join(pieces[i:])
        if (place := _exact(after, places)) is not None:
            before = " of ".join(pieces[:i])
            return _Match(place, _tidy_region(before) or None, text=after)
    words = _tidy(part).split()
    for n in (3, 2, 1):
        tail = " ".join(words[-n:])
        if len(words) > n and (place := _inner(tail, places, any_kind=any_kind)):
            before = _tidy_region(" ".join(words[:-n]))
            return _Match(place, _strip_directions(before) or None, text=tail)
    for n in (3, 2, 1):
        head = " ".join(words[:n])
        if len(words) > n and (place := _inner(head, places, any_kind=any_kind)):
            rest = _tidy_region(" ".join(words[n:])) or None
            return _Match(place, rest, text=head)
    return None


def _exact(text: str, places: Mapping[str, Place]) -> Place | None:
    """The place this whole text names, ignoring direction and filler words.

    Direction words are shed one at a time and each step is tried, so a name
    that starts with one ("South Sudan", "West Virginia", "Northern Ireland")
    is found before the shorter name inside it.
    """
    key = _THE.sub("", normalise_place(text))
    while True:
        for candidate in (key, _FILLER.sub("", key)):
            if candidate in places:
                return places[candidate]
        stripped = _DIRECTIONS.sub("", key)
        if stripped == key:
            return None
        key = stripped


def _inner(
    words: str, places: Mapping[str, Place], *, any_kind: bool = False
) -> Place | None:
    """A place named by words inside a longer part, if that kind may be."""
    place = _exact(words, places)
    if place is None or len(normalise_place(words)) <= SHORTEST_INNER:
        return None
    name_words = _FILLER.sub("", _strip_directions(normalise_place(words))).split()
    named = place.kind in NAMED_KINDS and len(name_words) > 1
    return place if any_kind or named or place.kind in INNER_KINDS else None


def _region_name(part: str, places: Mapping[str, Place]) -> str:
    """A roaster's middle part: a state or province by its standard name."""
    place = places.get(normalise_place(part))
    if place is not None and place.kind in ("state", "province"):
        return place.name
    return _tidy(part)


def _close(
    part: str, places: Mapping[str, Place], cutoff: float = CLOSE_CUTOFF
) -> Place | None:
    """A known place spelt very nearly like this part, if there is one.

    Used only when nothing matched exactly, and logged as ``close_spelling`` so
    every such match shows in the report ("Calfornia" → California).
    """
    key = _strip_directions(normalise_place(part))
    if len(key) < CLOSE_MIN_LENGTH:
        return None
    matches = get_close_matches(key, list(places), n=1, cutoff=cutoff)
    return places[matches[0]] if matches else None


def _garbled_copy(part: str, country: str, places: Mapping[str, Place]) -> bool:
    """Whether a leftover part is a garbled copy of the country already found.

    "Guatemala.e", "southwestern ColombiaNar", "KenyaMur": a close spelling of
    the country, or its name with junk stuck on the end. It can only confirm
    that country, never bring in another ("Chinas" next to Colombia is a town).
    """
    key = _strip_directions(normalise_place(part))
    if key.startswith(normalise_place(country)):
        return True
    close = _close(part, places)
    # Close to the country's own name, not to one of its regions: "Carmo de
    # Minas" is near the region "Sul de Minas", but it's a region, not "Brazil".
    return close is not None and close.kind == "country" and close.name == country


def _is_context(
    area: Place, found: set[str], text: str, areas: Mapping[str, frozenset[str]]
) -> bool:
    """Whether a broad area only places the one country found.

    "Guatemala, Central America" is Guatemala; "Indonesia; South America" is a
    blend.
    """
    if len(found) != 1 or _BLEND_WORDS.search(text):
        return False
    return next(iter(found)) in areas.get(area.name, frozenset())


def _marker(text: str) -> CleanRule | None:
    """The site's own way of saying "unknown", if this text is one."""
    key = normalise_place(text)
    if key.startswith("not disclosed") or get_close_matches(
        key, ["not disclosed"], cutoff=CLOSE_CUTOFF
    ):
        return "not_disclosed"
    if key in UNKNOWN_MARKERS:
        return "unknown_marker"
    return None


def _with_directions(part: str, name: str) -> str:
    """``name`` after the direction words ``part`` starts with, as written."""
    words = _tidy(part)
    directions = words[: len(words) - len(_strip_directions(words))].strip()
    return f"{directions} {name}" if directions else name


def _strip_directions(text: str) -> str:
    """Without leading direction words: "far southern central Kenya" → "Kenya"."""
    while (stripped := _DIRECTIONS.sub("", text)) != text:
        text = stripped
    return text


def _tidy(text: str) -> str:
    return " ".join(_QUOTES.sub("", text).split()).strip(" .")


def _tidy_region(part: str) -> str:
    """A region as written, without trailing filler ("Guji Zone" → "Guji")."""
    return _FILLER.sub("", _tidy(part))


def _origin_row(result: OriginResult) -> dict[str, object]:
    return {
        "origin_country": result.country,
        "origin_region": result.region,
        "rules": list(result.rules),
    }


def _roaster_row(result: RoasterResult) -> dict[str, object]:
    return {
        "roaster_city": result.city,
        "roaster_region": result.region,
        "roaster_country": result.country,
        "rules": list(result.rules),
    }


def _join_results(
    frame: pl.LazyFrame,
    field: str,
    place: Callable[[str], dict[str, object]],
    columns: dict[str, pl.DataType],
) -> pl.LazyFrame:
    """Place each distinct raw value once, then join the results back."""
    source = raw(field)
    texts = frame.select(pl.col(source).drop_nulls().unique()).collect()[source]
    schema = pl.Schema(
        {source: pl.String(), **columns, rule_column(field): pl.List(pl.String())}
    )
    rows = [{source: text, **place(text)} for text in texts]
    for row in rows:
        row[rule_column(field)] = row.pop("rules")
    results = pl.DataFrame(rows, schema=schema).lazy()
    return frame.join(
        results, on=source, how="left", maintain_order="left"
    ).with_columns(pl.col(rule_column(field)).fill_null([]))
