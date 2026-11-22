"""Generate ``transforms/lookups/countries.csv``: every country, every spelling.

The clean stage places origins and roaster locations by looking their parts up
in its tables. ``places.csv`` is hand-kept (other spellings, states, islands,
broad areas); this script writes the complete list of countries from ISO 3166
(via the ``pycountry`` package, a development dependency only), so a country
the reviews have never mentioned is still recognised.

Run it again when pycountry updates (countries rarely change):

    uv run python scripts/generate_countries.py
"""

from __future__ import annotations

import csv
from pathlib import Path

import pycountry

OUT = (
    Path(__file__).parent.parent / "src/coffee_review/transforms/lookups/countries.csv"
)

# The name we use for a country, where pycountry's common name isn't the one
# people write (ISO names like "Congo, The Democratic Republic of the").
PREFERRED = {
    "BN": "Brunei",
    "CD": "Democratic Republic of the Congo",
    "CV": "Cape Verde",
    "CZ": "Czech Republic",
    "FM": "Micronesia",
    "PS": "Palestine",
    "RU": "Russia",
    "TR": "Turkey",
    "VA": "Vatican City",
}


def main() -> None:
    rows: dict[str, str] = {}  # alias → name
    for country in pycountry.countries:
        name = PREFERRED.get(country.alpha_2) or getattr(
            country, "common_name", country.name
        )
        spellings = {
            name,
            country.name,
            getattr(country, "common_name", None),
            getattr(country, "official_name", None),
        }
        for alias in sorted(s for s in spellings if s):
            # A spelling with a comma ("Korea, Republic of") can't match: the
            # clean stage splits places on commas before looking them up.
            if "," not in alias:
                rows.setdefault(alias, name)
    with OUT.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(["alias", "name"])
        writer.writerows(sorted(rows.items()))
    print(f"{len(rows)} spellings of {len(set(rows.values()))} countries → {OUT}")


if __name__ == "__main__":
    main()
