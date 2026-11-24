"""The parse stage's contracts.

One record per review page, and the issues found along the way.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from coffee_review.models.common import FrozenModel, Slug

# --- Parsed records ----------------------------------------------------------
#
# Every value is the text as it appears on the page, cleaned of tags and extra
# whitespace; the clean stage turns it into numbers, dates and so on. A field
# that isn't on the page is None, never a guess.
#
# Roasted and green reviews are different models, so a record can only hold
# the fields its kind of review has: a green review has no aroma, a roasted one
# no lot ID. Unknown fields are refused (extra="forbid"), not silently dropped.

type Dataset = Literal["roasted", "green"]


class _ReviewBase(BaseModel):
    """What every review page has, roasted or green."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: Slug = Field(description="The review's id: the last part of its URL.")
    url: str = Field(description="The review page's address.")
    delisted: bool = Field(
        description="True when the review is no longer in the site's sitemaps."
    )

    title: str | None = Field(None, description="The coffee's name as reviewed.")
    roaster: str | None = Field(
        None,
        description=(
            "The roaster (for green reviews, the importer or seller) as written."
        ),
    )
    roaster_url: str | None = Field(None, description="The link on the roaster's logo.")
    rating: str | None = Field(
        None, description="The overall score as written: 50–100, or NR (not rated)."
    )
    review_date: str | None = Field(
        None, description='The review month as written: "Month YYYY".'
    )
    coffee_origin: str | None = Field(
        None,
        description=(
            'Where the coffee was grown, as written ("Guji Zone, Oromia Region, '
            'southern Ethiopia").'
        ),
    )
    price: str | None = Field(
        None, description='The price as written ("NT $700/200 grams", "NA").'
    )
    blind_assessment: str | None = Field(
        None, description="The reviewers' tasting notes, written blind."
    )
    notes: str | None = Field(
        None, description="Background on the coffee, the farm and the roaster."
    )
    bottom_line: str | None = Field(
        None, description='The summary judgement ("Who Should Drink It" before 2016).'
    )

    # Labels and headings the alias table doesn't know, or that don't belong to
    # this kind of review, kept as found.
    extra: dict[str, str] = Field(default_factory=dict)


class RoastedReview(_ReviewBase):
    """A roasted coffee review (espresso included: it has ``with_milk``)."""

    dataset: Literal["roasted"] = "roasted"
    # True when the title says the coffee was sold green (1997-1999 reviews).
    sold_green: bool = Field(
        description=(
            "True when the title says the coffee was sold green (1997–1999 reviews)."
        )
    )

    roaster_location: str | None = Field(
        None,
        description='Where the roaster is, as written ("Toronto, Ontario, Canada").',
    )
    roast_level: str | None = Field(
        None, description="Roast level as written (Light … Very Dark, or NA)."
    )
    agtron: str | None = Field(
        None,
        description=(
            'Agtron roast-colour readings as written: whole bean / ground ("55/68").'
        ),
    )
    aroma: str | None = Field(None, description="Aroma score as written (1–10).")
    acidity: str | None = Field(
        None,
        description=(
            'Acidity score as written (1–10; "Acidity/Structure" on newer pages).'
        ),
    )
    body: str | None = Field(None, description="Body score as written (1–10).")
    flavor: str | None = Field(None, description="Flavor score as written (1–10).")
    aftertaste: str | None = Field(
        None, description="Aftertaste score as written (1–10)."
    )
    with_milk: str | None = Field(
        None, description="Espresso only: the score in milk, as written (1–10)."
    )

    # The dataset's text columns, in table order. A test checks this matches
    # the fields above, so the order is written down exactly once.
    columns: ClassVar[tuple[str, ...]] = (
        "title", "roaster", "roaster_url", "rating", "review_date",
        "roaster_location", "coffee_origin", "roast_level", "agtron", "price",
        "aroma", "acidity", "body", "flavor", "aftertaste", "with_milk",
        "blind_assessment", "notes", "bottom_line",
    )  # fmt: skip


class GreenReview(_ReviewBase):
    """A green (unroasted) coffee review: the site's green-review layout."""

    dataset: Literal["green"] = "green"

    harvested: str | None = Field(
        None, description="When the green coffee was harvested, as written."
    )
    seller: str | None = Field(None, description="Who sells the green coffee.")
    variety: str | None = Field(
        None, description='The coffee variety (cultivar): "Gesha", "SL34".'
    )
    lot_size: str | None = Field(
        None, description="How much of the lot is for sale, as written."
    )
    processing_method: str | None = Field(
        None, description='How the coffee fruit was processed: "Natural", "Washed".'
    )
    lot_id: str | None = Field(None, description="The seller's lot number.")

    columns: ClassVar[tuple[str, ...]] = (
        "title", "roaster", "roaster_url", "rating", "review_date",
        "coffee_origin", "price", "harvested", "seller", "variety", "lot_size",
        "processing_method", "lot_id", "blind_assessment", "notes", "bottom_line",
    )  # fmt: skip


# Either kind of review; ``dataset`` says which.
type ParsedReview = Annotated[
    RoastedReview | GreenReview, Field(discriminator="dataset")
]


# no_review_template: the page isn't a review page; duplicate_label: a label
# appeared twice (the first value is kept); missing_required: no title, date or
# rating; page_missing: the manifest says a copy was saved but it can't be
# read; parse_error: the parser itself failed on the page (a bug to fix).
type IssueKind = Literal[
    "no_review_template",
    "duplicate_label",
    "missing_required",
    "page_missing",
    "parse_error",
]


class ParseIssue(FrozenModel):
    """Something about one page worth a human look. Never stops a run."""

    slug: str = Field(
        description="The page's slug (as found, even if it isn't a valid one)."
    )
    kind: IssueKind = Field(
        description=(
            "What kind of problem: no_review_template, duplicate_label, "
            "missing_required, page_missing or parse_error."
        )
    )
    detail: str = Field(description="A short note saying what was found.")


class ParsedPage(FrozenModel):
    """The result of parsing one page.

    Attributes:
        record: The parsed review, or None when there was nothing to parse.
        issues: Anything worth a look, found along the way.
    """

    record: ParsedReview | None
    issues: list[ParseIssue] = Field(default_factory=list)
