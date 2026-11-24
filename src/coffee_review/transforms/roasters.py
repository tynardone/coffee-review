"""One name per roaster: spellings of the same name become one.

The same roaster is often written several ways (``JBC Coffee Roasters`` and
``JBC``, ``El Gran Cafe`` and ``El Gran Café``). Two layers merge them:

- **The matching key** ignores what never tells roasters apart: case, accents,
  punctuation, ``&``, and filler words like *Coffee Roasters*. Spellings that
  share a key merge (rule ``spelling_variant``), unless their roasters are in
  different countries: then they're listed for review.
- **Close spellings** between keys (``La Mill`` and ``Lamill``) are scored with
  rapidfuzz's ``fuzz.ratio``, taken as 0–1 (like ``difflib``'s ratio). Very
  close ones merge (``close_spelling``) unless the roasters' locations
  conflict; doubtful ones are listed for a person (``possible_match``).

A person's decisions are kept in two lookups: ``roasters.csv`` (this spelling
is that roaster, ``listed_name``) and ``distinct_roasters.csv`` (these two are
different roasters). The kept spelling of a group is the listed name, else the
one with the most reviews.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from itertools import product
from typing import TYPE_CHECKING, Literal

import polars as pl
from rapidfuzz import fuzz, process

from coffee_review.transforms.rules import raw, rule_column

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from coffee_review.models.clean import CleanRule

FILLER_WORDS = frozenset(
    {
        "coffee",
        "coffees",
        "roaster",
        "roasters",
        "roasting",
        "roastery",
        "co",
        "company",
        "inc",
        "llc",
        "ltd",
        "the",
        "cafe",
        "caffe",
        "espresso",
        "and",
    }
)

# Keys shorter than this are never compared by close spelling: one letter's
# difference scores high on a short name ("jbc" and "jbd").
MIN_COMPARED_LENGTH = 4
# rapidfuzz scores 0–100; ours are 0–1, rounded so that a score of exactly a
# threshold counts (rapidfuzz's own 0–1 cutoffs drop 0.8 at a cutoff of 0.8).
SCORE_PLACES = 9

_NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def _words(text: str) -> list[str]:
    """Lower-case ASCII words: accents dropped, ``&`` as ``and``, ``'`` removed."""
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    lower = ascii_text.lower().replace("&", " and ").replace("'", "")
    return _NOT_ALPHANUMERIC.sub(" ", lower).split()


def roaster_key(name: str) -> str:
    """A roaster name in a form that ignores what never tells roasters apart.

    ``PT's Coffee Roasting Co.`` and ``PTs Coffee`` both become ``pts``. A name
    made only of filler words keeps them all: ``The Coffee Company`` stays
    ``the coffee company``.
    """
    words = _words(name)
    kept = [word for word in words if word not in FILLER_WORDS]
    return " ".join(kept or words)


def normalise_city(city: str) -> str:
    """A city name that ignores case, accents and the word *city*.

    ``Taoyuan City`` and ``taoyuan`` are the same city.
    """
    return " ".join(word for word in _words(city) if word != "city")


# --- The name map --------------------------------------------------------------


@dataclass(frozen=True)
class Thresholds:
    """How alike two keys must be (``fuzz.ratio`` as 0–1) for each outcome.

    Attributes:
        merge_at: Merged automatically, unless the roasters' countries conflict
            (then listed for review).
        review_at: Listed for review, unless the countries conflict.
        review_same_city_at: Listed for review only when both roasters are in
            the same city.
    """

    merge_at: float = 0.90
    review_at: float = 0.85
    review_same_city_at: float = 0.80


@dataclass(frozen=True)
class Spelling:
    """One roaster name as written, with what the reviews say about it.

    Attributes:
        name: The name as written.
        reviews: How many reviews use it.
        places: Its roasters' ``(normalise_city(city), country)`` pairs; the
            city is ``""`` when unknown. Empty when the location is unknown
            (green reviews have none).
    """

    name: str
    reviews: int
    places: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True)
class NameDecision:
    """What one spelling becomes.

    Attributes:
        name: The roaster name it becomes (itself, for a kept spelling).
        rules: The rules that explain the change; empty for a kept spelling.
        candidates: For ``possible_match``: the names it might be.
    """

    name: str
    rules: tuple[CleanRule, ...]
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class NameMap:
    """Every spelling's decision, and the hand-kept rows that matched nothing.

    Attributes:
        decisions: Keyed by the spelling as written.
        unused_rows: Spellings in ``roasters.csv`` or ``distinct_roasters.csv``
            that no review uses (sorted).
    """

    decisions: Mapping[str, NameDecision]
    unused_rows: tuple[str, ...]


def build_name_map(
    spellings: Sequence[Spelling],
    listed: Mapping[str, str],
    distinct: Sequence[tuple[str, str]],
    thresholds: Thresholds = Thresholds(),
) -> NameMap:
    """Decide which roaster each spelling is.

    Spellings start alone and are merged in three steps: those sharing a key,
    then the ``roasters.csv`` rows, then close spellings. Each step may also
    leave pairs for a person to review.

    Args:
        spellings: Every distinct spelling, with review counts and places.
        listed: ``roasters.csv``: spelling → the roaster it is.
        distinct: ``distinct_roasters.csv``: pairs that are different roasters.
        thresholds: The scores for merging and for review.

    Returns:
        A decision for every spelling with a name, and the unused rows.
    """
    named = [s for s in spellings if s.name.strip()]
    by_key: dict[str, list[Spelling]] = {}
    for spelling in sorted(named, key=_by_reviews_then_name):
        by_key.setdefault(roaster_key(spelling.name), []).append(spelling)
    apart = {frozenset(map(roaster_key, pair)) for pair in distinct}

    groups = _Groups(named)
    reviews = _merge_shared_keys(groups, by_key)
    _merge_listed(groups, by_key, listed)
    reviews += _merge_close_spellings(groups, by_key, apart, thresholds)

    listed_keys = {roaster_key(name) for row in listed.items() for name in row}
    decisions = {s.name: _decide(s, groups, listed_keys) for s in named}
    for carrier, candidates in _open_reviews(groups, reviews, apart).items():
        decision = decisions[carrier]
        decisions[carrier] = NameDecision(
            decision.name,
            (*decision.rules, "possible_match"),
            tuple(sorted(candidates)),
        )

    rows = [*listed, *(name for pair in distinct for name in pair)]
    unused = sorted({row for row in rows if roaster_key(row) not in by_key})
    return NameMap(decisions, tuple(unused))


def _by_reviews_then_name(spelling: Spelling) -> tuple[int, str]:
    """Sort key: most reviews first, then A–Z (the spelling a group keeps)."""
    return (-spelling.reviews, spelling.name)


class _Groups:
    """Which spellings are one roaster so far: a union-find over spelling names.

    Each group is known by its root spelling and answers questions about all
    of its members: their countries, cities, reviews and kept name. Which
    spelling is the root doesn't matter: the kept name is worked out from the
    members (or the ``roasters.csv`` name), never from the root.
    """

    def __init__(self, spellings: Sequence[Spelling]) -> None:
        self._parent = {s.name: s.name for s in spellings}
        self._members = {s.name: [s] for s in spellings}
        self._listed_as: dict[str, str] = {}  # root → the name roasters.csv gave

    def root(self, name: str) -> str:
        parent = self._parent
        while parent[name] != name:
            parent[name] = parent[parent[name]]  # path compression
            name = parent[name]
        return name

    def same(self, a: str, b: str) -> bool:
        return self.root(a) == self.root(b)

    def merge(self, a: str, b: str) -> None:
        a, b = self.root(a), self.root(b)
        if a == b:
            return
        self._parent[b] = a
        self._members[a] += self._members.pop(b)
        if b in self._listed_as:
            self._listed_as.setdefault(a, self._listed_as.pop(b))

    def name_as(self, name: str, listed_name: str) -> None:
        self._listed_as[self.root(name)] = listed_name

    def members(self, name: str) -> list[Spelling]:
        return self._members[self.root(name)]

    def listed_as(self, name: str) -> str | None:
        return self._listed_as.get(self.root(name))

    def lead(self, name: str) -> Spelling:
        """The member with the most reviews (ties: first alphabetically)."""
        return min(self.members(name), key=_by_reviews_then_name)

    def kept_name(self, name: str) -> str:
        return self.listed_as(name) or self.lead(name).name

    def reviews(self, name: str) -> int:
        return sum(s.reviews for s in self.members(name))

    def keys(self, name: str) -> set[str]:
        return {roaster_key(s.name) for s in self.members(name)}

    def countries(self, name: str) -> set[str]:
        return {country for s in self.members(name) for _, country in s.places}

    def cities(self, name: str) -> set[tuple[str, str]]:
        return {place for s in self.members(name) for place in s.places if place[0]}

    def conflict(self, a: str, b: str) -> bool:
        """Both groups have known countries, and they share none."""
        first, second = self.countries(a), self.countries(b)
        return bool(first and second and not first & second)


type _Review = tuple[str, str]  # two spellings a person should look at


def _merge_shared_keys(
    groups: _Groups, by_key: Mapping[str, list[Spelling]]
) -> list[_Review]:
    """Merge spellings that share a key, unless their countries conflict.

    Spellings are taken most-reviewed first, each joining the first group of
    its key it doesn't conflict with (one with no country joins the first).
    A key left in more than one group is listed for review.
    """
    reviews: list[_Review] = []
    for same_key in by_key.values():
        heads: list[str] = []  # one spelling per group of this key
        for spelling in same_key:
            head = next(
                (h for h in heads if not groups.conflict(h, spelling.name)), None
            )
            if head is None:
                heads.append(spelling.name)
            else:
                groups.merge(head, spelling.name)
        reviews += [(head, heads[0]) for head in heads[1:]]
    return reviews


def _merge_listed(
    groups: _Groups, by_key: Mapping[str, list[Spelling]], listed: Mapping[str, str]
) -> None:
    """Merge what each ``roasters.csv`` row covers, whatever the countries.

    A row covers the spellings of its own key and of the roaster's key, so
    ``BB Roasters,Bold Bean`` also takes in ``Bold Bean``.
    """
    for spelling, name in listed.items():
        keys = {roaster_key(spelling), roaster_key(name)}
        covered = [s.name for key in keys for s in by_key.get(key, [])]
        for other in covered:
            groups.merge(covered[0], other)
        if covered:
            groups.name_as(covered[0], name)


def _merge_close_spellings(
    groups: _Groups,
    by_key: Mapping[str, list[Spelling]],
    apart: set[frozenset[str]],
    thresholds: Thresholds,
) -> list[_Review]:
    """Merge close spellings that may merge; return the ones to review.

    Pairs are taken best first, and the country check is made against each
    side's whole group as it is by then, so a chain of merges can't join two
    countries.
    """
    reviews: list[_Review] = []
    for score, a, b in _close_pairs(by_key, thresholds.review_same_city_at):
        if groups.same(a, b) or _kept_apart(groups, a, b, apart):
            continue
        conflict = groups.conflict(a, b)
        same_city = bool(groups.cities(a) & groups.cities(b))
        match _outcome(score, thresholds, conflict=conflict, same_city=same_city):
            case "merge":
                groups.merge(a, b)
            case "review":
                reviews.append((a, b))
    return reviews


def _outcome(
    score: float, thresholds: Thresholds, *, conflict: bool, same_city: bool
) -> Literal["merge", "review"] | None:
    """What a close spelling's score and the two roasters' locations call for."""
    if score >= thresholds.merge_at:
        return "review" if conflict else "merge"
    if conflict:
        return None
    if score >= thresholds.review_at:
        return "review"
    if score >= thresholds.review_same_city_at and same_city:
        return "review"
    return None


def _close_pairs(
    by_key: Mapping[str, list[Spelling]], floor: float
) -> list[tuple[float, str, str]]:
    """Every pair of spellings whose keys score at least ``floor``, best first."""
    pairs = [
        (score, first.name, second.name)
        for score, key, other in _similar_keys(list(by_key), floor)
        for first, second in product(by_key[key], by_key[other])
    ]
    return sorted(pairs, key=lambda pair: (-pair[0], pair[1], pair[2]))


def _similar_keys(keys: list[str], floor: float) -> Iterator[tuple[float, str, str]]:
    """Each pair of keys scoring at least ``floor``, as (score, key, other).

    Keys shorter than ``MIN_COMPARED_LENGTH`` aren't compared.
    """
    compared = sorted(key for key in keys if len(key) >= MIN_COMPARED_LENGTH)
    for i, key in enumerate(compared):
        later = compared[i + 1 :]
        # fuzz.ratio is 0–100; the cutoff sits just below, so the rounded score
        # decides, and a score of exactly a threshold counts.
        cutoff = floor * 100 - 10**-SCORE_PLACES
        for other, score, _ in process.extract(
            key, later, scorer=fuzz.ratio, score_cutoff=cutoff, limit=None
        ):
            yield round(score / 100, SCORE_PLACES), key, other


def _kept_apart(groups: _Groups, a: str, b: str, apart: set[frozenset[str]]) -> bool:
    """True when a person said these are different roasters.

    A ``distinct_roasters.csv`` row covers the pair, or ``roasters.csv`` gave
    them different names.
    """
    first, second = groups.listed_as(a), groups.listed_as(b)
    if first and second and first != second:
        return True
    return any(
        frozenset(keys) in apart for keys in product(groups.keys(a), groups.keys(b))
    )


def _decide(spelling: Spelling, groups: _Groups, listed_keys: set[str]) -> NameDecision:
    """The name a spelling becomes, and the rule that explains it."""
    name = groups.kept_name(spelling.name)
    key = roaster_key(spelling.name)
    if spelling.name == name:
        return NameDecision(name, ())
    if key in listed_keys:
        return NameDecision(name, ("listed_name",))
    if key == roaster_key(groups.lead(spelling.name).name):
        return NameDecision(name, ("spelling_variant",))
    return NameDecision(name, ("close_spelling",))


def _open_reviews(
    groups: _Groups, reviews: Iterable[_Review], apart: set[frozenset[str]]
) -> dict[str, set[str]]:
    """The pairs still apart at the end: carrier spelling → the names it may be.

    Of the two groups, the one that would *not* keep its name if merged carries
    the item (fewer reviews; tie: later A–Z), on its kept spelling, or on its
    most-reviewed one when its kept name isn't a spelling in the data.
    """
    result: dict[str, set[str]] = {}
    for a, b in reviews:
        if groups.same(a, b) or _kept_apart(groups, a, b, apart):
            continue
        kept, other = sorted(
            (a, b), key=lambda s: (-groups.reviews(s), groups.kept_name(s))
        )
        written = {s.name for s in groups.members(other)}
        name = groups.kept_name(other)
        carrier = name if name in written else groups.lead(other).name
        result.setdefault(carrier, set()).add(groups.kept_name(kept))
    return result


# --- From the tables, and back ---------------------------------------------------


def roaster_spellings(*frames: pl.DataFrame) -> list[Spelling]:
    """Every distinct roaster spelling in the frames, with reviews and places.

    Reads ``raw_roaster``, and ``roaster_city`` and ``roaster_country`` where a
    frame has them (green reviews don't). Blank names are skipped.
    """
    rows = pl.concat([_spelling_columns(frame) for frame in frames]).filter(
        pl.col("raw_roaster").str.strip_chars() != ""
    )
    return [
        Spelling(name, group.height, _places(group))
        for (name,), group in rows.group_by("raw_roaster", maintain_order=True)
    ]


_SPELLING_COLUMNS = ("raw_roaster", "roaster_city", "roaster_country")


def _spelling_columns(frame: pl.DataFrame) -> pl.DataFrame:
    """The spelling and location columns, null where a frame has none."""
    return frame.select(
        pl.col(name) if name in frame.columns else pl.lit(None, pl.String).alias(name)
        for name in _SPELLING_COLUMNS
    )


def _places(rows: pl.DataFrame) -> frozenset[tuple[str, str]]:
    """The ``(city, country)`` pairs of one spelling's reviews, country known."""
    located = rows.filter(pl.col("roaster_country").is_not_null())
    return frozenset(
        (normalise_city(city or ""), country)
        for city, country in located.select(
            "roaster_city", "roaster_country"
        ).iter_rows()
    )


def clean_roaster(frame: pl.LazyFrame, names: NameMap) -> pl.LazyFrame:
    """``raw_roaster`` to one name per roaster, with the rule that explains it.

    A spelling the map doesn't know (or a blank one) is kept as written.
    """
    decisions = pl.LazyFrame(
        {
            "_spelling": list(names.decisions),
            "_name": [d.name for d in names.decisions.values()],
            "_rules": [list(d.rules) for d in names.decisions.values()],
        },
        schema={
            "_spelling": pl.String,
            "_name": pl.String,
            "_rules": pl.List(pl.String),
        },
    )
    return (
        frame.join(
            decisions,
            left_on=raw("roaster"),
            right_on="_spelling",
            how="left",
            maintain_order="left",
        )
        .with_columns(
            pl.coalesce("_name", raw("roaster")).alias("roaster"),
            pl.col("_rules").fill_null([]).alias(rule_column("roaster")),
        )
        .drop("_name", "_rules")
    )
