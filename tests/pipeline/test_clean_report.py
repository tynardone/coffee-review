"""Tests for coffee_review.pipeline.clean_report."""

from __future__ import annotations

import polars as pl
from inline_snapshot import snapshot

from coffee_review.models.clean import LOG_SCHEMA
from coffee_review.pipeline.clean_report import (
    ReviewItem,
    RoasterNamesSummary,
    build_clean_report,
    render_clean_report,
)
from coffee_review.transforms.roasters import NameDecision, NameMap
from tests.models.test_clean_contract import GREEN_ROW, ROASTED_ROW, frame


def log(*rows: tuple[str, str, str, str, str | None]) -> pl.DataFrame:
    """Log rows as (slug, dataset, field, rule, raw value)."""
    return pl.DataFrame(list(rows), schema=LOG_SCHEMA, orient="row")


def roasted(*slugs: str, **fields: object) -> pl.DataFrame:
    return frame("roasted", *(ROASTED_ROW | {"slug": s} | fields for s in slugs))


NO_GREEN = frame("green").clear()
NO_LOG = log()


def test_rules_counted_with_examples() -> None:
    entries = [(f"r{i}", "roasted", "rating", "not_rated", "NR") for i in range(5)]

    report = build_clean_report(roasted("r0"), NO_GREEN, log(*entries), NO_LOG)

    [rule] = report.rules
    assert (rule.field, rule.rule, rule.rows) == ("rating", "not_rated", 5)
    assert rule.examples == [("r0", "NR"), ("r1", "NR"), ("r2", "NR")]


def test_to_review_lists_distinct_values() -> None:
    entries = log(
        ("a", "roasted", "coffee_origin", "unplaced", "Atlantis"),
        ("b", "roasted", "coffee_origin", "unplaced", "Atlantis"),
        ("c", "roasted", "rating", "not_rated", "NR"),  # handled: not to review
    )

    report = build_clean_report(roasted("a", "b", "c"), NO_GREEN, entries, NO_LOG)

    assert report.to_review == [
        ReviewItem("coffee_origin", "unplaced", "Atlantis", 2, None, new=True)
    ]


def test_to_review_marks_new_values() -> None:
    entries = log(
        ("a", "roasted", "coffee_origin", "unplaced", "Atlantis"),
        ("b", "roasted", "coffee_origin", "unplaced", "Lemuria"),
    )
    previous = log(("x", "roasted", "coffee_origin", "unplaced", "Atlantis"))

    report = build_clean_report(roasted("a", "b"), NO_GREEN, entries, previous)

    assert {(i.raw_value, i.new) for i in report.to_review} == {
        ("Atlantis", False),
        ("Lemuria", True),
    }
    assert report.new_to_review == {"unplaced": 1}


def test_close_spellings_show_what_they_matched() -> None:
    clean = roasted("a", roaster_region="California", roaster_country="United States")
    entries = log(
        ("a", "roasted", "roaster_location", "close_spelling", "Sacramento, Calfornia")
    )

    report = build_clean_report(clean, NO_GREEN, entries, NO_LOG)

    [item] = report.to_review
    assert item.placed_as == "California, United States"


def test_coverage_per_column() -> None:
    clean = frame("roasted", ROASTED_ROW, ROASTED_ROW | {"slug": "b", "acidity": None})

    report = build_clean_report(clean, NO_GREEN, NO_LOG, NO_LOG)

    assert report.coverage["roasted"]["acidity"] == 1
    assert report.coverage["roasted"]["slug"] == 2
    assert report.coverage["green"]["harvest_year"] == 0


def test_distributions() -> None:
    clean = roasted("a", "b", "c")

    report = build_clean_report(clean, frame("green"), NO_LOG, NO_LOG)

    assert report.distributions["origin country"][:2] == [
        ("Ethiopia", 3),
        ("Taiwan", 1),
    ]
    assert report.distributions["currency"][0] == ("USD", 3)


def test_summary_line_exact() -> None:
    clean = roasted(*(f"r{i}" for i in range(9330)))
    green = frame("green", *(GREEN_ROW | {"slug": f"g{i}"} for i in range(110)))
    entries = [(f"r{i}", "roasted", "rating", "not_rated", "NR") for i in range(1216)]
    entries += [
        (f"r{i}", "roasted", "coffee_origin", "unplaced", f"Place {i}")
        for i in range(18)
    ]
    previous = log(*entries[1216:1231])  # 15 of the 18 seen last run

    report = build_clean_report(clean, green, log(*entries), previous)

    assert report.summary_line(elapsed_seconds=2) == (
        "clean finished: 9,440 rows · 9,330 roasted, 110 green · 1,234 log rows"
        " · 18 to review (3 new) · in 2s"
    )


def test_markdown_sections() -> None:
    entries = log(("a", "roasted", "coffee_origin", "unplaced", "Atlantis"))
    report = build_clean_report(roasted("a"), frame("green"), entries, NO_LOG)

    markdown = render_clean_report(report)

    for heading in (
        "# Clean report",
        "## Totals",
        "## Rules applied",
        "## To review",
        "## Coverage: roasted",
        "## Coverage: green",
        "## Distributions",
    ):
        assert heading in markdown
    assert "| coffee_origin | unplaced | `Atlantis` | 1 | – | **new** |" in markdown


def test_markdown_escapes_pipes() -> None:
    entries = log(("a", "roasted", "price", "unparsed", "$5 | $7"))
    report = build_clean_report(roasted("a"), NO_GREEN, entries, NO_LOG)

    assert "`$5 \\| $7`" in render_clean_report(report)


NAMES = NameMap(
    {
        "Boyd Coffee": NameDecision("Boyd Coffee", ()),
        "Boyds": NameDecision("Boyds", ("possible_match",), ("Boyd Coffee", "Boyd's")),
        "Lamill": NameDecision("La Mill", ("close_spelling",)),
        "La Mill": NameDecision("La Mill", ()),
    },
    ("Gone Roasters",),
)


def test_possible_match_lists_its_candidates() -> None:
    entries = log(("a", "roasted", "roaster", "possible_match", "Boyds"))

    report = build_clean_report(roasted("a"), NO_GREEN, entries, NO_LOG, NAMES)

    [item] = report.to_review
    assert item.placed_as == "Boyd Coffee; Boyd's"


def test_close_spelling_shows_merged_name() -> None:
    entries = log(("a", "roasted", "roaster", "close_spelling", "Lamill"))

    report = build_clean_report(
        roasted("a", roaster="La Mill"), NO_GREEN, entries, NO_LOG, NAMES
    )

    [item] = report.to_review
    assert item.placed_as == "La Mill"


def test_roaster_names_summarised() -> None:
    report = build_clean_report(roasted("a"), NO_GREEN, NO_LOG, NO_LOG, NAMES)

    assert report.roaster_names == RoasterNamesSummary(
        spellings=4,
        roasters=3,
        merged={"close_spelling": 1},
        to_review=1,
        unused_rows=("Gone Roasters",),
    )


def test_report_has_roaster_names_section() -> None:
    report = build_clean_report(roasted("a"), NO_GREEN, NO_LOG, NO_LOG, NAMES)

    markdown = render_clean_report(report)

    section = markdown.split("## Roaster names")[1].split("\n## ")[0]
    assert section == snapshot("""\


4 spellings became 3 roasters; 1 possible match to review.

| Merged by | Spellings |
|---|---|
| close_spelling | 1 |

Rows in `roasters.csv` or `distinct_roasters.csv` no review uses:

- `Gone Roasters`
""")


def test_possible_match_seen_from_the_other_side_is_not_new() -> None:
    # Last run, "Boyd Coffee" carried the pair; this run "Boyds" does.
    entries = log(("a", "roasted", "roaster", "possible_match", "Boyds"))
    previous = log(("x", "roasted", "roaster", "possible_match", "Boyd Coffee"))

    report = build_clean_report(roasted("a"), NO_GREEN, entries, previous, NAMES)

    [item] = report.to_review
    assert item.new is False


def test_roaster_names_count_merges_by_rule() -> None:
    report = build_clean_report(roasted("a"), NO_GREEN, NO_LOG, NO_LOG, NAMES)

    assert report.roaster_names is not None
    assert report.roaster_names.merged == {"close_spelling": 1}


def test_review_note_says_how_to_undo_a_roaster_merge() -> None:
    markdown = render_clean_report(
        build_clean_report(roasted("a"), NO_GREEN, NO_LOG, NO_LOG, NAMES)
    )
    assert "undone with a `distinct_roasters.csv` row" in markdown
