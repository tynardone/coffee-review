"""One saved review page → one parsed record, plus anything worth a look.

Pure: HTML text in, models out. Every coffeereview.com review since 1997 uses
the same template (``div.review-template``): a score, roaster and title, one or
more label/value tables, then ``h2`` sections. What varies is which labels and
sections a page has and what they're called; ``field_aliases`` knows the names.

Rules:

- The site's ``green-coffee-review`` class decides the dataset.
- Values are cleaned text, never interpreted; blank values are None.
- A label or heading that isn't known, or doesn't belong to this dataset, goes
  into ``extra`` instead of being dropped.
- Problems become ``ParseIssue``s; only a page with no template has no record.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from selectolax.lexbor import LexborHTMLParser

from coffee_review.models.parsed import (
    GreenReview,
    ParsedPage,
    ParseIssue,
    RoastedReview,
)
from coffee_review.parsers.field_aliases import (
    FIELD_DATASETS,
    IGNORED_HEADINGS,
    canonical_heading,
    canonical_label,
    normalise_label,
)

if TYPE_CHECKING:
    from selectolax.lexbor import LexborNode

    from coffee_review.models.parsed import Dataset

GREEN_CLASS = "green-coffee-review"
# "Bourbon – Green", "Boquete Premium - Green by …": a coffee sold green.
SOLD_GREEN_TITLE = re.compile(r"[-–—]\s*Green\b", re.IGNORECASE)
REQUIRED_FIELDS = ("title", "review_date", "rating")


def parse_review_page(slug: str, url: str, html: str, *, delisted: bool) -> ParsedPage:
    """Parse one review page.

    Never raises for a strange page: problems are reported as issues.

    Args:
        slug: The review's id.
        url: The page's address.
        html: The page as saved.
        delisted: Whether the review has left the sitemaps.

    Returns:
        The parsed record (None if the page has no review template) and any
        issues.
    """
    template = LexborHTMLParser(html).css_first("div.review-template")
    if template is None:
        issue = ParseIssue(
            slug=slug, kind="no_review_template", detail="no div.review-template"
        )
        return ParsedPage(record=None, issues=[issue])

    classes = (template.attributes.get("class") or "").split()
    dataset: Dataset = "green" if GREEN_CLASS in classes else "roasted"
    found = _Found(slug=slug, dataset=dataset)
    found.set_fixed("rating", _text(template.css_first("span.review-template-rating")))
    found.set_fixed("roaster", _text(template.css_first("p.review-roaster")))
    found.set_fixed("title", _text(template.css_first("h1.review-title")))
    found.set_fixed("roaster_url", _logo_link(template))

    for row in template.css("table.review-template-table tr"):
        cells = row.css("td")
        if not cells:
            continue
        label = _text(cells[0]) or ""
        value = _text(cells[1]) if len(cells) > 1 else None
        found.place(canonical_label(label), label.removesuffix(":").strip(), value)

    for heading, text in _sections(template):
        found.place(canonical_heading(heading), f"section: {heading}", text)

    return found.page(url=url, delisted=delisted)


class _Found:
    """What one page has been found to contain, as parsing goes along."""

    def __init__(self, *, slug: str, dataset: Dataset) -> None:
        self.slug = slug
        self.dataset = dataset
        self.fields: dict[str, str | None] = {}
        self.extra: dict[str, str] = {}
        self.issues: list[ParseIssue] = []

    def set_fixed(self, field: str, value: str | None) -> None:
        """A field from a fixed part of the template (score, roaster, title)."""
        self.fields[field] = value

    def place(self, field: str | None, extra_key: str, value: str | None) -> None:
        """Put a labelled value in its canonical field, or in ``extra``."""
        belongs = field is not None and self.dataset in FIELD_DATASETS.get(field, ())
        if field is not None and belongs:
            if field in self.fields:
                self.issues.append(
                    ParseIssue(
                        slug=self.slug,
                        kind="duplicate_label",
                        detail=f"{extra_key} (kept the first value)",
                    )
                )
                return
            self.fields[field] = value
        elif extra_key not in self.extra:
            # Kept even when blank, so a new label always shows in the report.
            self.extra[extra_key] = value or ""

    def page(self, *, url: str, delisted: bool) -> ParsedPage:
        for field in REQUIRED_FIELDS:
            if self.fields.get(field) is None:
                self.issues.append(
                    ParseIssue(slug=self.slug, kind="missing_required", detail=field)
                )
        common = {"slug": self.slug, "url": url, "delisted": delisted}
        values = {k: v for k, v in self.fields.items() if v is not None}
        record: RoastedReview | GreenReview
        if self.dataset == "green":
            record = GreenReview(**common, **values, extra=self.extra)
        else:
            title = self.fields.get("title") or ""
            sold_green = SOLD_GREEN_TITLE.search(title) is not None
            record = RoastedReview(
                **common, **values, sold_green=sold_green, extra=self.extra
            )
        return ParsedPage(record=record, issues=self.issues)


def _sections(template: LexborNode) -> list[tuple[str, str | None]]:
    """(heading, text) for each ``h2`` section of the review, in page order.

    The review's sections are the template's direct children after its rows.
    The first heading that belongs to the site around the review (e.g.
    "Explore Similar Coffees") ends the review.
    """
    sections: list[tuple[str, list[str]]] = []
    for child in template.iter(include_text=False):
        if child.tag == "h2":
            heading = _text(child) or ""
            if normalise_label(heading) in IGNORED_HEADINGS:
                break
            sections.append((heading, []))
        elif sections and (text := _text(child)) is not None:
            sections[-1][1].append(text)
    return [(heading, "\n\n".join(parts) or None) for heading, parts in sections]


def _logo_link(template: LexborNode) -> str | None:
    link = template.css_first("div.col-3 a")
    if link is None:
        return None
    href = (link.attributes.get("href") or "").strip()
    return href or None


def _text(node: LexborNode | None) -> str | None:
    """A node's text, cleaned.

    Tags removed, entities decoded and whitespace (non-breaking spaces
    included) collapsed.

    Args:
        node: The node, or None.

    Returns:
        The cleaned text, or None when there is no node or the text is blank.
    """
    if node is None:
        return None
    text = " ".join(_joined_text(node).split())
    return text or None


# Tags that separate words: a line break, or the edge of a block. Any other tag
# (a link, bold text) sits inside a word as often as between words, so it adds
# nothing: caf<a>é</a> is "café", not "caf é".
_BREAK_TAGS = frozenset({"br", "hr"})
_BLOCK_TAGS = frozenset(
    {"p", "div", "li", "ul", "ol", "tr", "td", "th", "table", "blockquote"}
    | {"h1", "h2", "h3", "h4", "h5", "h6"}
)


def _joined_text(node: LexborNode) -> str:
    """The text under a node exactly as written.

    A space is added only where a line break or block edge separates words.
    """
    parts: list[str] = []
    for child in node.iter(include_text=True):
        if child.tag == "-text":
            parts.append(child.text_content or "")
        elif child.tag in _BREAK_TAGS:
            parts.append(" ")
        elif child.tag and not child.tag.startswith("-"):  # not a comment
            edge = " " if child.tag in _BLOCK_TAGS else ""
            parts += [edge, _joined_text(child), edge]
    return "".join(parts)
