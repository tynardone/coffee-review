"""Generate the complete lookup tables from ISO standards.

The clean stage reads hand-kept tables (``places.csv``, ``currencies.csv``…)
for the spellings reviews actually use. This script writes the two complete
lists those build on, from the ``pycountry`` package (a development dependency
only), so a country or currency no review has used yet is still recognised:

- ``countries.csv``: every ISO 3166 country and its spellings;
- ``currency_codes.csv``: every ISO 4217 currency code.

Run it again when pycountry updates (these rarely change):

    uv run python scripts/generate_lookup_tables.py
"""

from __future__ import annotations

import csv
from pathlib import Path

import pycountry

LOOKUPS = Path(__file__).parent.parent / "src/coffee_review/transforms/lookups"

# Spellings that mean something else in reviews: "Georgia" is the US state in
# roaster locations (places.csv), never the country, which isn't an origin.
CLAIMED_BY_PLACES = {"Georgia"}

# The name we use for a country, where pycountry's common name isn't the one
# people write (ISO names like "Congo, The Democratic Republic of the").
PREFERRED = {
    "BN": "Brunei",
    "CD": "Democratic Republic of the Congo",
    "CV": "Cape Verde",
    "CZ": "Czech Republic",
    "FM": "Micronesia",
    "GE": "Republic of Georgia",
    "PS": "Palestine",
    "RU": "Russia",
    "TR": "Turkey",
    "VA": "Vatican City",
}


def main() -> None:
    write_countries(LOOKUPS / "countries.csv")
    write_currency_codes(LOOKUPS / "currency_codes.csv")


def write_countries(out: Path) -> None:
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
            if "," not in alias and alias not in CLAIMED_BY_PLACES:
                rows.setdefault(alias, name)
    with out.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(["alias", "name"])
        writer.writerows(sorted(rows.items()))
    print(f"{len(rows)} spellings of {len(set(rows.values()))} countries → {out}")


def write_currency_codes(out: Path) -> None:
    codes = sorted({currency.alpha_3 for currency in pycountry.currencies})
    with out.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(["code"])
        writer.writerows([code] for code in codes)
    print(f"{len(codes)} currency codes → {out}")


if __name__ == "__main__":
    main()
