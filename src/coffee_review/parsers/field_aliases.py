"""Every name a review field has had on coffeereview.com, in one place.

Pages label the same field differently over the years ("Acidity" and
"Acidity/Structure", "Est. Price" and "Estimated Price"), and renamed one
section ("Who Should Drink It" became "Bottom Line" in 2016). This table maps
each name, after normalising, to one canonical field. The first version comes
from the 2026-11-21 survey; new names are added here, each with a test.
"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from coffee_review.models.parsed import Dataset

_ROASTED: frozenset[Dataset] = frozenset({"roasted"})
_GREEN: frozenset[Dataset] = frozenset({"green"})
_BOTH: frozenset[Dataset] = frozenset({"roasted", "green"})


def normalise_label(raw: str) -> str:
    """Comparable form of a label or heading: trimmed, no trailing colon,
    single spaces, lower case. ``" Roast  Level :"`` becomes ``"roast level"``."""
    text = re.sub(r"\s+", " ", raw).strip()
    return text.removesuffix(":").strip().lower()


# Table labels (as normalised) → canonical field.
LABEL_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "review date": "review_date",
        "roaster location": "roaster_location",
        "coffee origin": "coffee_origin",
        "roast level": "roast_level",
        "agtron": "agtron",
        "est. price": "price",
        "estimated price": "price",
        "aroma": "aroma",
        "acidity/structure": "acidity",
        "acidity": "acidity",
        "body": "body",
        "flavor": "flavor",
        "aftertaste": "aftertaste",
        "with milk": "with_milk",
        "harvested": "harvested",
        "seller": "seller",
        "variety": "variety",
        "lot size": "lot_size",
        "processing method": "processing_method",
        "lot # or id": "lot_id",
    }
)

# Section headings (as normalised) → canonical field.
HEADING_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "blind assessment": "blind_assessment",
        "notes": "notes",
        "bottom line": "bottom_line",
        "who should drink it": "bottom_line",  # its name until 2016
    }
)

# Headings that belong to the site around the review, not the review. The
# first one marks the end of the review.
IGNORED_HEADINGS: frozenset[str] = frozenset(
    {"explore similar coffees", "primary sidebar"}
)

# Which datasets each canonical field belongs to. A known label on a page of
# the other kind of review is kept in ``extra`` rather than dropped.
FIELD_DATASETS: Mapping[str, frozenset[Dataset]] = MappingProxyType(
    {
        "review_date": _BOTH,
        "coffee_origin": _BOTH,
        "price": _BOTH,
        "blind_assessment": _BOTH,
        "notes": _BOTH,
        "bottom_line": _BOTH,
        "roaster_location": _ROASTED,
        "roast_level": _ROASTED,
        "agtron": _ROASTED,
        "aroma": _ROASTED,
        "acidity": _ROASTED,
        "body": _ROASTED,
        "flavor": _ROASTED,
        "aftertaste": _ROASTED,
        "with_milk": _ROASTED,
        "harvested": _GREEN,
        "seller": _GREEN,
        "variety": _GREEN,
        "lot_size": _GREEN,
        "processing_method": _GREEN,
        "lot_id": _GREEN,
    }
)


def canonical_label(raw: str) -> str | None:
    """The canonical field for a table label, or None if it's unknown."""
    return LABEL_ALIASES.get(normalise_label(raw))


def canonical_heading(raw: str) -> str | None:
    """The canonical field for a section heading, or None if it's unknown."""
    return HEADING_ALIASES.get(normalise_label(raw))
