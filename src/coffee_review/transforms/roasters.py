"""One name per roaster: spellings of the same name become one.

The same roaster is often written several ways (``JBC Coffee Roasters`` and
``JBC``, ``El Gran Cafe`` and ``El Gran Café``). Two layers merge them:

- **The matching key** ignores what never tells roasters apart: case, accents,
  punctuation, ``&``, and filler words like *Coffee Roasters*. Spellings that
  share a key merge (rule ``spelling_variant``), unless their roasters are in
  different countries: then they're listed for review.
- **Close spellings** between keys (``La Mill`` and ``Lamill``) are scored with
  ``difflib``. Very close ones merge (``close_spelling``) unless the roasters'
  locations conflict; doubtful ones are listed for a person (``possible_match``).

A person's decisions are kept in two lookups: ``roasters.csv`` (this spelling
is that roaster, ``listed_name``) and ``distinct_roasters.csv`` (these two are
different roasters). The kept spelling of a group is the listed name, else the
one with the most reviews.
"""

from __future__ import annotations

import re
import unicodedata

FILLER_WORDS = frozenset(
    {
        "coffee",
        "coffees",
        "roaster",
        "roasters",
        "roasting",
        "roastery",
        "co",
        "company",
        "inc",
        "llc",
        "ltd",
        "the",
        "cafe",
        "caffe",
        "espresso",
        "and",
    }
)

_NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def _words(text: str) -> list[str]:
    """Lower-case ASCII words: accents dropped, ``&`` as ``and``, ``'`` removed."""
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    lower = ascii_text.lower().replace("&", " and ").replace("'", "")
    return _NOT_ALPHANUMERIC.sub(" ", lower).split()


def roaster_key(name: str) -> str:
    """A roaster name in a form that ignores what never tells roasters apart.

    ``PT's Coffee Roasting Co.`` and ``PTs Coffee`` both become ``pts``. A name
    made only of filler words keeps them all: ``The Coffee Company`` stays
    ``the coffee company``.
    """
    words = _words(name)
    kept = [word for word in words if word not in FILLER_WORDS]
    return " ".join(kept or words)


def normalise_city(city: str) -> str:
    """A city name that ignores case, accents and the word *city*.

    ``Taoyuan City`` and ``taoyuan`` are the same city.
    """
    return " ".join(word for word in _words(city) if word != "city")
