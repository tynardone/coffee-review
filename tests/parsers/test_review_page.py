"""Tests for coffee_review.parsers.review_page: one review page → one record.

The fixture corpus in tests/fixtures/pages/review/ has one cut-down page per
layout variant (see each file's header comment). ``test_parses_fixture`` pins
the full result for each with an inline snapshot; the focused tests below pin
each rule on its own, so a failure says which rule broke.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from inline_snapshot import snapshot

from coffee_review.models.parsed import (
    GreenReview,
    ParsedPage,
    ParseIssue,
    RoastedReview,
)
from coffee_review.parsers.review_page import parse_review_page

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pages" / "review"
FIXTURE_NAMES = sorted(p.stem for p in FIXTURES.glob("*.html"))


def parse_fixture(name: str, *, delisted: bool = False) -> ParsedPage:
    slug = name.replace("_", "-")
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    return parse_review_page(
        slug, f"https://www.coffeereview.com/review/{slug}/", html, delisted=delisted
    )


def record(name: str) -> RoastedReview | GreenReview:
    page = parse_fixture(name)
    assert page.record is not None
    return page.record


def roasted(name: str) -> RoastedReview:
    result = record(name)
    assert isinstance(result, RoastedReview)
    return result


def green(name: str) -> GreenReview:
    result = record(name)
    assert isinstance(result, GreenReview)
    return result


def minimal_page(rows: str = "", sections: str = "", template_class: str = "") -> str:
    """A small page in the site's template, for rules a fixture doesn't show."""
    return f"""<html><body><div class="entry-content">
<div class="review-template {template_class}">
  <div class="row row-1">
    <div class="column col-1"><span class="review-template-rating">90</span></div>
    <div class="column col-2"><p class="review-roaster">R</p>
      <h1 class="review-title">T</h1></div>
  </div>
  <div class="row row-2"><div class="column col-1">
    <table class="review-template-table">
      <tr><td>Review Date:</td><td>May 2020</td></tr>
      {rows}
    </table>
  </div></div>
  {sections}
</div></div></body></html>"""


def parse_html(html: str) -> ParsedPage:
    return parse_review_page(
        "x", "https://www.coffeereview.com/review/x/", html, delisted=False
    )


# --- every fixture, whole ------------------------------------------------------

FIXTURE_SNAPSHOTS = snapshot(
    {
        "espresso": ParsedPage(
            record=RoastedReview(
                slug="espresso",
                url="https://www.coffeereview.com/review/espresso/",
                delisted=False,
                title="100% Arabica Nespresso-Compatible Capsules",
                roaster="Caffe Bomrad",
                rating="88",
                review_date="March 2025",
                coffee_origin="Not disclosed",
                price="$9.99/10 capsules",
                blind_assessment="Placeholder blind assessment, paragraph 1.",
                notes="Placeholder notes, paragraph 1.",
                bottom_line="Placeholder bottom line, paragraph 1.",
                sold_green=False,
                roaster_location="Milano, Italy",
                roast_level="NA",
                agtron="0/0",
                aroma="8",
                body="7",
                flavor="8",
                aftertaste="7",
                with_milk="8",
            )
        ),
        "green_new": ParsedPage(
            record=GreenReview(
                slug="green-new",
                url="https://www.coffeereview.com/review/green-new/",
                delisted=False,
                title="Duwancho Kebele Natural 74110 G1",
                roaster="Dawencho G/H/D Bunna Amrach",
                rating="93",
                review_date="January 2022",
                coffee_origin="Ethiopia",
                price="$47.30/kg",
                blind_assessment="Placeholder blind assessment, paragraph 1.",
                notes="Placeholder notes, paragraph 1.",
                harvested="January to March 2021",
                seller="Guangzhou Inge International Trade Co., Limited",
                variety="74110",
                lot_size="1,900 kg (4,189 lbs)",
                processing_method="Natural",
                lot_id="036",
            )
        ),
        "green_old": ParsedPage(
            record=GreenReview(
                slug="green-old",
                url="https://www.coffeereview.com/review/green-old/",
                delisted=False,
                title="Boquete Premium – Green",
                roaster="Café Garrido",
                rating="83",
                review_date="July 2000",
                blind_assessment="Placeholder blind assessment, paragraph 1.",
                notes="Placeholder notes, paragraph 1.",
            )
        ),
        "modern_roasted": ParsedPage(
            record=RoastedReview(
                slug="modern-roasted",
                url="https://www.coffeereview.com/review/modern-roasted/",
                delisted=False,
                title="Ethiopia Guji Muli Garden Natural G1",
                roaster="1980 CAFE",
                roaster_url="https://www.1980cafetw.com/",
                rating="93",
                review_date="November 2024",
                coffee_origin="Guji Zone, Oromia region, southern Ethiopia",
                price="NT $399/227 grams",
                blind_assessment="Placeholder blind assessment, paragraph 1.",
                notes="Placeholder notes, paragraph 1.",
                bottom_line="Placeholder bottom line, paragraph 1.",
                sold_green=False,
                roaster_location="Tainan, Taiwan",
                roast_level="Medium",
                agtron="58/77",
                aroma="9",
                acidity="8",
                flavor="9",
                aftertaste="8",
            )
        ),
        "no_closing_section": ParsedPage(
            record=RoastedReview(
                slug="no-closing-section",
                url="https://www.coffeereview.com/review/no-closing-section/",
                delisted=False,
                title="Café Virgen de Oro",
                roaster="Exportadora de Café Condor",
                rating="82",
                review_date="August 1999",
                blind_assessment="Placeholder blind assessment, paragraph 1.",
                notes="Placeholder notes, paragraph 1.",
                sold_green=False,
                roaster_location="Antioquia, Colombia",
                roast_level="NA",
                agtron="/",
                aroma="6",
                acidity="6",
                body="6",
                flavor="6",
            )
        ),
        "no_template": ParsedPage(
            record=None,
            issues=[
                ParseIssue(
                    slug="no-template",
                    kind="no_review_template",
                    detail="no div.review-template",
                )
            ],
        ),
        "older_roasted": ParsedPage(
            record=RoastedReview(
                slug="older-roasted",
                url="https://www.coffeereview.com/review/older-roasted/",
                delisted=False,
                title="100% Arabica Instant Coffee",
                roaster="Yuban",
                rating="72",
                review_date="March 2011",
                coffee_origin="Colombia. All coffee of the Arabica species.",
                price="$8.27/8 ounces instant",
                blind_assessment="Placeholder blind assessment, paragraph 1.",
                notes="Placeholder notes, paragraph 1.",
                bottom_line="Placeholder who should drink it, paragraph 1.",
                sold_green=False,
                roaster_location="Northfield, Illinois",
                roast_level="NA",
                agtron="0/0",
                aroma="4",
                acidity="4",
                body="7",
                flavor="3",
                aftertaste="4",
            )
        ),
        "sold_green": ParsedPage(
            record=RoastedReview(
                slug="sold-green",
                url="https://www.coffeereview.com/review/sold-green/",
                delisted=False,
                title="Bourbon – Green",
                roaster="Larin",
                rating="76",
                review_date="September 1997",
                blind_assessment="Placeholder blind assessment, paragraph 1.",
                notes="Placeholder notes, paragraph 1.",
                bottom_line="Placeholder who should drink it, paragraph 1.",
                sold_green=True,
                roaster_location="El Salvador",
                roast_level="NA",
                agtron="/",
                aroma="6.3",
                acidity="5.5",
                body="5.6",
                flavor="5.8",
            )
        ),
        "unclosed_td": ParsedPage(
            record=RoastedReview(
                slug="unclosed-td",
                url="https://www.coffeereview.com/review/unclosed-td/",
                delisted=False,
                title="100% Ka‘ū Navarro",
                roaster="Pacific Coffee Research",
                rating="93",
                review_date="February 2024",
                coffee_origin="Ka‘ū growing region, “Big Island” of Hawai’i",
                price="$39.20/10 ounces",
                blind_assessment="Placeholder blind assessment, paragraph 1.",
                notes="Placeholder notes, paragraph 1.",
                bottom_line="Placeholder bottom line, paragraph 1.",
                sold_green=False,
                roaster_location="Kealakekua, Kona, “Big Island” of Hawai’i",
                roast_level="Medium-Light",
                agtron="60/84",
                aroma="9",
                acidity="8",
                body="9",
                flavor="9",
                aftertaste="8",
            )
        ),
        "unknown_fields": ParsedPage(
            record=GreenReview(
                slug="unknown-fields",
                url="https://www.coffeereview.com/review/unknown-fields/",
                delisted=False,
                title="Placeholder Lot – Natural",
                roaster="Placeholder Importers & Co.",
                rating="88",
                review_date="March 2023",
                price="$7.50/pound",
                blind_assessment="""\
Placeholder blind assessment, first paragraph.

Placeholder blind assessment, second paragraph & an entity.\
""",
                notes="Placeholder notes, paragraph 1.",
                extra={
                    "Mystery Label": "Something new",
                    "Aroma": "8",
                    "section: Panel Comments": "Placeholder panel comments.",
                },
                seller="Placeholder Seller",
            )
        ),
    }
)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_parses_fixture(name: str) -> None:
    assert parse_fixture(name) == FIXTURE_SNAPSHOTS[name]


# --- one rule each ---------------------------------------------------------------


def test_dataset_from_class() -> None:
    assert record("green_old").dataset == "green"
    assert record("green_new").dataset == "green"
    assert record("modern_roasted").dataset == "roasted"


def test_sold_green_from_title() -> None:
    assert roasted("sold_green").sold_green is True
    assert roasted("modern_roasted").sold_green is False


def test_delisted_is_passed_through() -> None:
    page = parse_fixture("modern_roasted", delisted=True)

    assert page.record is not None
    assert page.record.delisted is True


def test_unknown_label_goes_to_extra() -> None:
    assert green("unknown_fields").extra["Mystery Label"] == "Something new"


def test_unknown_heading_goes_to_extra() -> None:
    extra = green("unknown_fields").extra

    assert extra["section: Panel Comments"] == "Placeholder panel comments."


def test_wrong_dataset_label_goes_to_extra() -> None:
    review = green("unknown_fields")

    assert review.extra["Aroma"] == "8"
    assert "aroma" not in GreenReview.model_fields


def test_known_labels_found_under_any_alias() -> None:
    assert roasted("modern_roasted").acidity == "8"  # "Acidity/Structure"
    assert roasted("older_roasted").acidity is not None  # "Acidity"
    assert roasted("older_roasted").bottom_line is not None  # "Who Should Drink It"
    assert green("unknown_fields").price == "$7.50/pound"  # "Estimated Price"


def test_duplicate_label_keeps_first() -> None:
    page = parse_html(
        minimal_page(
            rows="<tr><td>Aroma:</td><td>8</td></tr><tr><td>Aroma:</td><td>9</td></tr>"
        )
    )

    assert isinstance(page.record, RoastedReview)
    assert page.record.aroma == "8"
    assert [issue.kind for issue in page.issues] == ["duplicate_label"]


def test_missing_required_is_kept_with_issue() -> None:
    html = minimal_page().replace('<span class="review-template-rating">90</span>', "")

    page = parse_html(html)

    assert page.record is not None
    assert page.record.rating is None
    assert page.issues == [
        ParseIssue(slug="x", kind="missing_required", detail="rating")
    ]


def test_no_template_gives_issue_and_no_record() -> None:
    page = parse_fixture("no_template")

    assert page.record is None
    assert [issue.kind for issue in page.issues] == ["no_review_template"]


def test_blank_and_nbsp_values_are_none() -> None:
    assert green("unknown_fields").coffee_origin is None  # the cell is just &nbsp;

    page = parse_html(minimal_page(rows="<tr><td>Body:</td><td>   </td></tr>"))
    assert isinstance(page.record, RoastedReview)
    assert page.record.body is None


def test_sections_stop_at_first_ignored_heading() -> None:
    page = parse_html(
        minimal_page(
            sections="""
  <h2>Bottom Line</h2><p>Real bottom line.</p>
  <h2>Explore Similar Coffees</h2><p>Related.</p>
  <h2>Notes</h2><p>Sidebar text that is not the review.</p>
"""
        )
    )

    assert page.record is not None
    assert page.record.bottom_line == "Real bottom line."
    assert page.record.notes is None


def test_section_paragraphs_joined_with_blank_line() -> None:
    assert green("unknown_fields").blind_assessment == (
        "Placeholder blind assessment, first paragraph.\n\n"
        "Placeholder blind assessment, second paragraph & an entity."
    )


def test_entities_decoded() -> None:
    review = green("unknown_fields")

    assert review.title == "Placeholder Lot – Natural"
    assert review.roaster == "Placeholder Importers & Co."


def test_unclosed_td_value() -> None:
    # The fixture's page has a row like <td>9 </tr> with no closing </td>.
    review = roasted("unclosed_td")

    assert review.body is not None
    assert review.body.isdigit()


def test_roaster_url_from_logo_link() -> None:
    assert roasted("modern_roasted").roaster_url == "https://www.1980cafetw.com/"


def test_link_text_inside_a_value() -> None:
    assert green("unknown_fields").seller == "Placeholder Seller"


def test_inline_tags_do_not_split_words() -> None:
    # Real pages: caf<a>é</a>, Coffee Review<a>’s</a>, P<strong>roduced</strong>.
    page = parse_html(
        minimal_page(
            sections="<h2>Notes</h2><p>caf<a href='#'>é</a> by Coffee Review"
            "<a href='#'>’s</a> panel. P<strong>roduced</strong> at "
            "<a href='#'>example.com</a>.</p>"
        )
    )

    assert page.record is not None
    assert page.record.notes == (
        "café by Coffee Review’s panel. Produced at example.com."
    )


def test_line_breaks_and_blocks_separate_words() -> None:
    page = parse_html(
        minimal_page(
            rows="<tr><td>Coffee Origin:</td><td>Kona<br>Hawaii</td></tr>",
            sections="<h2>Notes</h2><div><p>First.</p><p>Second.</p></div>",
        )
    )

    assert page.record is not None
    assert page.record.coffee_origin == "Kona Hawaii"
    assert page.record.notes == "First. Second."


def test_blank_unknown_label_is_kept() -> None:
    page = parse_html(minimal_page(rows="<tr><td>Mystery:</td><td> </td></tr>"))

    assert page.record is not None
    assert page.record.extra == {"Mystery": ""}


def test_empty_unknown_heading_is_kept() -> None:
    page = parse_html(minimal_page(sections="<h2>Panel Comments</h2>"))

    assert page.record is not None
    assert page.record.extra == {"section: Panel Comments": ""}
