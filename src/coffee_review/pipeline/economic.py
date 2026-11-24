"""The economic stages: exchange rates (``fx``) and CPI (``cpi``).

Each stage works the same way:

1. Parse every saved response. A saved file that won't parse is treated as
   missing, so it's fetched again.
2. Work out what's missing (``transforms/economic``) and fetch only that,
   saving each answer before it's used.
3. Rebuild the tidy table in full from the saved responses, check it against
   its contract, and write it.

So a rerun costs no requests, and a parser fix needs none either.
``run_fx`` and ``run_cpi`` take their client and store as arguments, so tests
hand them a client over a fake network and a store in a temp folder; ``fx``
and ``cpi`` are the composition roots.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import PurePath
from typing import TYPE_CHECKING, Literal

from coffee_review.clients.bls_api import BlsApiClient
from coffee_review.clients.economic import (
    SourceAuthError,
    SourceRefusedError,
    SourceUnavailableError,
)
from coffee_review.clients.forex_api import ForexApiClient
from coffee_review.models.economic import (
    CpiFile,
    EconomicParseError,
    validate_economic,
)
from coffee_review.parsers.cpi_bls import parse_cpi_bls
from coffee_review.parsers.fx_openexchangerates import parse_fx_openexchangerates
from coffee_review.storage.economic_store import open_economic_store
from coffee_review.transforms.economic import (
    build_cpi,
    build_fx,
    cpi_wanted_months,
    fx_missing,
    fx_wanted_days,
    plan_cpi,
)

if TYPE_CHECKING:
    import polars as pl

    from coffee_review.config import Settings
    from coffee_review.models.economic import CpiParsed, FxParsed
    from coffee_review.storage.economic_store import EconomicStore

logger = logging.getLogger(__name__)

# The source didn't answer: stop cleanly, keep what's saved.
STOPPING_ERRORS = (SourceAuthError, SourceRefusedError, SourceUnavailableError)
# Answers in a row that won't parse before a run stops: a systematic change
# (a new format, a non-USD base) shouldn't spend a month's quota.
MAX_UNPARSABLE_IN_A_ROW = 3
# Keyless BLS: at most 10 years per request.
BLS_MAX_YEARS = 10
# How many gap periods the summary line names before "…".
GAPS_SHOWN = 5

type Outcome = Literal["finished", "stopped"]


@dataclass(frozen=True)
class EconomicRunResult:
    """What one fx or cpi run did, and what the table holds afterwards."""

    kind: Literal["fx", "cpi"]
    outcome: Outcome
    reason: str | None
    fetched: int
    already: int
    failed: int
    gaps: tuple[str, ...]
    rows: int
    first: date | None
    last: date | None
    location: str

    def describe(self) -> str:
        """One summary line for the terminal and the run log."""
        parts = [
            f"{self.fetched} fetched",
            f"{self.already:,} already stored",
            f"{self.failed} failed",
        ]
        if self.gaps:
            shown = ", ".join(self.gaps[:GAPS_SHOWN])
            more = ", …" if len(self.gaps) > GAPS_SHOWN else ""
            noun = "gap" if len(self.gaps) == 1 else "gaps"
            parts.append(f"{len(self.gaps)} {noun} ({shown}{more})")
        line = f"{self.kind} {self.outcome}: " + ", ".join(parts)
        if self.outcome == "stopped":
            line += f" (stopped: {self.reason})"
        table = f"{PurePath(self.location).name} {self.rows:,} rows"
        if self.first is not None and self.last is not None:
            table += f", {self.first} to {self.last}"
        return f"{line} · {table}"


@dataclass
class _Fetching:
    """The running tally of one stage's fetch loop."""

    kind: str
    fetched: int = 0
    failed: int = 0
    unparsable: int = 0
    outcome: Outcome = "finished"
    reason: str | None = None

    def stop(self, reason: str) -> None:
        self.outcome, self.reason = "stopped", reason
        logger.warning("%s stopped: %s", self.kind, reason)

    def unparsable_answer(self, what: str, exc: EconomicParseError) -> bool:
        """Count an answer that won't parse; True when the run should stop."""
        self.failed += 1
        self.unparsable += 1
        logger.warning(
            "%s %s: not saved, asked again next run: %s", self.kind, what, exc
        )
        if self.unparsable == MAX_UNPARSABLE_IN_A_ROW:
            self.stop(f"{self.unparsable} answers in a row wouldn't parse; last: {exc}")
            return True
        return False

    def saved(self) -> None:
        self.fetched += 1
        self.unparsable = 0


# --- Exchange rates ----------------------------------------------------------


def fx(settings: Settings, *, today: date | None = None) -> EconomicRunResult:
    """Bring the exchange-rate table up to date. Needs the API key, checked
    before anything else."""
    client = ForexApiClient(settings.forex_api)
    return asyncio.run(
        run_fx(
            client=client,
            store=open_economic_store(settings),
            start=settings.economic.fx_start,
            today=today or datetime.now(UTC).date(),
        )
    )


async def run_fx(
    *, client: ForexApiClient, store: EconomicStore, start: date, today: date
) -> EconomicRunResult:
    """Fetch the missing days, then rebuild the FX table."""
    parsed: dict[date, FxParsed] = {}
    for day in store.fx_days():
        try:
            parsed[day] = parse_fx_openexchangerates(store.read_fx(day), day)
        except EconomicParseError as exc:
            logger.warning(
                "fx %s: saved file won't parse, fetching again: %s", day, exc
            )

    wanted = fx_wanted_days(start, today)
    missing = fx_missing(wanted, parsed)
    tally = _Fetching("fx")
    async with client:
        for day in missing:
            try:
                text = await client.fetch_day(day)
            except STOPPING_ERRORS as exc:
                tally.stop(str(exc))
                break
            try:
                response = parse_fx_openexchangerates(text, day)
            except EconomicParseError as exc:
                if tally.unparsable_answer(str(day), exc):
                    break
                continue
            store.save_fx(day, text)
            parsed[day] = response
            tally.saved()

    table = validate_economic(build_fx(parsed.values()), "fx")
    store.write_table("fx", table)
    gaps = [f"{day}" for day in sorted(parsed) if parsed[day].gap]
    already = len(wanted) - len(missing)
    return _result("fx", tally, already, gaps, table, store)


# --- CPI ---------------------------------------------------------------------


def cpi(settings: Settings, *, now: datetime | None = None) -> EconomicRunResult:
    """Bring the CPI table up to date (no key needed)."""
    return asyncio.run(
        run_cpi(
            client=BlsApiClient(settings.bls_api),
            store=open_economic_store(settings),
            series=settings.economic.cpi_series,
            start=settings.economic.cpi_start,
            now=now or datetime.now(UTC),
        )
    )


async def run_cpi(
    *,
    client: BlsApiClient,
    store: EconomicStore,
    series: str,
    start: date,
    now: datetime,
) -> EconomicRunResult:
    """Fetch the missing years of ``series``, then rebuild the CPI table.

    ``now`` (UTC) names each saved response, and its date is "today" for the
    once-a-day rule.
    """
    now = now.astimezone(UTC)
    today = now.date()
    saved: list[tuple[CpiFile, CpiParsed]] = []
    for file in store.cpi_files(series):
        try:
            saved.append((file, parse_cpi_bls(store.read_cpi(series, file), series)))
        except EconomicParseError as exc:
            logger.warning(
                "cpi %s (%s-%s): saved file won't parse; its years count as"
                " missing: %s",
                file.fetched_at,
                file.start_year,
                file.end_year,
                exc,
            )

    stored = {m.month for _, p in saved for m in p.months}
    unavailable = {m for _, p in saved for m in p.unavailable} - stored
    asked_today = {
        year
        for file, _ in saved
        if file.fetched_at.date() == today
        for year in range(file.start_year, file.end_year + 1)
    }
    wanted = cpi_wanted_months(start, today)
    known = stored | unavailable
    tally = _Fetching("cpi")
    async with client:
        for first, last in plan_cpi(wanted, known, asked_today, BLS_MAX_YEARS):
            years = f"{first}-{last}"
            try:
                text = await client.fetch_years(series, first, last)
            except STOPPING_ERRORS as exc:
                tally.stop(str(exc))
                break
            try:
                response = parse_cpi_bls(text, series)
            except EconomicParseError as exc:
                if tally.unparsable_answer(years, exc):
                    break
                continue
            for message in response.messages:
                logger.warning("cpi %s %s: the source said: %s", series, years, message)
            file = CpiFile(fetched_at=now, start_year=first, end_year=last)
            store.save_cpi(series, file, text)
            saved.append((file, response))
            tally.saved()

    newest_first = [p for _, p in sorted(saved, key=lambda s: s[0].fetched_at)][::-1]
    table = validate_economic(build_cpi(newest_first), "cpi")
    store.write_table("cpi", table)
    gaps = sorted(
        {m for p in newest_first for m in p.unavailable} - set(table["month"])
    )
    already = sum(1 for month in wanted if month in known)
    return _result("cpi", tally, already, [f"{m:%Y-%m}" for m in gaps], table, store)


# --- Shared ------------------------------------------------------------------


def _result(
    kind: Literal["fx", "cpi"],
    tally: _Fetching,
    already: int,
    gaps: list[str],
    table: pl.DataFrame,
    store: EconomicStore,
) -> EconomicRunResult:
    dates = table[table.columns[0]]
    result = EconomicRunResult(
        kind=kind,
        outcome=tally.outcome,
        reason=tally.reason,
        fetched=tally.fetched,
        already=already,
        failed=tally.failed,
        gaps=tuple(gaps),
        rows=table.height,
        first=dates.min() if table.height else None,  # type: ignore[arg-type]
        last=dates.max() if table.height else None,  # type: ignore[arg-type]
        location=store.location(kind),
    )
    log = logger.warning if result.outcome == "stopped" else logger.info
    log(result.describe())
    return result
