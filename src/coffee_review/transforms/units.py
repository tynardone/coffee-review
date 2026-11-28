"""Units of quantity, shared by prices and green lot sizes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import get_close_matches
from typing import TYPE_CHECKING

from coffee_review.transforms.lookup_tables import normalise_unit

if TYPE_CHECKING:
    from collections.abc import Mapping

    from coffee_review.transforms.lookup_tables import Unit

# How alike a misspelt unit must be to a known one ("onces" → ounces), and the
# shortest spelling worth trying (so "oz" never guesses).
CLOSE_CUTOFF = 0.85
CLOSE_MIN_LENGTH = 5
# Words that describe the packaging, not the unit: "single-serve capsules".
_FILLER = frozenset({"single-serve"})
# A unit text holding its own count ("six 8-ounce cans") isn't one unit.
_COUNTED = re.compile(
    r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
    r"|twenty|thirty|case|pack)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FoundUnit:
    """The unit a text names, any words left over, and whether it was spelt close."""

    unit: Unit
    leftover: str | None
    close: bool = False


def find_unit(text: str, units: Mapping[str, Unit]) -> FoundUnit | None:
    """Find the unit a price's unit text names, if any.

    Tried in turn: the whole text, its first word ("ounce can"), its last word
    ("realcup capsules"), then a close spelling of the whole text ("onces").

    Args:
        text: The text after the quantity.
        units: The unit lookup table.

    Returns:
        The unit, how it was found, and the other words as ``leftover`` (they
        become the price note); None if no unit is named.
    """
    if _COUNTED.search(text):
        return None
    words = [w for w in text.split() if w.lower() not in _FILLER]
    if not words:
        return None
    key = normalise_unit(" ".join(words))
    if key in units:
        return FoundUnit(units[key], None)
    first, last = normalise_unit(words[0]), normalise_unit(words[-1])
    if len(words) > 1 and first in units:
        return FoundUnit(units[first], " ".join(words[1:]))
    if len(words) > 1 and last in units:
        return FoundUnit(units[last], " ".join(words[:-1]))
    if len(key) >= CLOSE_MIN_LENGTH:
        match = get_close_matches(key, list(units), n=1, cutoff=CLOSE_CUTOFF)
        if match:
            return FoundUnit(units[match[0]], None, close=True)
    return None


def to_grams(quantity: float, unit: Unit) -> float | None:
    """Convert a quantity to grams.

    Args:
        quantity: How many of the unit.
        unit: The unit.

    Returns:
        The weight in grams, or None for counts and volumes.
    """
    return None if unit.grams is None else round(quantity * unit.grams, 2)
