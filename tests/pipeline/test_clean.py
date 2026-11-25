"""Tests for coffee_review.pipeline.clean, against the in-memory fakes.

The parsed store is seeded with records parsed from the parser's fixture pages, the
same way a parse run fills it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pytest
from inline_snapshot import snapshot

from coffee_review.config import CleanSettings
from coffee_review.models.clean import (
    GREEN_SCHEMA,
    CleanContractError,
    validate_clean,
)
from coffee_review.models.parsed import GreenReview, RoastedReview
from coffee_review.parsers.review_page import parse_review_page
from coffee_review.pipeline import clean as clean_module
from coffee_review.pipeline.clean import UnloggedNullsError, run_clean
from coffee_review.storage.parsed_store import ParsedLayerMissingError
from tests.fakes import InMemoryCleanStore, InMemoryParsedStore

if TYPE_CHECKING:
    from coffee_review.models.parsed import ParsedReview

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pages" / "review"
URL = "https://www.coffeereview.com/review/{}/"


def fixture_record(name: str) -> ParsedReview:
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    slug = name.replace("_", "-")
    record = parse_review_page(slug, URL.format(slug), html, delisted=False).record
    assert record is not None
    return record


def roasted(slug: str, **fields: object) -> RoastedReview:
    return RoastedReview.model_validate(
        {"slug": slug, "url": URL.format(slug), "delisted": False, "sold_green": False}
        | {"title": "T", "review_date": "May 2020", "rating": "90"}
        | fields
    )


def green(slug: str, **fields: object) -> GreenReview:
    return GreenReview.model_validate(
        {"slug": slug, "url": URL.format(slug), "delisted": False}
        | {"title": "T", "review_date": "May 2020", "rating": "90"}
        | fields
    )


def parsed_with(*records: ParsedReview) -> InMemoryParsedStore:
    parsed = InMemoryParsedStore()
    parsed.write(list(records), [], "")
    return parsed


def test_cleans_both_datasets() -> None:
    parsed = parsed_with(fixture_record("modern_roasted"), fixture_record("green_new"))
    clean = InMemoryCleanStore()

    run_clean(parsed=parsed, clean=clean)

    assert clean.read_table("roasted").height == 1
    assert clean.read_table("green").height == 1
    validate_clean(clean.read_table("roasted"), "roasted")
    validate_clean(clean.read_table("green"), "green")
    validate_clean(clean.read_table("log"), "log")


def test_log_names_every_change() -> None:
    record = roasted("a", agtron="48/40", review_date="December 2002", rating="NR")
    clean = InMemoryCleanStore()

    run_clean(parsed=parsed_with(record), clean=clean)

    rules = clean.read_table("log").select("field", "rule").rows()
    assert ("agtron", "swapped_2002_2003") in rules
    assert ("rating", "not_rated") in rules


def test_empty_dataset_cleans() -> None:
    clean = InMemoryCleanStore()

    run_clean(parsed=parsed_with(roasted("a")), clean=clean)

    green = clean.read_table("green")
    assert green.height == 0
    assert green.schema == GREEN_SCHEMA


def test_unlogged_null_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def loses_ratings(frame: pl.LazyFrame) -> pl.LazyFrame:
        return frame.with_columns(
            pl.lit(None, dtype=pl.Int16).alias("rating"),
            pl.lit([], dtype=pl.List(pl.String)).alias("_rule_rating"),
        )

    monkeypatch.setattr(clean_module, "clean_rating", loses_ratings)
    clean = InMemoryCleanStore()

    with pytest.raises(UnloggedNullsError, match="a: rating"):
        run_clean(parsed=parsed_with(roasted("a")), clean=clean)
    assert clean.writes == 0


def test_contract_failure_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def impossible_ratings(frame: pl.LazyFrame) -> pl.LazyFrame:
        return frame.with_columns(
            pl.lit(101, dtype=pl.Int16).alias("rating"),
            pl.lit([], dtype=pl.List(pl.String)).alias("_rule_rating"),
        )

    monkeypatch.setattr(clean_module, "clean_rating", impossible_ratings)
    clean = InMemoryCleanStore()

    with pytest.raises(CleanContractError, match="rating"):
        run_clean(parsed=parsed_with(roasted("a")), clean=clean)
    assert clean.writes == 0


def test_missing_parsed_layer() -> None:
    with pytest.raises(ParsedLayerMissingError):
        run_clean(parsed=InMemoryParsedStore(), clean=InMemoryCleanStore())


def test_report_written() -> None:
    clean = InMemoryCleanStore()

    result = run_clean(parsed=parsed_with(roasted("a")), clean=clean)

    assert clean.report_markdown.startswith("# Clean report")
    assert result.report_location == "in memory"
    assert result.report.rows_by_dataset == {"roasted": 1, "green": 0}


def test_elapsed_from_clock() -> None:
    ticks = iter([10.0, 12.5])

    result = run_clean(
        parsed=parsed_with(roasted("a")),
        clean=InMemoryCleanStore(),
        clock=lambda: next(ticks),
    )

    assert result.elapsed_seconds == 2.5


def test_summary_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="coffee_review")

    run_clean(parsed=parsed_with(roasted("a")), clean=InMemoryCleanStore())

    assert "clean finished: 1 rows · 1 roasted, 0 green" in caplog.text


def test_new_values_logged_as_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="coffee_review")
    parsed = parsed_with(
        roasted(
            "a", coffee_origin="Atlantis", roaster_location="Sacramento, Califonria"
        )
    )
    clean = InMemoryCleanStore()

    run_clean(parsed=parsed, clean=clean)

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == [
        "2 new values to review in in memory (1 unplaced, 1 close spelling)"
    ]

    caplog.clear()
    run_clean(parsed=parsed, clean=clean)  # the same values again: nothing new

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_end_to_end_snapshot() -> None:
    clean = InMemoryCleanStore()

    run_clean(parsed=parsed_with(fixture_record("modern_roasted")), clean=clean)

    row = clean.read_table("roasted").row(0, named=True)
    row = {
        k: v
        for k, v in row.items()
        if k not in {"blind_assessment", "notes", "bottom_line"}
    }
    assert row == snapshot(
        {
            "slug": "modern-roasted",
            "url": "https://www.coffeereview.com/review/modern-roasted/",
            "delisted": False,
            "sold_green": False,
            "title": "Ethiopia Guji Muli Garden Natural G1",
            "roaster": "1980 CAFE",
            "roaster_url": "https://www.1980cafetw.com/",
            "review_month": date(2024, 11, 1),
            "rating": 93,
            "roaster_city": "Tainan",
            "roaster_region": None,
            "roaster_country": "Taiwan",
            "origin_country": "Ethiopia",
            "origin_region": "Guji, Oromia",
            "roast_level": "Medium",
            "agtron_whole": 58,
            "agtron_ground": 77,
            "agtron_swapped": False,
            "agtron_corrected": False,
            "agtron_suspect": False,
            "price_amount": 399.0,
            "price_currency": "TWD",
            "price_currency_inferred": False,
            "price_quantity": 227.0,
            "price_unit": "gram",
            "price_grams": 227.0,
            "price_note": None,
            "aroma": 9.0,
            "acidity": 8.0,
            "body": None,
            "flavor": 9.0,
            "aftertaste": 8.0,
            "with_milk": None,
            "extra_count": 0,
        }
    )


def test_odd_values_do_not_stop_the_run() -> None:
    # From the final review: each of these used to break the contract.
    record = roasted("a", price="$0.00/12 ounces", agtron="55/0.4")
    clean = InMemoryCleanStore()

    run_clean(parsed=parsed_with(record), clean=clean)

    rules = clean.read_table("log").select("field", "rule").rows()
    assert ("price", "suspect_value") in rules
    assert ("agtron", "suspect_value") in rules


def test_roaster_variants_become_one_name() -> None:
    records = [
        roasted(
            "a", roaster="JBC Coffee Roasters", roaster_location="Madison, Wisconsin"
        ),
        roasted(
            "b", roaster="JBC Coffee Roasters", roaster_location="Madison, Wisconsin"
        ),
        roasted("c", roaster="JBC", roaster_location="Madison, Wisconsin"),
    ]
    clean = InMemoryCleanStore()

    run_clean(parsed=parsed_with(*records), clean=clean)

    assert clean.read_table("roasted")["roaster"].unique().to_list() == [
        "JBC Coffee Roasters"
    ]
    log = clean.read_table("log").filter(pl.col("field") == "roaster")
    assert log.select("slug", "rule", "raw_value").rows() == [
        ("c", "spelling_variant", "JBC")
    ]


def test_roaster_names_cover_green_reviews() -> None:
    records: list[ParsedReview] = [
        roasted("a", roaster="Royal Coffee", roaster_location="Oakland, California"),
        roasted("b", roaster="Royal Coffee", roaster_location="Oakland, California"),
        green("g", roaster="ROYAL COFFEE INC"),
    ]
    clean = InMemoryCleanStore()

    run_clean(parsed=parsed_with(*records), clean=clean)

    assert clean.read_table("green")["roaster"].to_list() == ["Royal Coffee"]


def test_roaster_thresholds_come_from_settings() -> None:
    records = [
        roasted("a", roaster="La Mill", roaster_location="Los Angeles, California"),
        roasted("b", roaster="La Mill", roaster_location="Los Angeles, California"),
        roasted("c", roaster="Lamill", roaster_location="Los Angeles, California"),
    ]
    clean = InMemoryCleanStore()

    run_clean(
        parsed=parsed_with(*records),
        clean=clean,
        settings=CleanSettings(roaster_merge_at=0.95),  # la mill / lamill: 0.92
    )

    assert clean.read_table("roasted")["roaster"].to_list() == [
        "La Mill",
        "La Mill",
        "Lamill",
    ]
