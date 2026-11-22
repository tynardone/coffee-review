"""The parse stage's contracts: one record per review page, and the issues
found along the way."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from coffee_review.models.common import FrozenModel, Slug

# --- Parsed records (M2) -----------------------------------------------------
#
# Every value is the text as it appears on the page, cleaned of tags and extra
# whitespace; M3 turns it into numbers, dates and so on. A field that isn't on
# the page is None, never a guess.
#
# Roasted and green reviews are different models, so a record can only hold
# the fields its kind of review has: a green review has no aroma, a roasted one
# no lot ID. Unknown fields are refused (extra="forbid"), not silently dropped.

type Dataset = Literal["roasted", "green"]


class _ReviewBase(BaseModel):
    """What every review page has, roasted or green."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: Slug
    url: str
    delisted: bool

    title: str | None = None
    roaster: str | None = None
    roaster_url: str | None = None
    rating: str | None = None
    review_date: str | None = None
    coffee_origin: str | None = None
    price: str | None = None
    blind_assessment: str | None = None
    notes: str | None = None
    bottom_line: str | None = None

    # Labels and headings the alias table doesn't know, or that don't belong to
    # this kind of review, kept as found.
    extra: dict[str, str] = Field(default_factory=dict)


class RoastedReview(_ReviewBase):
    """A roasted coffee review (espresso included: it has ``with_milk``)."""

    dataset: Literal["roasted"] = "roasted"
    # True when the title says the coffee was sold green (1997-1999 reviews).
    sold_green: bool

    roaster_location: str | None = None
    roast_level: str | None = None
    agtron: str | None = None
    aroma: str | None = None
    acidity: str | None = None
    body: str | None = None
    flavor: str | None = None
    aftertaste: str | None = None
    with_milk: str | None = None

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

    harvested: str | None = None
    seller: str | None = None
    variety: str | None = None
    lot_size: str | None = None
    processing_method: str | None = None
    lot_id: str | None = None

    columns: ClassVar[tuple[str, ...]] = (
        "title", "roaster", "roaster_url", "rating", "review_date",
        "coffee_origin", "price", "harvested", "seller", "variety", "lot_size",
        "processing_method", "lot_id", "blind_assessment", "notes", "bottom_line",
    )  # fmt: skip


# Either kind of review; ``dataset`` says which.
type ParsedReview = Annotated[
    RoastedReview | GreenReview, Field(discriminator="dataset")
]


type IssueKind = Literal[
    "no_review_template", "duplicate_label", "missing_required", "page_missing"
]


class ParseIssue(FrozenModel):
    """Something about one page worth a human look. Never stops a run."""

    slug: str
    kind: IssueKind
    detail: str


class ParsedPage(FrozenModel):
    """The result of parsing one page: a record (unless there was nothing to
    parse) and any issues found along the way."""

    record: ParsedReview | None
    issues: list[ParseIssue] = Field(default_factory=list)
