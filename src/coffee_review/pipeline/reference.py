"""The reference stages: exchange rates (``fx``) and CPI (``cpi``).

Each stage works the same way:

1. Parse every saved response once. A saved file that won't parse is treated
   as missing, so it's fetched again and replaced.
2. Work out what's missing for the configured source (``transforms/reference``)
   and fetch only that. Each answer is saved before it's used.
3. Rebuild the tidy tables in full from every saved response, check them
   against their contracts, and write them.

A source is an entry in ``FX_SOURCES`` or ``CPI_SOURCES``: a client, the
settings it's built from, and a parser. Swapping one is a new entry and a
setting (``COFFEE_REVIEW_REFERENCE__FX_SOURCE``); nothing else changes.

``run_fx`` and ``run_cpi`` take their client and stores as arguments, so tests
hand them fakes; ``fx`` and ``cpi`` are the composition roots.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import PurePath
from typing import TYPE_CHECKING, Literal

from coffee_review.clients.bls_api import BlsApiClient
from coffee_review.clients.forex_api import ForexApiClient
from coffee_review.clients.reference import (
    SourceAuthError,
    SourceRefusedError,
    SourceUnavailableError,
)
from coffee_review.models.reference import (
    CpiResponse,
    FxResponse,
    ReferenceParseError,
    validate_reference,
)
from coffee_review.parsers.cpi_bls import parse_cpi_bls
from coffee_review.parsers.fx_openexchangerates import parse_fx_openexchangerates
from coffee_review.storage.reference_store import (
    open_reference_raw_store,
    open_reference_store,
)
from coffee_review.transforms.reference import (
    build_cpi,
    build_fx,
    cpi_gap_months,
    cpi_stored_months,
    cpi_wanted_months,
    fx_missing,
    fx_wanted_days,
    plan_cpi,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import polars as pl

    from coffee_review.clients.reference import CpiClient, FxClient
    from coffee_review.config import Settings
    from coffee_review.models.reference import CpiParsed, FxParsed
    from coffee_review.storage.reference_store import (
        ReferenceRawStore,
        ReferenceStore,
    )

logger = logging.getLogger(__name__)

# The source didn't answer: stop cleanly, keep what's saved.
STOPPING_ERRORS = (SourceAuthError, SourceRefusedError, SourceUnavailableError)


@dataclass(frozen=True)
class FxSource:
    """An exchange-rate source: how to build its client, and its parser."""

    client: Callable[[Settings], FxClient]
    parse: Callable[[str, date], FxParsed]


FX_SOURCES: Mapping[str, FxSource] = {
    "openexchangerates": FxSource(
        client=lambda settings: ForexApiClient(settings.forex_api),
        parse=parse_fx_openexchangerates,
    ),
}


@dataclass(frozen=True)
class CpiSource:
    """A CPI source: its client, its parser, and how many years one request
    may cover."""

    client: Callable[[Settings], CpiClient]
    parse: Callable[[str, str], CpiParsed]
    max_years: int


CPI_SOURCES: Mapping[str, CpiSource] = {
    # Keyless BLS: 10 years per request, 25 requests a day.
    "bls": CpiSource(
        client=lambda settings: BlsApiClient(settings.bls_api),
        parse=parse_cpi_bls,
        max_years=10,
    ),
}

# How many gap periods the summary line names before "…".
GAPS_SHOWN = 5


class UnknownSourceError(Exception):
    """A setting names a source the registry doesn't have."""


@dataclass(frozen=True)
class ReferenceRunResult:
    """What one fx or cpi run did, and what the table holds afterwards."""

    kind: Literal["fx", "cpi"]
    outcome: Literal["finished", "stopped"]
    reason: str | None
    requests: int
    fetched: int
    already: int
    failed: int
    gaps: int
    dropped: int
    rows: int
    first: date | None
    last: date | None
    location: str
    gap_periods: tuple[str, ...] = ()

    def describe(self) -> str:
        """One summary line for the terminal and the run log."""
        parts = [
            f"{self.fetched} fetched",
            f"{self.already:,} already stored",
            f"{self.failed} failed",
        ]
        if self.gaps:
            gaps = _count(self.gaps, "gap")
            if self.gap_periods:
                shown = ", ".join(self.gap_periods[:GAPS_SHOWN])
                more = ", …" if len(self.gap_periods) > GAPS_SHOWN else ""
                gaps += f" ({shown}{more})"
            parts.append(gaps)
        if self.dropped:
            parts.append(_count(self.dropped, "value") + " dropped")
        line = f"{self.kind} {self.outcome}: " + ", ".join(parts)
        if self.outcome == "stopped":
            line += f" (stopped: {self.reason})"
        table = f"{PurePath(self.location).name} {self.rows:,} rows"
        if self.first is not None and self.last is not None:
            table += f", {self.first} to {self.last}"
        return f"{line} · {table}"


def fx(settings: Settings, *, today: date | None = None) -> ReferenceRunResult:
    """Bring the exchange-rate tables up to date with the configured source.

    The source is looked up and its client built (which needs the API key)
    before any store is opened or request sent.
    """
    name = settings.reference.fx_source
    source = _lookup(FX_SOURCES, name, "fx")
    client = source.client(settings)
    raw = open_reference_raw_store(settings)
    try:
        store = open_reference_store(settings)
        try:
            return asyncio.run(
                run_fx(
                    source_name=name,
                    sources=FX_SOURCES,
                    client=client,
                    raw=raw,
                    store=store,
                    start=settings.reference.fx_start,
                    today=today or datetime.now(UTC).date(),
                )
            )
        finally:
            store.close()
    finally:
        raw.close()


async def run_fx(
    *,
    source_name: str,
    sources: Mapping[str, FxSource],
    client: FxClient,
    raw: ReferenceRawStore,
    store: ReferenceStore,
    start: date,
    today: date,
) -> ReferenceRunResult:
    """Fetch the missing days from ``source_name``, then rebuild the FX tables.

    ``sources`` is the registry: every saved response is parsed with its own
    source's parser, so the history of a swapped-out source stays readable.
    """
    source = _lookup(sources, source_name, "fx")
    parsed: dict[FxResponse, FxParsed] = {}
    for response in raw.list_fx():
        fx_parsed = _parse_saved_fx(raw, response, sources)
        if fx_parsed is not None:
            parsed[response] = fx_parsed

    wanted = fx_wanted_days(start, today)
    have = {r.day for r in parsed if r.source == source_name}
    missing = fx_missing(wanted, have)
    requests = fetched = failed = 0
    outcome: Literal["finished", "stopped"] = "finished"
    reason: str | None = None

    async with client:
        for day in missing:
            requests += 1
            try:
                text = await client.fetch_day(day)
            except STOPPING_ERRORS as exc:
                outcome, reason = "stopped", str(exc)
                logger.warning("fx stopped at %s: %s", day, exc)
                break
            try:
                fx_parsed = source.parse(text, day)
            except ReferenceParseError as exc:
                failed += 1
                logger.warning("fx %s: not saved, asked again next run: %s", day, exc)
                continue
            response = FxResponse(source=source_name, day=day)
            raw.save_fx(response, text)
            parsed[response] = fx_parsed
            fetched += 1

    fx_table, gaps = build_fx(
        [(r.source, p) for r, p in parsed.items()], preferred=source_name
    )
    _write(store, {"fx": fx_table, "fx_gaps": gaps})
    dropped = sum(len(p.problems) for p in parsed.values())
    days = fx_table["day"]
    result = ReferenceRunResult(
        kind="fx",
        outcome=outcome,
        reason=reason,
        requests=requests,
        fetched=fetched,
        already=len(wanted) - len(missing),
        failed=failed,
        gaps=gaps.height,
        dropped=dropped,
        rows=fx_table.height,
        first=days.min() if fx_table.height else None,  # type: ignore[arg-type]
        last=days.max() if fx_table.height else None,  # type: ignore[arg-type]
        location=store.location("fx"),
        gap_periods=tuple(f"{d:%Y-%m-%d}" for d in gaps["day"]),
    )
    _log(result)
    return result


def cpi(settings: Settings, *, now: datetime | None = None) -> ReferenceRunResult:
    """Bring the CPI tables up to date with the configured source (no key needed)."""
    name = settings.reference.cpi_source
    source = _lookup(CPI_SOURCES, name, "cpi")
    client = source.client(settings)
    raw = open_reference_raw_store(settings)
    try:
        store = open_reference_store(settings)
        try:
            return asyncio.run(
                run_cpi(
                    source_name=name,
                    sources=CPI_SOURCES,
                    client=client,
                    raw=raw,
                    store=store,
                    series=settings.reference.cpi_series,
                    start=settings.reference.cpi_start,
                    now=now or datetime.now(UTC),
                )
            )
        finally:
            store.close()
    finally:
        raw.close()


async def run_cpi(
    *,
    source_name: str,
    sources: Mapping[str, CpiSource],
    client: CpiClient,
    raw: ReferenceRawStore,
    store: ReferenceStore,
    series: str,
    start: date,
    now: datetime,
) -> ReferenceRunResult:
    """Fetch the missing years of ``series``, then rebuild the CPI tables.

    ``now`` (UTC) names each saved response, and its date is "today" for the
    once-a-day rule.
    """
    now = now.astimezone(UTC)
    today = now.date()
    source = _lookup(sources, source_name, "cpi")
    saved: list[tuple[CpiResponse, CpiParsed]] = []
    for response in raw.list_cpi():
        if response.series != series:
            continue
        cpi_parsed = _parse_saved_cpi(raw, response, sources)
        if cpi_parsed is not None:
            saved.append((response, cpi_parsed))

    own = [s for s in saved if s[0].source == source_name]
    wanted = cpi_wanted_months(start, today)
    known = cpi_stored_months(own) | cpi_gap_months(own)
    already = sum(1 for month in wanted if month in known)
    requests = fetched = failed = 0
    outcome: Literal["finished", "stopped"] = "finished"
    reason: str | None = None

    async with client:
        for first_year, last_year in plan_cpi(wanted, own, today, source.max_years):
            years = f"{first_year}-{last_year}"
            requests += 1
            try:
                text = await client.fetch_years(series, first_year, last_year)
            except STOPPING_ERRORS as exc:
                outcome, reason = "stopped", str(exc)
                logger.warning("cpi stopped at %s: %s", years, exc)
                break
            try:
                cpi_parsed = source.parse(text, series)
            except ReferenceParseError as exc:
                failed += 1
                logger.warning(
                    "cpi %s: not saved, asked again next run: %s", years, exc
                )
                continue
            response = CpiResponse(
                source=source_name,
                series=series,
                fetched_at=now,
                start_year=first_year,
                end_year=last_year,
            )
            raw.save_cpi(response, text)
            saved.append((response, cpi_parsed))
            fetched += 1

    cpi_table, gaps = build_cpi(saved, preferred=source_name)
    _write(store, {"cpi": cpi_table, "cpi_gaps": gaps})
    months = cpi_table["month"]
    result = ReferenceRunResult(
        kind="cpi",
        outcome=outcome,
        reason=reason,
        requests=requests,
        fetched=fetched,
        already=already,
        failed=failed,
        gaps=gaps.height,
        dropped=sum(len(p.problems) for _, p in saved),
        rows=cpi_table.height,
        first=months.min() if cpi_table.height else None,  # type: ignore[arg-type]
        last=months.max() if cpi_table.height else None,  # type: ignore[arg-type]
        location=store.location("cpi"),
        gap_periods=tuple(f"{m:%Y-%m}" for m in gaps["month"]),
    )
    _log(result)
    return result


def _parse_saved_cpi(
    raw: ReferenceRawStore, response: CpiResponse, sources: Mapping[str, CpiSource]
) -> CpiParsed | None:
    """A saved response parsed, or None (with a warning) if it won't parse."""
    source = sources.get(response.source)
    if source is None:
        logger.warning("cpi: no parser for saved source %r; skipped", response.source)
        return None
    try:
        return source.parse(raw.read_cpi(response), response.series)
    except ReferenceParseError as exc:
        logger.warning(
            "cpi %s %s-%s (%s): saved file won't parse; its years count as missing: %s",
            response.source,
            response.start_year,
            response.end_year,
            response.fetched_at,
            exc,
        )
        return None


def _log(result: ReferenceRunResult) -> None:
    """The summary line: to the terminal and the run log."""
    if result.outcome == "stopped":
        logger.warning(result.describe())
    else:
        logger.info(result.describe())


def _parse_saved_fx(
    raw: ReferenceRawStore, response: FxResponse, sources: Mapping[str, FxSource]
) -> FxParsed | None:
    """A saved response parsed, or None (with a warning) if it won't parse."""
    parse = sources.get(response.source)
    if parse is None:
        logger.warning("fx: no parser for saved source %r; skipped", response.source)
        return None
    try:
        return parse.parse(raw.read_fx(response), response.day)
    except ReferenceParseError as exc:
        logger.warning(
            "fx %s %s: saved file won't parse, fetching again: %s",
            response.source,
            response.day,
            exc,
        )
        return None


def _write(store: ReferenceStore, tables: Mapping[str, pl.DataFrame]) -> None:
    """Check every table, then write them: nothing is written if one fails."""
    for name, frame in tables.items():
        validate_reference(frame, name)  # type: ignore[arg-type]
    for name, frame in tables.items():
        store.write(name, frame)  # type: ignore[arg-type]


def _lookup[S](sources: Mapping[str, S], name: str, kind: str) -> S:
    try:
        return sources[name]
    except KeyError:
        known = ", ".join(sorted(sources))
        msg = f"unknown {kind} source {name!r}; known: {known}"
        raise UnknownSourceError(msg) from None


def _count(n: int, noun: str) -> str:
    return f"{n:,} {noun}" + ("" if n == 1 else "s")
