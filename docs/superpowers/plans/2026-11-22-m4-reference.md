# M4 Reference Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `coffee-review fx` and `coffee-review cpi` keep exchange rates (the 1st of every month from 2010, USD base) and US CPI-U (monthly from 1997) up to date in `data/reference/`, saving every source response under `data/raw/` first, and a rerun costs zero requests.

**Architecture:**
- **Models** (`models/reference.py`): the rows a parser returns, the names of saved responses, and pandera contracts for the four tidy tables (`fx`, `cpi`, `fx_gaps`, `cpi_gaps`).
- **Clients** (`clients/reference.py` Protocols and errors; `forex_api.py`, `bls_api.py`) return response text when the source answered and raise a typed error when it didn't.
- **Parsers** (`parsers/fx_openexchangerates.py`, `parsers/cpi_bls.py`): pure text → rows.
- **Storage** (`storage/reference_store.py`): a raw store for responses and a tidy store for Parquet, each a Protocol with a file implementation and an in-memory fake.
- **Transforms** (`transforms/reference.py`): pure "what to fetch" and "merge saved responses into tables", with `today` passed in.
- **Pipeline** (`pipeline/reference.py`): the source registry, `run_fx`/`run_cpi` with stores and clients passed in, and the `fx`/`cpi` composition roots. CLI commands on top.

**Tech Stack:** Python 3.14, httpx (via `BaseHttpClient`), Polars, pandera (Polars backend), pydantic, Typer, pytest, hypothesis.

**Spec:** `docs/superpowers/specs/2026-11-22-m4-reference-design.md` (approved 2026-11-22).

## Global Constraints

- **Each task ends with a pause for Tyler's review.** Claude explains the change and commits only after Tyler has reviewed it. One concern per commit.
- **The dependency rule** (import-linter): `cli → pipeline → {clients | storage | parsers | transforms} → {models | config}`; workers don't import each other; `parsers`, `transforms`, `models` import no `httpx`/`sqlite3`. Only `storage/` knows paths and formats.
- **Every date is UTC.** "Today" is `datetime.now(UTC).date()`, taken once by the composition root and passed down; pure functions take `today` as an argument.
- **Never fetch what's stored.** A day/month counts as stored when the *configured source* has a saved response for it (a gap response counts).
- **Raw first.** A response is saved (atomically) before it's used; tidy tables are always rebuilt in full from the saved responses, then validated, then written atomically. Nothing is written if a contract fails.
- **Source names appear only** as the raw folder name and the `source` column. No `oxr_`/`bls_` columns; the API key only in `ForexApiClient`'s header.
- **Tests never touch the network or `data/`.** Response samples are hand-written JSON, never real responses.
- **Defaults:** `fx_start = 2010-01-01`, `cpi_start = 1997-01-01`, `cpi_series = "CUUR0000SA0"`, BLS keyless v1 `https://api.bls.gov/publicAPI/v1/`, BLS `max_years = 10`.
- **Exit codes:** 0 finished, 1 error, 3 stopped by the source (the existing `EXIT_STOPPED`).
- **Every task ends with `uv run pre-commit run --all-files` (exit 0) and `uv run pytest` passing.**

## Review Focus

1. **A response with a base other than USD** (a paid plan, or a future source): must never be stored as units per USD. The parser raises `ReferenceParseError`; pinned in Task 2.
2. **A saved raw file that's corrupt or hand-edited** (truncated JSON): the run logs a warning, treats its period as missing, fetches it again and overwrites the file. Pinned in Task 7 (and the same step in `run_cpi`, Task 8).
3. **BLS's daily limit** (`REQUEST_NOT_PROCESSED`, status 200): a refusal, so the run stops with exit 3, never a "no data" answer saved as gaps. Pinned in Task 3.
4. **A start setting that isn't a 1st** (`COFFEE_REVIEW_REFERENCE__FX_START=2010-01-15`): rejected when settings load with a clear message, not silently snapped. Pinned in Task 1.
5. **A currency that appears or disappears over time** (HRK ends 2023; a day lacks TWD): only that row is absent; it's neither a gap nor a failure. Pinned in Task 5.

---

### Task 1: Settings and reference models

**Files:**
- Modify: `src/coffee_review/config.py` (add `ReferenceSettings`, `Settings.reference`; `BLSApiSettings.base_url` → v1)
- Create: `src/coffee_review/models/reference.py`
- Modify: `src/coffee_review/models/clean.py` (move `_first_of_month` to `models/frames.py` as `first_of_month`, import it back)
- Create: `src/coffee_review/models/frames.py`
- Modify: `.env.example` (commented `REFERENCE__FX_SOURCE`, `REFERENCE__CPI_SOURCE`)
- Test: `tests/test_config.py`, `tests/models/test_reference_contract.py`

**Interfaces:**
- Produces (config): `ReferenceSettings(BaseModel)` with `fx_source: str = "openexchangerates"`, `cpi_source: str = "bls"`, `fx_start: date = date(2010, 1, 1)`, `cpi_start: date = date(1997, 1, 1)`, `cpi_series: str = "CUUR0000SA0"`; `Settings.reference: ReferenceSettings`.
- Produces (models, all `FrozenModel` unless noted):
  - `FxRate(day: date, currency: str, units_per_usd: float)`
  - `CpiMonth(month: date, series: str, index_value: float, final: bool)`
  - `ParseProblem(where: str, value: str, reason: str)`: a value a parser dropped.
  - `FxParsed(day: date, rates: tuple[FxRate, ...], gap: str | None, problems: tuple[ParseProblem, ...])`: `gap` is what the source said when it has no data for the day (`"not_available"`), else `None`.
  - `CpiParsed(series: str, months: tuple[CpiMonth, ...], problems: tuple[ParseProblem, ...])`
  - `FxResponse(source: str, day: date)` and `CpiResponse(source: str, series: str, fetched_at: UtcDatetime, start_year: int, end_year: int)`: the identity of a saved response.
  - `class ReferenceParseError(Exception)`: a response that isn't a valid answer.
  - `type ReferenceTable = Literal["fx", "cpi", "fx_gaps", "cpi_gaps"]`
  - pandera models `FxFrame`, `CpiFrame`, `FxGapFrame`, `CpiGapFrame`; `FX_SCHEMA`, `CPI_SCHEMA`, `FX_GAPS_SCHEMA`, `CPI_GAPS_SCHEMA` (derived as in `models/clean.py`).
  - `validate_reference(frame: pl.DataFrame, table: ReferenceTable) -> pl.DataFrame`; `class ReferenceContractError(Exception)`.
- Produces (frames): `first_of_month(data: PolarsData) -> pl.LazyFrame`.

Columns (every field has a `description=`, which feeds the data dictionary):

| Table | Columns | Checks |
|---|---|---|
| `fx` | `day` Date, `currency` String, `units_per_usd` Float64, `source` String | day first of month; currency matches `^[A-Z]{3}$`; units > 0; unique (`day`, `currency`) |
| `cpi` | `month` Date, `series` String, `index_value` Float64, `source` String | month first of month; index > 0; unique (`month`, `series`) |
| `fx_gaps` | `day` Date, `source` String, `said` String | day first of month; unique (`day`, `source`) |
| `cpi_gaps` | `month` Date, `series` String, `source` String, `said` String | month first of month; unique (`month`, `series`, `source`) |

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
def test_reference_defaults() -> None:
    r = Settings().reference
    assert (r.fx_source, r.cpi_source) == ("openexchangerates", "bls")
    assert (r.fx_start, r.cpi_start) == (date(2010, 1, 1), date(1997, 1, 1))
    assert r.cpi_series == "CUUR0000SA0"


def test_bls_is_keyless_v1() -> None:
    assert str(Settings().bls_api.base_url) == "https://api.bls.gov/publicAPI/v1/"


@pytest.mark.parametrize("field", ["FX_START", "CPI_START"])
def test_reference_start_must_be_a_first(monkeypatch, field) -> None:
    monkeypatch.setenv(f"COFFEE_REVIEW_REFERENCE__{field}", "2010-01-15")
    with pytest.raises(ValidationError, match="must be the 1st of a month"):
        Settings()


# tests/models/test_reference_contract.py: a helper frame(table, **overrides) builds a one-row valid frame
def test_each_valid_frame_passes(
    table,
) -> None: ...  # parametrized over the four tables
def test_fx_rejects_non_positive_rate() -> None:
    with pytest.raises(ReferenceContractError, match="units_per_usd"):
        validate_reference(frame("fx", units_per_usd=0.0), "fx")


def test_fx_rejects_lowercase_currency() -> None: ...  # "twd" → error naming currency
def test_fx_rejects_day_not_first() -> None: ...  # 2020-01-02 → error
def test_fx_rejects_duplicate_day_currency() -> None: ...
def test_cpi_rejects_duplicate_month_series() -> None: ...
def test_schemas_match_contracts() -> None:
    assert FX_SCHEMA == pl.Schema(
        {
            "day": pl.Date,
            "currency": pl.String,
            "units_per_usd": pl.Float64,
            "source": pl.String,
        }
    )
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_config.py tests/models/test_reference_contract.py -q`
Expected: FAIL (`AttributeError: reference`, `ModuleNotFoundError: coffee_review.models.reference`)

- [ ] **Step 3: Implement the settings, the models and the moved check**

The first-of-month validator raises `ValueError("<field> must be the 1st of a month")`. `validate_reference` follows `validate_clean` (lazy mode, one error listing every failure).

- [ ] **Step 4: Run them to see them pass, then the whole suite**

Run: `uv run pytest -q`
Expected: all pass (the M3 contract tests still pass after the move).

- [ ] **Step 5: Pause for review, then commit**

```bash
git add src/coffee_review/config.py src/coffee_review/models/ .env.example tests/test_config.py tests/models/test_reference_contract.py
git commit -m "feat: reference settings and the reference-table contracts"
```

---

### Task 2: Parsers for OpenExchangeRates and the BLS

**Files:**
- Create: `src/coffee_review/parsers/fx_openexchangerates.py`, `src/coffee_review/parsers/cpi_bls.py`
- Test: `tests/parsers/test_fx_openexchangerates.py`, `tests/parsers/test_cpi_bls.py` (samples as small `json.dumps` dicts in the test files)

**Interfaces:**
- Consumes: `FxParsed`, `FxRate`, `CpiParsed`, `CpiMonth`, `ParseProblem`, `ReferenceParseError` (Task 1).
- Produces: `parse_fx_openexchangerates(text: str, day: date) -> FxParsed`; `parse_cpi_bls(text: str, series: str) -> CpiParsed`.

Rules:
- FX: a 200 body needs `base == "USD"` and a `rates` object, else `ReferenceParseError`. Each rate: code must match `^[A-Z]{3}$` and value be a finite number > 0, otherwise a `ParseProblem` and the rate is dropped. `USD` itself is kept (1.0). An error body `{"error": true, "message": "not_available", ...}` → `FxParsed(gap="not_available", rates=())`. Any other error body, or invalid JSON → `ReferenceParseError`.
- CPI: `status` must be `REQUEST_SUCCEEDED` and `Results.series` a list, else `ReferenceParseError`. Only the entry whose `seriesID == series` is read; a missing entry means no months (not an error). Periods `M01`–`M12` become `month = date(year, n, 1)`; `M13` and any other period are dropped silently. `value` must parse to a finite float > 0, else a `ParseProblem`. `final` is `False` when any footnote has `code == "P"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_fx_reads_every_rate() -> None:
    parsed = parse_fx_openexchangerates(
        oxr(rates={"USD": 1, "TWD": 31.2, "CAD": 1.35}), date(2020, 1, 1)
    )
    assert {(r.currency, r.units_per_usd) for r in parsed.rates} == {
        ("USD", 1.0),
        ("TWD", 31.2),
        ("CAD", 1.35),
    }
    assert parsed.gap is None and parsed.problems == ()


def test_fx_rejects_a_non_usd_base() -> None:
    with pytest.raises(ReferenceParseError, match="base"):
        parse_fx_openexchangerates(oxr(base="EUR"), date(2020, 1, 1))


def test_fx_drops_a_zero_rate_with_a_problem() -> None:
    parsed = parse_fx_openexchangerates(
        oxr(rates={"USD": 1, "VEF": 0}), date(2020, 1, 1)
    )
    assert [r.currency for r in parsed.rates] == ["USD"]
    assert parsed.problems[0].value == "VEF=0"


def test_fx_not_available_is_a_gap() -> None:
    body = json.dumps(
        {"error": True, "status": 400, "message": "not_available", "description": "..."}
    )
    assert parse_fx_openexchangerates(body, date(2020, 1, 1)).gap == "not_available"


@pytest.mark.parametrize(
    "text",
    ["", "{", json.dumps({"error": True, "status": 401, "message": "invalid_app_id"})],
)
def test_fx_rejects_what_isnt_an_answer(text) -> None: ...


def test_cpi_reads_months_and_drops_m13() -> None:
    parsed = parse_cpi_bls(
        bls([("2020", "M01", "257.971"), ("2020", "M13", "258.811")]), "CUUR0000SA0"
    )
    assert [(m.month, m.index_value) for m in parsed.months] == [
        (date(2020, 1, 1), 257.971)
    ]


def test_cpi_preliminary_is_not_final() -> None:
    parsed = parse_cpi_bls(
        bls([("2020", "M02", "258.678")], footnote="P"), "CUUR0000SA0"
    )
    assert parsed.months[0].final is False


def test_cpi_bad_value_is_a_problem() -> (
    None
): ...  # value "-" → dropped, problem recorded
def test_cpi_other_series_ignored() -> None: ...  # seriesID differs → months == ()
def test_cpi_not_succeeded_is_an_error() -> (
    None
): ...  # status "REQUEST_FAILED" → ReferenceParseError
def test_cpi_empty_data_with_message_is_no_months() -> (
    None
): ...  # REQUEST_SUCCEEDED, data [] → months == ()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/parsers/test_fx_openexchangerates.py tests/parsers/test_cpi_bls.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement both parsers**

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/parsers -q`
Expected: PASS

- [ ] **Step 5: Pause for review, then commit**

```bash
git add src/coffee_review/parsers/fx_openexchangerates.py src/coffee_review/parsers/cpi_bls.py tests/parsers/
git commit -m "feat: parse OpenExchangeRates and BLS responses"
```

---

### Task 3: Clients: the Protocols, the typed errors, and two fetch methods

**Files:**
- Create: `src/coffee_review/clients/reference.py`
- Modify: `src/coffee_review/clients/forex_api.py` (add `fetch_day`, narrow `retryable_status`)
- Create: `src/coffee_review/clients/bls_api.py`
- Test: `tests/clients/test_forex_api.py` (extend), `tests/clients/test_bls_api.py`

**Interfaces:**
- Produces (`clients/reference.py`):
  - `class SourceAuthError(Exception)`, `class SourceRefusedError(Exception)`, `class SourceUnavailableError(Exception)`. Messages name the source and status, never the key.
  - `class FxClient(Protocol)`: `async def fetch_day(self, day: date) -> str`, plus `__aenter__`/`__aexit__`.
  - `class CpiClient(Protocol)`: `async def fetch_years(self, series: str, start: int, end: int) -> str`, plus `__aenter__`/`__aexit__`.
- Produces: `ForexApiClient.fetch_day(day)` sends `GET historical/{day:%Y-%m-%d}.json`. `BlsApiClient(settings: BLSApiSettings, *, transport=None)` with `fetch_years`, which sends `POST timeseries/data/` with body `{"seriesid": [series], "startyear": str(start), "endyear": str(end)}`.

Mapping (both clients set `retryable_status = frozenset({408, 500, 502, 503, 504})`; they catch `httpx.HTTPStatusError` and wrap `RetriesExhaustedError`):

| Response | Result |
|---|---|
| 200 | the text (BLS: unless `status == "REQUEST_NOT_PROCESSED"` → `SourceRefusedError`) |
| 400 | the text (an answer: the parser decides gap or error) |
| 401 | `SourceAuthError` |
| 403, 429 | `SourceRefusedError`, never retried |
| other 4xx | `SourceUnavailableError` |
| retries exhausted (5xx, timeout, network) | `SourceUnavailableError` |

- [ ] **Step 1: Write the failing tests** (with `httpx.MockTransport`, unthrottled settings `rate_per_sec=1000, max_retries=1, backoff_base_seconds=0.001`)

```python
async def test_fetch_day_requests_the_historical_path() -> None:
    transport, seen = capture(200, json={"base": "USD", "rates": {}})
    async with ForexApiClient(fast_settings(), transport=transport) as client:
        await client.fetch_day(date(2020, 3, 1))
    assert seen[0].url.path == "/api/historical/2020-03-01.json"

async def test_fetch_day_returns_a_400_body() -> None: ...           # not_available text comes back
async def test_401_is_auth_error_without_the_key() -> None:          # SourceAuthError; KEY not in str(exc)
async def test_429_is_refused_and_not_retried() -> None:             # one request only
async def test_403_is_refused() -> None: ...
async def test_exhausted_5xx_is_unavailable() -> None: ...           # SourceUnavailableError after 2 attempts

async def test_bls_posts_series_and_years() -> None:
    transport, seen = capture(200, json={"status": "REQUEST_SUCCEEDED", "Results": {"series": []}})
    async with BlsApiClient(fast_bls(), transport=transport) as client:
        await client.fetch_years("CUUR0000SA0", 1997, 2006)
    assert seen[0].method == "POST" and seen[0].url.path == "/publicAPI/v1/timeseries/data/"
    assert json.loads(seen[0].content) == {"seriesid": ["CUUR0000SA0"], "startyear": "1997", "endyear": "2006"}

async def test_bls_daily_limit_is_refused() -> None:
    transport, _ = capture(200, json={"status": "REQUEST_NOT_PROCESSED", "message": ["daily threshold"], "Results": {}})
    async with BlsApiClient(fast_bls(), transport=transport) as client:
        with pytest.raises(SourceRefusedError):
            await client.fetch_years("CUUR0000SA0", 2020, 2020)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/clients -q`
Expected: FAIL (`AttributeError: fetch_day`, `ModuleNotFoundError: bls_api`)

- [ ] **Step 3: Implement the Protocols, errors and both methods**

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/clients -q`
Expected: PASS, including the existing key-in-header tests.

- [ ] **Step 5: Pause for review, then commit**

```bash
git add src/coffee_review/clients/ tests/clients/
git commit -m "feat: FX and CPI clients that tell an answer from a refusal"
```

---

### Task 4: Reference stores: raw responses and tidy tables

**Files:**
- Create: `src/coffee_review/storage/reference_store.py`
- Modify: `tests/fakes.py` (add `InMemoryReferenceRawStore`, `InMemoryReferenceStore`)
- Test: `tests/storage/test_reference_store_contract.py`, `tests/storage/test_file_reference_store.py`

**Interfaces:**
- Consumes: `FxResponse`, `CpiResponse`, `ReferenceTable`, the four schemas (Task 1); `write_atomically`, `write_parquet`.
- Produces:
  - `class ReferenceRawStore(Protocol)`: `save_fx(response: FxResponse, text: str) -> None`; `list_fx() -> list[FxResponse]` (every source); `read_fx(response: FxResponse) -> str`; `save_cpi(response: CpiResponse, text: str) -> None`; `list_cpi() -> list[CpiResponse]`; `read_cpi(response: CpiResponse) -> str`; `close() -> None`.
  - `class ReferenceStore(Protocol)`: `write(table: ReferenceTable, frame: pl.DataFrame) -> None`; `read_table(table: ReferenceTable) -> pl.DataFrame` (raises `ReferenceLayerMissingError`); `location(table: ReferenceTable) -> str`; `close() -> None`.
  - `FileReferenceRawStore(root: Path)` with root `settings.data_dir / "raw"`; `ParquetReferenceStore(root: Path)` with root `settings.data_dir / "reference"`; `open_reference_raw_store(settings) -> ReferenceRawStore`; `open_reference_store(settings) -> ReferenceStore`; `class ReferenceLayerMissingError(Exception)`.

Layout: `fx/<source>/<YYYY-MM-DD>.json`; `cpi/<source>/<series>/<YYYYMMDDTHHMMSSZ>_<start>-<end>.json` (UTC). `list_*` skips names that don't fit the pattern and logs a warning. Text is written as UTF-8, atomically; `save_fx` for an existing response replaces it.

- [ ] **Step 1: Write the failing contract tests** (parametrized over file and in-memory, as `test_clean_store_contract.py` is)

```python
def test_fx_save_list_read(raw) -> None:
    r = FxResponse(source="openexchangerates", day=date(2020, 1, 1))
    raw.save_fx(r, '{"a": 1}')
    assert raw.list_fx() == [r] and raw.read_fx(r) == '{"a": 1}'


def test_fx_save_again_replaces(raw) -> None: ...
def test_cpi_save_list_read_keeps_fetched_at_utc(
    raw,
) -> None: ...  # fetched_at round-trips as the same UTC instant
def test_sources_listed_together(raw) -> None: ...  # two sources → both in list_fx()
def test_tidy_write_then_read_each_table(store, table) -> None: ...  # schema preserved
def test_tidy_read_before_write_raises(store) -> None: ...  # ReferenceLayerMissingError
```

File-only tests: the on-disk paths match the layout above; a stray `notes.txt` in `fx/openexchangerates/` is skipped with a warning (`caplog`).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/storage/test_reference_store_contract.py tests/storage/test_file_reference_store.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement both stores and both fakes**

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/storage -q`
Expected: PASS

- [ ] **Step 5: Pause for review, then commit**

```bash
git add src/coffee_review/storage/reference_store.py tests/fakes.py tests/storage/
git commit -m "feat: stores for raw reference responses and the tidy tables"
```

---

### Task 5: FX transforms: what to fetch, and building the tables

**Files:**
- Create: `src/coffee_review/transforms/reference.py`
- Test: `tests/transforms/test_reference_fx.py`

**Interfaces:**
- Consumes: `FxParsed`, `FX_SCHEMA`, `FX_GAPS_SCHEMA` (Task 1).
- Produces:
  - `fx_wanted_days(start: date, today: date) -> list[date]`: every 1st from `start` while `day < today` (a day is wanted once it's over), oldest first.
  - `fx_missing(wanted: Sequence[date], have: Collection[date]) -> list[date]`, in order.
  - `build_fx(parsed: Sequence[tuple[str, FxParsed]], preferred: str) -> tuple[pl.DataFrame, pl.DataFrame]`: `(fx, fx_gaps)` sorted by day then currency. Per day, the preferred source's response is used if there is one, otherwise the alphabetically first other source. A gap response adds an `fx_gaps` row and no rates.

- [ ] **Step 1: Write the failing tests**

```python
def test_wanted_days_stop_before_today() -> None:
    assert fx_wanted_days(date(2026, 9, 1), date(2026, 11, 1)) == [
        date(2026, 9, 1),
        date(2026, 10, 1),
    ]
    assert fx_wanted_days(date(2026, 9, 1), date(2026, 11, 2))[-1] == date(2026, 11, 1)


def test_utc_evening_in_new_york() -> None:
    ny_evening = datetime(2026, 11, 30, 20, 0, tzinfo=ZoneInfo("America/New_York"))
    today = ny_evening.astimezone(UTC).date()  # 2026-12-01
    assert date(2026, 12, 1) not in fx_wanted_days(date(2026, 1, 1), today)
    assert date(2026, 12, 1) in fx_wanted_days(date(2026, 1, 1), date(2026, 12, 2))


@given(start=months(), today=dates(), have=sets(months()))
def test_missing_is_exactly_the_unstored_past_firsts(start, today, have) -> None:
    missing = fx_missing(fx_wanted_days(start, today), have)
    assert all(d.day == 1 and start <= d < today and d not in have for d in missing)
    assert set(missing) | (have & set(fx_wanted_days(start, today))) == set(
        fx_wanted_days(start, today)
    )


def test_build_prefers_the_configured_source() -> (
    None
): ...  # same day from "a" and "b", preferred "b" → b's rates, source "b"
def test_build_fills_from_another_source() -> (
    None
): ...  # day only in "a" → kept, source "a"
def test_build_gap_row() -> (
    None
): ...  # gap → fx_gaps row (day, source, said="not_available"), no fx rows
def test_vanished_currency_is_only_absent() -> None:
    fx, gaps = build_fx(
        [
            ("s", day_with(date(2023, 1, 1), {"USD": 1})),
            ("s", day_with(date(2022, 12, 1), {"USD": 1, "HRK": 7.1})),
        ],
        "s",
    )
    assert fx.filter(pl.col("currency") == "HRK")["day"].to_list() == [
        date(2022, 12, 1)
    ]
    assert gaps.is_empty()


def test_built_tables_meet_their_contracts() -> (
    None
): ...  # validate_reference on both outputs
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/transforms/test_reference_fx.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement the three functions**

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/transforms/test_reference_fx.py -q`
Expected: PASS

- [ ] **Step 5: Pause for review, then commit**

```bash
git add src/coffee_review/transforms/reference.py tests/transforms/test_reference_fx.py
git commit -m "feat: work out which FX days to fetch and build the FX tables"
```

---

### Task 6: CPI transforms: what to fetch, gaps, and building the tables

**Files:**
- Modify: `src/coffee_review/transforms/reference.py`
- Test: `tests/transforms/test_reference_cpi.py`

**Interfaces:**
- Consumes: `CpiResponse`, `CpiParsed`, `CPI_SCHEMA`, `CPI_GAPS_SCHEMA` (Task 1).
- Produces:
  - `cpi_wanted_months(start: date, today: date) -> list[date]`: every 1st from `start` through the month *before* `today`'s month.
  - `cpi_gap_months(saved: Sequence[tuple[CpiResponse, CpiParsed]]) -> set[date]`: a month is a gap when some response covers its year (`start_year <= year <= end_year`) and contains a later month, but not that month.
  - `cpi_stored_months(saved: Sequence[tuple[CpiResponse, CpiParsed]]) -> set[date]`: months with a `final` row.
  - `plan_cpi(wanted: Sequence[date], saved: Sequence[tuple[CpiResponse, CpiParsed]], today: date, max_years: int) -> list[tuple[int, int]]`. `saved` is the configured source's and series' responses only. Missing = wanted − stored − gaps. Missing years are grouped into contiguous runs, and each run is split into `(start, end)` chunks of at most `max_years`, oldest first. **Newest-month rule:** if missing is exactly `{last month}` and a saved response covering that year has `fetched_at.date() == today`, it returns `[]`.
  - `build_cpi(saved: Sequence[tuple[CpiResponse, CpiParsed]], preferred: str) -> tuple[pl.DataFrame, pl.DataFrame]`: `(cpi, cpi_gaps)`. Per (month, series), the preferred source wins over others, then the newest `fetched_at` within a source. Non-final rows are kept in `cpi` (they're the best value known) but don't count as stored. Gaps come from `cpi_gap_months` per source, with `said = "absent from a response that has later months"`, minus months that have a value.

- [ ] **Step 1: Write the failing tests**

```python
def test_wanted_months_end_last_month() -> None:
    assert cpi_wanted_months(date(2026, 9, 1), date(2026, 11, 22)) == [date(2026, 9, 1), date(2026, 10, 1)]

def test_first_run_asks_in_ten_year_chunks() -> None:
    assert plan_cpi(cpi_wanted_months(date(1997, 1, 1), date(2026, 11, 22)), [], date(2026, 11, 22), 10) == [(1997, 2006), (2007, 2016), (2017, 2026)]

def test_october_2025_is_a_gap_and_not_asked_again() -> None:
    saved = [resp(2025, 2025, months=[m for m in months_of(2025) if m != date(2025, 10, 1)])]
    assert date(2025, 10, 1) in cpi_gap_months(saved)
    assert plan_cpi(cpi_wanted_months(date(2025, 1, 1), date(2026, 1, 20)), saved, date(2026, 1, 20), 10) == []

def test_newest_month_not_out_yet_asked_once_a_day() -> None:
    saved = [resp(2026, 2026, months=months_of(2026)[:9], fetched_at=datetime(2026, 11, 5, 9, tzinfo=UTC))]
    wanted = cpi_wanted_months(date(2026, 1, 1), date(2026, 11, 5))
    assert plan_cpi(wanted, saved, date(2026, 11, 5), 10) == []
    assert plan_cpi(wanted, saved, date(2026, 11, 6), 10) == [(2026, 2026)]

def test_preliminary_month_is_asked_again() -> None: ...   # final=False row → its year planned
@given(...)  # random stored/gap sets
def test_plan_covers_exactly_the_missing_years_in_chunks(...) -> None: ...  # every chunk ≤ max_years; union of chunk years == missing years
def test_build_prefers_configured_then_newest() -> None: ...
def test_built_tables_meet_their_contracts() -> None: ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/transforms/test_reference_cpi.py -q`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Implement the five functions**

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/transforms -q`
Expected: PASS

- [ ] **Step 5: Pause for review, then commit**

```bash
git add src/coffee_review/transforms/reference.py tests/transforms/test_reference_cpi.py
git commit -m "feat: work out which CPI years to fetch, settle gaps, build the CPI tables"
```

---

### Task 7: The FX stage: registry, run, rebuild, stop rules

**Files:**
- Create: `src/coffee_review/pipeline/reference.py`
- Modify: `tests/fakes.py` (add `FakeFxClient(answers: dict[date, str | Exception])` with a `calls: list[date]`)
- Test: `tests/pipeline/test_reference_fx.py`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces:
  - `@dataclass(frozen=True) class FxSource`: `client: Callable[[Settings], FxClient]`, `parse: Callable[[str, date], FxParsed]`.
  - `FX_SOURCES: dict[str, FxSource] = {"openexchangerates": FxSource(client=lambda s: ForexApiClient(s.forex_api), parse=parse_fx_openexchangerates)}`.
  - `class UnknownSourceError(Exception)`: message `unknown fx source 'x'; known: openexchangerates`.
  - `@dataclass(frozen=True) class ReferenceRunResult`: `kind: Literal["fx", "cpi"]`, `outcome: Literal["finished", "stopped"]`, `reason: str | None`, `requests: int`, `already: int`, `failed: int`, `gaps: int`, `dropped: int`, `rows: int`, `first: date | None`, `last: date | None`, `location: str`, and `describe() -> str`.
  - `async def run_fx(*, source_name: str, source: FxSource, client: FxClient, raw: ReferenceRawStore, store: ReferenceStore, start: date, today: date) -> ReferenceRunResult`
  - `def fx(settings: Settings, *, today: date | None = None) -> ReferenceRunResult`: looks up the source (`UnknownSourceError` before building any client), builds the client (so `MissingApiKeyError` comes before any request), opens the stores, runs, closes.

`run_fx` steps:
1. Parse every saved response once (`raw.list_fx()`, every source). A saved file that raises `ReferenceParseError` is logged as a warning and left out, so it counts as *missing* (Review Focus 2).
2. Stored days = days with a good parsed response from `source_name` (a gap counts).
3. For each missing day, oldest first: `text = await client.fetch_day(day)`, then `source.parse(text, day)`. On `ReferenceParseError`, log a warning, `failed += 1`, don't save. Otherwise `raw.save_fx(...)` (replacing a corrupt file) and add it to the parsed set. On `SourceAuthError`, `SourceRefusedError` or `SourceUnavailableError`: `outcome="stopped"`, `reason=str(exc)`, and stop fetching.
4. Always rebuild from the parsed set: `build_fx`, `validate_reference` both tables, `store.write` both. `dropped` = parse problems across the set; `gaps` = rows in `fx_gaps`.

`describe()` shape (spec): `fx finished: 3 fetched, 201 already stored, 0 failed · fx.parquet 34,170 rows, 2010-01-01 to 2026-11-01`. Add `, N gaps` / `, N values dropped` only when non-zero; for `stopped`, add ` (stopped: <reason>)`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_first_run_fetches_every_missing_first_then_rerun_fetches_nothing() -> None:
    client = FakeFxClient.answering_every_day()
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()
    first = await run_fx(source_name="s", source=fake_source(), client=client, raw=raw, store=store, start=date(2026, 8, 1), today=date(2026, 11, 22))
    assert client.calls == [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)]
    again = await run_fx(..., today=date(2026, 11, 22))
    assert len(client.calls) == 4 and again.requests == 0
    assert store.read_table("fx")["day"].unique().len() == 4

async def test_zero_requests_on_rerun_with_the_real_client() -> None:
    # ForexApiClient over an httpx.MockTransport that counts requests; two runs; count unchanged by the second
async def test_not_available_saved_as_gap_and_not_asked_again() -> None: ...
async def test_refused_stops_keeps_earlier_files_and_rebuilds() -> None:
    # day 2 raises SourceRefusedError → outcome "stopped", day 1 saved, fx table has day 1, day 3 never asked
async def test_malformed_response_not_saved_and_retried_next_run() -> None: ...
async def test_corrupt_saved_file_is_refetched_and_replaced() -> None:
    # raw.save_fx(FxResponse("s", date(2026, 9, 1)), "{") beforehand →
    # client asked for 2026-09-01, the file now holds the new text, the fx table has that day
async def test_swap_fills_only_what_the_new_source_lacks() -> None:
    # days saved under "a"; run with source_name "b" → b asked for every day; table rows' source == "b"
def test_unknown_source_fails_before_any_client() -> None: ...   # fx(settings with fx_source="nope") → UnknownSourceError listing "openexchangerates"
def test_missing_key_fails_before_any_request() -> None: ...     # fx(Settings()) → MissingApiKeyError
def test_describe_lines() -> None: ...                           # finished and stopped shapes, exact strings
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/pipeline/test_reference_fx.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement the registry, `run_fx`, `fx` and `ReferenceRunResult`**

- [ ] **Step 4: Run them to see them pass, plus the import rule**

Run: `uv run pytest tests/pipeline -q && uv run lint-imports`
Expected: PASS; both import contracts kept.

- [ ] **Step 5: Pause for review, then commit**

```bash
git add src/coffee_review/pipeline/reference.py tests/fakes.py tests/pipeline/test_reference_fx.py
git commit -m "feat: the fx stage: fetch what's missing, save it raw, rebuild the tables"
```

---

### Task 8: The CPI stage and both CLI commands

**Files:**
- Modify: `src/coffee_review/pipeline/reference.py`
- Modify: `src/coffee_review/cli.py` (`fx_command`, `cpi_command`)
- Modify: `tests/fakes.py` (add `FakeCpiClient(answers: Callable[[str, int, int], str | Exception])` with `calls`)
- Test: `tests/pipeline/test_reference_cpi.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces:
  - `@dataclass(frozen=True) class CpiSource`: `client: Callable[[Settings], CpiClient]`, `parse: Callable[[str, str], CpiParsed]`, `max_years: int`.
  - `CPI_SOURCES = {"bls": CpiSource(client=lambda s: BlsApiClient(s.bls_api), parse=parse_cpi_bls, max_years=10)}`.
  - `async def run_cpi(*, source_name: str, source: CpiSource, client: CpiClient, raw: ReferenceRawStore, store: ReferenceStore, series: str, start: date, today: date, now: datetime) -> ReferenceRunResult`. `now` (UTC) names each saved response, and its date must equal `today`.
  - `def cpi(settings: Settings, *, now: datetime | None = None) -> ReferenceRunResult`.
  - CLI: `coffee-review fx`, `coffee-review cpi`. Each configures logging (`run_log_path(settings.data_dir, "fx" | "cpi", now)`), prints `result.describe()`, and exits `EXIT_FINISHED`/`EXIT_STOPPED`. Any exception follows the `clean_command` pattern and exits `EXIT_ERROR`.

`run_cpi` mirrors `run_fx`. It parses the configured source and series' saved responses once (a corrupt one is skipped with a warning on every run; its years count as missing and are fetched into a new file), calls `plan_cpi`, then for each chunk: `fetch_years` → parse (on `ReferenceParseError`: warn, `failed += 1`, don't save) → save with `CpiResponse(source, series, fetched_at=now, start_year, end_year)`. The same three errors stop it. It always rebuilds with `build_cpi` over every source's saved responses for the series, validates and writes. `describe()`: `cpi finished: 1 request, 359 months stored · cpi.parquet 1997-01 to 2026-10`, with gaps when non-zero (`, 1 gap (2025-10)`).

- [ ] **Step 1: Write the failing tests**

```python
async def test_first_run_three_requests_then_zero() -> None:
    client = FakeCpiClient(full_years_except({date(2025, 10, 1)}))
    first = await run_cpi(
        ...,
        start=date(1997, 1, 1),
        today=date(2026, 11, 22),
        now=datetime(2026, 11, 22, 12, tzinfo=UTC),
    )
    assert client.calls == [
        ("CUUR0000SA0", 1997, 2006),
        ("CUUR0000SA0", 2007, 2016),
        ("CUUR0000SA0", 2017, 2026),
    ]
    again = await run_cpi(
        ..., today=date(2026, 11, 22), now=datetime(2026, 11, 22, 13, tzinfo=UTC)
    )
    assert again.requests == 0
    assert store.read_table("cpi_gaps")["month"].to_list() == [date(2025, 10, 1)]


async def test_next_month_asks_only_this_year() -> (
    None
): ...  # today 2026-12-20 → [("CUUR0000SA0", 2026, 2026)]
async def test_refused_daily_limit_stops_with_saved_kept() -> None: ...
async def test_saved_name_is_utc() -> None: ...  # list_cpi()[0].fetched_at == now


def test_fx_command_prints_summary_and_exits_0(
    monkeypatch,
) -> None: ...  # pipeline fx replaced by a stub result
def test_fx_command_stopped_exits_3(monkeypatch) -> None: ...
def test_fx_command_without_key_exits_1_with_the_message() -> None:
    result = runner.invoke(app, ["fx"])
    assert result.exit_code == 1 and "COFFEE_REVIEW_FOREX_API__API_KEY" in result.output


def test_cpi_in_help() -> None: ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/pipeline/test_reference_cpi.py tests/test_cli.py -q`
Expected: FAIL (`ImportError: run_cpi`; `No such command 'fx'`)

- [ ] **Step 3: Implement `run_cpi`, `cpi` and both commands**

- [ ] **Step 4: Run the whole suite and hooks**

Run: `uv run pytest -q && uv run pre-commit run --all-files`
Expected: all pass; exit 0.

- [ ] **Step 5: Pause for review, then commit**

```bash
git add src/coffee_review/pipeline/reference.py src/coffee_review/cli.py tests/
git commit -m "feat: the cpi stage, and the fx and cpi commands"
```

- [ ] **Step 6: First real runs (ask Tyler first: about 203 OpenExchangeRates requests out of 1,000 this month, and 3 to the BLS)**

Run: `uv run coffee-review cpi && uv run coffee-review fx`, then both again.
Expected: the first runs fetch and write; the second runs print `0 requests` / `0 fetched`. Check whether October 2025 shows as a CPI gap, and record what the real summary lines say in the review.

---

### Task 9: Docs: ADR 0009, architecture, data dictionary, conventions, roadmap, README

**Files:**
- Create: `docs/decisions/0009-raw-reference-responses-and-swappable-sources.md`
- Modify: `docs/architecture.md` (pipeline-at-a-glance arrows from the two APIs into `data/raw/fx`, `data/raw/cpi` and `data/reference/`; a "Reference data (M4)" stage diagram; the layer table)
- Modify: `scripts/generate_data_dictionary.py` (a "Reference layer" section from the four pandera models; a hand-written raw-folders table like `EXTRA_FIELDS`), then regenerate `docs/data-dictionary.md`
- Modify: `docs/conventions.md` (new modules; `data/raw/` holds scrape's pages and the reference responses), `docs/roadmap.md` (M4 items reworded to the spec and checked; backlog: prices in review text, 78 pre-2009 pages), `README.md` (the two commands; BLS needs no key)
- Test: `tests/test_data_dictionary.py` (existing, must pass)

- [ ] **Step 1: Regenerate the dictionary and watch the freshness test fail before it's committed**

Run: `uv run pytest tests/test_data_dictionary.py -q` after the generator change, before regenerating.
Expected: FAIL ("docs/data-dictionary.md is out of date").

- [ ] **Step 2: Regenerate, and write the ADR and doc changes**

Run: `uv run python scripts/generate_data_dictionary.py`

The ADR records approaches A/B/C from the spec, the choice of B, and the swap rule and its cost (≈200 FX requests, 3 CPI).

- [ ] **Step 3: Check the Mermaid diagrams render** (open `docs/architecture.md`'s new diagram in the browser, as for M3)

- [ ] **Step 4: Run the whole suite and hooks**

Run: `uv run pytest -q && uv run pre-commit run --all-files`
Expected: all pass; exit 0.

- [ ] **Step 5: Pause for review, then commit**

```bash
git add docs/ scripts/generate_data_dictionary.py README.md
git commit -m "docs: M4 reference data: ADR 0009, architecture, data dictionary, roadmap"
```
