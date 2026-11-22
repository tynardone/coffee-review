"""Tests for coffee_review.pipeline.parse_report: coverage, unknowns, shapes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from coffee_review.models.parsed import GreenReview, ParseIssue, RoastedReview
from coffee_review.pipeline.parse_report import (
    SHAPE_FIELDS,
    ParseReport,
    build_parse_report,
    render_report_markdown,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

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


def report_for(
    records: Sequence[RoastedReview | GreenReview],
    issues: list[ParseIssue] | None = None,
    *,
    skipped: int = 0,
) -> ParseReport:
    return build_parse_report(
        records, issues or [], pages=len(records) + skipped, skipped=skipped
    )


def test_totals_by_dataset_and_sold_green() -> None:
    report = report_for(
        [roasted("a", sold_green=True), roasted("b"), green("c")], skipped=2
    )

    assert report.pages == 5
    assert report.skipped == 2
    assert report.by_dataset == {"roasted": 2, "green": 1}
    assert report.sold_green == 1


def test_coverage_counts_non_none_per_dataset() -> None:
    report = report_for([roasted("a", aroma="9"), roasted("b"), green("c", price="$5")])

    assert report.coverage["roasted"]["aroma"] == 1
    assert report.coverage["roasted"]["title"] == 0
    assert report.coverage["green"]["price"] == 1
    assert "aroma" not in report.coverage["green"]


def test_unknown_labels_counted_with_examples() -> None:
    records = [roasted(f"r{i}", extra={"Mystery Label": "x"}) for i in range(5)]

    report = report_for(records)

    count, examples = report.unknown["Mystery Label"]
    assert count == 5
    assert examples == ["r0", "r1", "r2"]


def test_shapes_most_common_first_with_example() -> None:
    report = report_for(
        [
            roasted("a", price="$34.00/12 ounces"),
            roasted("b", price="$18.00/12 ounces"),
            roasted("c", price="NA"),
        ]
    )

    assert report.shapes["price"][0] == ("$99.99/99 a", 2, "a", "$34.00/12 ounces")
    assert report.shapes["price"][1] == ("a", 1, "c", "NA")


def test_shapes_ties_broken_by_shape() -> None:
    report = report_for([roasted("a", agtron="58/80"), roasted("b", agtron="NA")])

    assert [shape for shape, *_ in report.shapes["agtron"]] == ["99/99", "a"]


def test_shapes_capped_at_10() -> None:
    records = [roasted(f"r{i}", agtron="9" * (i + 1)) for i in range(15)]

    assert len(report_for(records).shapes["agtron"]) == 10


def test_text_sections_have_no_shapes() -> None:
    assert "blind_assessment" not in SHAPE_FIELDS
    assert "price" in SHAPE_FIELDS
    assert "rating" in SHAPE_FIELDS
    assert "roaster_url" in SHAPE_FIELDS


def test_issues_by_kind() -> None:
    issues = [
        ParseIssue(slug="a", kind="missing_required", detail="rating"),
        ParseIssue(slug="b", kind="missing_required", detail="title"),
        ParseIssue(slug="c", kind="page_missing", detail="no file"),
    ]

    report = report_for([], issues)

    assert report.issues_by_kind == {"missing_required": 2, "page_missing": 1}


def test_summary_line_exact() -> None:
    records = [roasted(f"r{i}", sold_green=i < 42) for i in range(9330)] + [
        green(f"g{i}") for i in range(110)
    ]
    for i in range(3):
        records[i] = roasted(f"r{i}", extra={f"Label {i}": "x"})
    issues = [
        ParseIssue(slug=f"r{i}", kind="missing_required", detail="rating")
        for i in range(7)
    ]

    line = report_for(records, issues).summary_line(elapsed_seconds=24)

    assert line == (
        "parse finished: 9,440 pages · 9,330 roasted (39 sold green), 110 green"
        " · 7 issues · 3 unknown labels · in 24s"
    )


def test_markdown_has_all_sections() -> None:
    issues = [ParseIssue(slug="a", kind="missing_required", detail="rating")]
    report = report_for(
        [roasted("a", price="NA", extra={"Mystery Label": "x"}), green("b")], issues
    )

    markdown = render_report_markdown(report, issues)

    for heading in (
        "# Parse report",
        "## Totals",
        "## Coverage: roasted",
        "## Coverage: green",
        "## Unknown labels and headings",
        "## Value shapes",
        "## Issues",
    ):
        assert heading in markdown
    assert "`Mystery Label`" in markdown
    assert "missing_required" in markdown


def test_markdown_escapes_pipes_in_values() -> None:
    report = report_for([roasted("a", price="$5 | $7")])

    assert "`$5 \\| $7`" in render_report_markdown(report, [])
