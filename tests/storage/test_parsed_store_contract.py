"""The ParsedStore contract: every test runs against every implementation,
so the in-memory fake behaves exactly like the Parquet store."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from coffee_review.models.parsed import GreenReview, ParseIssue, RoastedReview
from coffee_review.storage.parsed_store import ParquetParsedStore
from tests.fakes import InMemoryParsedStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from coffee_review.storage.parsed_store import ParsedStore

URL = "https://www.coffeereview.com/review/{}/"


def roasted(slug: str, **fields: object) -> RoastedReview:
    return RoastedReview.model_validate(
        {"slug": slug, "url": URL.format(slug), "delisted": False, "sold_green": False}
        | fields
    )


def green(slug: str, **fields: object) -> GreenReview:
    return GreenReview.model_validate(
        {"slug": slug, "url": URL.format(slug), "delisted": False} | fields
    )


@pytest.fixture(params=["parquet", "in_memory"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ParsedStore]:
    parsed: ParsedStore = (
        ParquetParsedStore(tmp_path / "parsed")
        if request.param == "parquet"
        else InMemoryParsedStore()
    )
    yield parsed
    parsed.close()


def test_round_trip_both_datasets(store: ParsedStore) -> None:
    a = roasted("a", title="A", aroma="9", sold_green=True)
    b = green("b", title="B", lot_id="036", delisted=True)

    store.write([b, a], [], "# report")

    assert store.read_reviews("roasted") == [a]
    assert store.read_reviews("green") == [b]


def test_extra_fields_round_trip(store: ParsedStore) -> None:
    a = roasted("a", extra={"Mystery Label": "x", "section: Panel Comments": "y"})

    store.write([a], [], "")

    assert store.read_reviews("roasted")[0].extra == a.extra


def test_issues_round_trip(store: ParsedStore) -> None:
    issues = [
        ParseIssue(slug="b", kind="missing_required", detail="rating"),
        ParseIssue(slug="a", kind="no_review_template", detail="none"),
    ]

    store.write([], issues, "")

    assert store.read_issues() == sorted(issues, key=lambda i: (i.slug, i.kind))


def test_rewrite_replaces_previous_rows(store: ParsedStore) -> None:
    store.write([roasted("a"), roasted("b")], [], "")

    store.write([roasted("a")], [], "")

    assert [r.slug for r in store.read_reviews("roasted")] == ["a"]


def test_reviews_read_back_sorted_by_slug(store: ParsedStore) -> None:
    store.write([roasted("c"), roasted("a"), roasted("b")], [], "")

    assert [r.slug for r in store.read_reviews("roasted")] == ["a", "b", "c"]


def test_empty_dataset_reads_back_empty(store: ParsedStore) -> None:
    store.write([roasted("a")], [], "")

    assert store.read_reviews("green") == []
