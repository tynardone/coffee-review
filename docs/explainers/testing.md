# How the tests are built

This is a guide to the testing techniques this project uses, with real
examples from `tests/`. It covers what a good unit test looks like, the kinds
of test doubles (stubs, spies, mocks, fakes), fixtures, parametrization,
property-based and snapshot tests, and how the suite is organised. The rules
themselves are in [conventions.md](../conventions.md#tests). This page shows
how they're applied, and why.

## 1. The shape of a unit test

A unit test runs one piece of code on an input you chose and checks the
result. Most tests here have three parts, separated by blank lines: set up
the input (*arrange*), call the code (*act*), and check the result
(*assert*).

```python
# tests/pipeline/test_fetch_rules.py
def test_find_delistings_relists_reappearing() -> None:
    manifest = {"back": record("back", delisted_at=T)}

    plan = find_delistings([entry("back")], manifest)

    assert plan == Delistings(delisted=frozenset(), relisted=frozenset({"back"}))
```

What makes it a good test:

- **The name says the behaviour.** `test_find_delistings_relists_reappearing`
  tells you what broke when it fails, without opening the file.
  `test_delistings_2` wouldn't.
- **It checks one behaviour.** If a name needs "and", it's two tests. When a
  test checks one thing, a failure points to one cause.
- **It checks a result, not the steps.** The assertion compares what came
  out. It doesn't care how `find_delistings` worked it out, so the function
  can be rewritten without touching the test.
- **The input is as small as it can be.** One manifest row and one sitemap
  entry, with helper functions (`record`, `entry`) filling in fields the test
  doesn't care about. The only values you see are the ones that matter.
- **Nothing is hidden.** No network, no clock, no files left over from
  another test. Run it a thousand times and it gives the same answer.

### Watching it fail

A test you've never seen fail might not test anything. It could be checking
the wrong value, or never reaching the code. So write the test first and run
it before the code exists: it should fail, and for the reason you expect.
When the code already exists, break it on purpose once (flip a `<` to `<=`,
delete a line) and check that the test catches it. This is a hand-run version
of *mutation testing*.

## 2. Different layers, different tests

Code is easier to test when it does no I/O. This project is built that way on
purpose: `parsers/`, `transforms/` and `models/` never touch the network or
the disk (import-linter enforces it), so their tests are plain function
calls. The I/O sits at the edges, and the tests change style as you move
outwards:

| Layer | What it does | How it's tested | Example |
|---|---|---|---|
| `parsers/`, `transforms/`, `models/` | pure logic | call it with values, compare the output | `tests/transforms/test_scores.py` |
| `clients/` | HTTP | a fake server plugged into httpx | `tests/clients/test_http.py` |
| `storage/` | files, SQLite, Parquet | a real store in a temp folder | `tests/storage/test_raw_store_contract.py` |
| `pipeline/` | puts the others in order | real logic, fake client and stores | `tests/pipeline/test_scrape.py` |
| `cli.py` | arguments and exit codes | Typer's test runner, pipeline replaced | `tests/test_cli.py` |
| the real site | does it still look like we think? | one live request, skipped by default | `tests/test_live_site.py` |

Most tests sit in the top rows, where they're fast and exact. Fewer sit
further down, where each test covers more code but takes more setup. That's
the "test pyramid": many small tests at the base, a few wide ones at the top.

## 3. Test doubles

A *test double* stands in for a real dependency (a web site, a database, the
clock) so a test can control it. The names come from Gerard Meszaros's
*xUnit Test Patterns*. People often call every double a "mock", but the kinds
behave differently:

| Kind | What it does | In this project |
|---|---|---|
| **Dummy** | is passed in but never used | `NoProgress()`, a progress display that does nothing |
| **Stub** | returns canned answers | `canned_summary()` in `test_cli.py`; the replacement `configure_logging` |
| **Spy** | a stub that also records how it was called | `ScrapeSpy` in `test_cli.py`; `recorded_sleeps` |
| **Mock** | is set up with expected calls and fails the test if they don't happen | not used |
| **Fake** | a real, working, simpler implementation | `InMemoryRawStore`, `FakeSite`, `InMemoryCleanStore` in `tests/fakes.py` |

### Stubs and spies

`recorded_sleeps` (in `tests/conftest.py`) replaces `asyncio.sleep` with a
function that writes the delay down and returns straight away:

```python
async def fake_sleep(delay: float, *args: object, **kwargs: object) -> None:
    delays.append(delay)
```

So a retry test can check the back-off without waiting:

```python
# tests/clients/test_http.py
async def test_retries_then_succeeds(recorded_sleeps: list[float]) -> None:
    server = ScriptedServer(
        httpx.Response(503), httpx.Response(503), httpx.Response(200, text="ok")
    )

    async with server.client() as client:
        response = await client.request("GET", "page")

    assert response.text == "ok"
    assert server.calls == 3
    assert len(recorded_sleeps) == 2
```

That's a stub (it stops the real sleep) and a spy (it records each delay).
`ScriptedServer` is a stub too: it plays back a fixed list of replies, one
per request.

### Why there are no mocks

A mock checks *interactions*: "`save_page` was called once, with these
arguments." Python's `unittest.mock` makes that easy, but such tests describe
how the code works rather than what it does. Rename a method, or save two
pages in one call, and the test fails even though nothing visible changed.
After a few false alarms, people stop trusting the tests, or stop
refactoring.

A fake checks *state*: run the code, then look at what it left behind.

```python
# tests/pipeline/test_scrape.py, roughly
store = InMemoryRawStore()
await scrape(site_with(THREE), store)

assert store.read_page("a") == "<html>a</html>"
```

The test doesn't care which store methods ran, or how often. It checks that
the page ended up saved. Martin Fowler's essay *Mocks Aren't Stubs* sets out
the two styles.

Mocks have their place. Use one when the interaction *is* the behaviour: an
email was sent, or a payment API was called exactly once. This project has no
such cases. `test_cli.py` comes closest, and it uses a spy (`ScrapeSpy`
records what the CLI passed to `scrape`) and asserts on the recorded calls
afterwards.

### Fakes, and keeping them honest

`InMemoryRawStore` keeps pages and manifest rows in two dicts instead of
gzip files and SQLite. That makes pipeline tests fast and needs no clean-up.
The risk is that the fake drifts from the real store, and then the pipeline
tests pass against something that doesn't exist.

The fix is a **contract test**: one set of tests run against both the real
store and the fake. A parametrized fixture (section 5) hands each test one
store, then the other:

```python
# tests/storage/test_raw_store_contract.py
@pytest.fixture(params=["gzip_sqlite", "in_memory"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[RawStore]:
    raw_store: RawStore = (
        GzipSqliteRawStore(tmp_path / "raw")
        if request.param == "gzip_sqlite"
        else InMemoryRawStore()
    )
    yield raw_store
    raw_store.close()


def test_save_then_read_page(store: RawStore) -> None:
    store.save_page(entry(), "<html>café</html>", T, lookback=False)

    assert store.read_page("x") == "<html>café</html>"
```

Every test in the file runs twice. If the fake does something the real store
wouldn't, a test fails. The parsed and clean stores have contract files of
their own.

The pipeline code doesn't know which store it has. It's written against
`RawStore`, a `typing.Protocol`: a description of the methods a store must
have. Both classes fit it without inheriting from anything, and mypy checks
that they do.

### Faking HTTP

httpx can send requests through a *transport* you supply instead of the
network. `httpx.MockTransport(handler)` calls your function with each request
and returns whatever response it gives back. Despite the name, it isn't a
mock in the sense above: it's the hook a fake server plugs into.

`FakeSite` (in `tests/fakes.py`) is that fake server. It maps paths to
responses, answers 404 for anything else, and lists every path it was asked
for:

```python
site = FakeSite(
    {
        "/sitemap_index.xml": httpx.Response(
            200, text=index_xml("/review-sitemap.xml")
        ),
        "/review/a/": httpx.Response(429),
    }
)
```

A route can also be a function, for a page that answers differently on
different runs (a 429 first, then a 200). Because the client gets the fake
through its `transport` argument, the real retry, rate-limit and pushback
code all runs. Only the network is swapped out.

## 4. Making code testable: pass dependencies in

You can only swap in a double if the code lets you. Here, every stage has two
entry points:

- `run_scrape(config, client=..., store=..., now=..., clock=..., progress=...)`
  takes everything it depends on as arguments. Tests call this one.
- `scrape(settings, ...)` builds the real client and store and calls
  `run_scrape`. It's the *composition root*: the one place real parts are
  wired together. The CLI calls this one.

This is *dependency injection*, and it needs no framework, just parameters.
It covers time too. A clock passed in as a function can be replaced with one
that returns chosen values:

```python
# tests/pipeline/test_clean.py
def test_elapsed_from_clock() -> None:
    ticks = iter([10.0, 12.5])

    result = run_clean(
        parsed=parsed_with(roasted("a")),
        clean=InMemoryCleanStore(),
        clock=lambda: next(ticks),
    )

    assert result.elapsed_seconds == 2.5
```

With the real clock this test would be flaky. With a fixed one it's exact.
The scrape tests pass `now=FIRST_RUN` and `now=LATER_RUN` the same way, to
act out "the next day" without waiting a day.

### monkeypatch: when you can't pass it in

Sometimes a dependency can't be passed in. The CLI commands call
`pipeline.scrape.scrape` directly, and `asyncio.sleep` is called deep inside
the HTTP client. pytest's `monkeypatch` fixture replaces a name for the
length of one test and puts the original back afterwards:

```python
# tests/test_cli.py
@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> ScrapeSpy:
    fake = ScrapeSpy()
    monkeypatch.setattr(cli, "scrape", fake)
    monkeypatch.setattr(cli, "configure_logging", lambda *args, **kwargs: None)
    return fake
```

Patch the name **where it's looked up**, not where it's defined. `cli.py`
does `from coffee_review.pipeline.scrape import scrape`, which copies the
name into the `cli` module, so the test patches `cli.scrape`. Patching
`coffee_review.pipeline.scrape.scrape` would leave the CLI's copy pointing at
the real function. The HTTP client, by contrast, calls `asyncio.sleep(...)`
through the module, so `recorded_sleeps` patches the `sleep` attribute of
`asyncio` itself.

Treat monkeypatch as the fallback. When you find yourself patching a lot,
that usually means the code should take the dependency as a parameter.

## 5. Fixtures

A pytest *fixture* is a function that sets something up for a test. A test
asks for a fixture by naming it as a parameter, and pytest calls the fixture
and passes in what it returns.

```python
@pytest.fixture
def recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    ...
    return delays


async def test_retries_then_succeeds(recorded_sleeps: list[float]) -> None: ...
```

The features used here:

- **Built-in fixtures.** pytest comes with several. `tmp_path` is a fresh,
  empty folder for each test (the storage tests write there). `monkeypatch`
  replaces names and environment variables and undoes it afterwards.
  `caplog` captures log records (section 8).
- **Fixtures using fixtures.** `recorded_sleeps` asks for `monkeypatch`.
  pytest works out the order.
- **Clean-up with `yield`.** Code before the `yield` runs before the test and
  code after it runs afterwards, even if the test failed. The contract
  fixture above closes the store that way.
- **`autouse=True`.** The fixture runs for every test in its scope without
  being asked for. `clean_env` in `tests/conftest.py` is autouse for the
  whole suite. It removes `COFFEE_REVIEW_*` environment variables, moves into
  an empty temp folder so your real `.env` is never read, and clears the
  cached settings before and after. Without it, a test could pass on your
  machine because of your `.env` and fail on CI. `test_scrape.py` has a
  file-level autouse fixture, `_no_real_sleeps`, so no scrape test ever
  really waits.
- **`conftest.py`.** pytest loads fixtures from it automatically. Ones in
  `tests/conftest.py` are available to every test. A `conftest.py` in a
  subfolder would apply to that folder only.
- **Parametrized fixtures.** `@pytest.fixture(params=[...])` runs every test
  that uses the fixture once per value, as in the contract tests.
- **Scope.** By default a fixture is built fresh for each test
  (`scope="function"`). Wider scopes (`"module"`, `"session"`) share one
  object between tests. That's faster, but the tests can then affect each
  other. Everything here uses the default.

### Fixtures or plain helper functions?

Not every bit of setup needs to be a fixture. Many test files use plain
functions instead: `settings()`, `site_with(...)` and `scrape(...)` in
`test_scrape.py`, `record(...)` and `entry(...)` in `test_fetch_rules.py`,
and `run(transform, field, values)` in `tests/transforms/helpers.py`.

A rough rule:

- **Use a fixture** when setup needs clean-up, needs other fixtures
  (`tmp_path`, `monkeypatch`), should apply to every test (autouse), or should
  run each test several times (params).
- **Use a function** when setup takes arguments. `site_with({"a": OLD})`
  shows exactly what each test's site holds. A fixture can't take arguments
  as easily, and you'd have to look elsewhere to see what it built.

Helpers shared between files go in modules under `tests/`: `fakes.py`,
`economic_samples.py` (canned API bodies and clients for the economic
stages), and `transforms/helpers.py`.

### Fixture files

"Fixture" also means test data kept in files. `tests/fixtures/` holds saved
review pages, a Cloudflare challenge page and two sitemaps. They're cut down
to the markup the parser reads, and the review text is replaced with
placeholders (`"Placeholder notes, paragraph 1."`), so the repo holds no
copied reviews. Small inputs make a failure easy to read. Real pages would
add thousands of lines of noise.

## 6. Parametrization: one test, many inputs

`@pytest.mark.parametrize` runs one test function once per input. Use it
when several cases share the same steps and differ only in the data.

**A list of values:**

```python
# tests/transforms/test_scores.py
@pytest.mark.parametrize("marker", [" NA ", "na", "Na", "n/a", "N/A", "NR"])
def test_unknown_markers_any_case(marker: str) -> None:
    assert scores([marker]) == ([None], [["unknown_marker"]])
```

That's six tests. When one fails, pytest names the input:
`test_unknown_markers_any_case[n/a]`.

**Input and expected output together, with readable ids:**

```python
# tests/pipeline/test_fetch_rules.py
@pytest.mark.parametrize(
    ("delisted", "manifest_size", "exceeds"),
    [
        (25, 40, False),
        (26, 40, True),
        (470, 9400, False),
        (471, 9400, True),
        ...
    ],
    ids=["at-the-floor", "over-floor-and-percent",
         "exactly-5-percent-of-full-site", "over-5-percent-of-full-site", ...],
)
def test_delisting_limit(delisted: int, manifest_size: int, exceeds: bool) -> None:
```

Each pair of rows sits either side of a boundary (25 vs 26, 470 vs 471).
Bugs collect at boundaries, especially `<` against `<=`, so test values
just below, on, and just above every threshold. That's *boundary-value
analysis*.

**`pytest.param` to label each case where it's written:**

```python
# tests/parsers/test_bls.py
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("{", id="truncated"),
        pytest.param(json.dumps({"status": "REQUEST_SUCCEEDED"}), id="no-results"),
        # ...two more
    ],
)
def test_rejects_what_isnt_an_answer(text: str) -> None:
    with pytest.raises(EconomicParseError):
        parse_bls(text, SERIES)
```

**A table of named cases**, for inputs with many fields. `test_fetch_rules.py`
builds a dict from case name to a small `Case` dataclass, where each field
has a default, so a case lists only what's different about it:

```python
CASES = {
    "not-in-manifest": Case(stored=None, expected=FetchReason.NEW),
    "edited-exactly-day-before": Case(
        lastmod=T - DAY, now=T + DAY, expected=FetchReason.LOOKBACK
    ),
    "unchanged": Case(),
    ...
}

@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_select_reason(case: Case) -> None:
```

The rules for which reviews to fetch are easiest to read as a table, and
here the test file is that table.

**When not to parametrize:** when cases need different setup or different
assertions. Squeezing them into one function means `if` statements inside
the test, and a test with branches is hard to trust. Write separate tests
instead.

## 7. Property-based tests

The tests above check examples: this input gives that output. A
*property-based* test states a rule that holds for **every** input, and the
[Hypothesis](https://hypothesis.readthedocs.io/) library generates inputs to
try to break it.

```python
# tests/transforms/test_roasters.py
@given(st.text())
def test_key_is_idempotent(name: str) -> None:
    assert roaster_key(roaster_key(name)) == roaster_key(name)
```

`st.text()` is a *strategy*, a recipe for random values. Hypothesis runs the
test about a hundred times with strings you'd never think of: empty strings,
combining accents, mathematical letters, right-to-left text.

That test found a real bug. Hypothesis turned up `":ß"`, a name whose key,
keyed again, came out different. The fix was to casefold names before
anything else. When a property
fails, Hypothesis *shrinks* the input to the smallest one that still fails,
then saves it and tries it first on later runs.

Common kinds of property:

- **Idempotent:** doing it twice is the same as doing it once
  (`test_key_is_idempotent`).
- **Invariant:** something is always true of the output. Every digit
  survives (`test_value_shape_keeps_every_digit`), or a corrected Agtron
  reading is a two-digit prefix of the original
  (`test_corrections_are_two_digit_prefixes`).
- **Equivalence:** two inputs that should be treated alike are
  (`test_key_ignores_case`, using letters and digits only, because `"ß"` has
  no single upper-case form).
- **Round trip:** decoding what you encoded gives back the original.

Strategies can be narrowed and combined:
`st.integers(min_value=151, max_value=9999)`, `st.lists(st.tuples(...),
max_size=50)`, and `.map(...)` to change each value, such as moving a date to
the 1st of its month. `@settings(max_examples=25)` caps the number of runs
when each one is slow. `test_roast.py` checks a batch of up to 50 readings in
one Polars frame per example, because building a frame has a fixed cost.

Use properties *alongside* examples, not in place of them. An example
pins one exact answer. A property covers inputs you didn't think of, but
usually can't say what the exact answer is.

## 8. Checking bigger outputs, errors and logs

### Snapshot tests

Some outputs are too wide to assert field by field: a cleaned row with thirty
columns, or a section of the Markdown report. A *snapshot* test stores the
expected output and compares against it. This project uses
[inline-snapshot](https://15r10nk.github.io/inline-snapshot/), which writes
the expected value into the test file itself:

```python
# tests/pipeline/test_clean.py
def test_end_to_end_snapshot() -> None:
    ...
    assert row == snapshot(
        {
            "slug": "modern-roasted",
            "roaster": "1980 CAFE",
            "review_month": date(2024, 11, 1),
            "rating": 93,
            ...
        }
    )
```

Snapshots are cheap to write and catch any change. That's also the risk:
when one fails, it's tempting to accept the new output without reading it.
Read every snapshot diff as carefully as a code change. The rest of the
suite should still pin the important rules in small, named tests, so a
snapshot is never the only test of something. (Here, snapshots are updated
by editing the expected value by hand.)

A related idea is the **golden file**. `test_data_dictionary.py` regenerates
`docs/data-dictionary.md` from the models and fails if the committed file is
different. The failure message says how to fix it:

```python
assert actual == expected, (
    "docs/data-dictionary.md is out of date: "
    "run `uv run python scripts/generate_data_dictionary.py`"
)
```

A failure message like that, saying what to do next, is worth writing
whenever the fix isn't obvious.

### Errors

`pytest.raises` passes only if the code inside it raises that exception.
`match=` also checks the message, as a regular expression:

```python
# tests/pipeline/test_clean.py
with pytest.raises(UnloggedNullsError, match="a: rating"):
    ...
```

Check the message when it's meant for a person, so the test proves it names
the right review and field.

### Logs

The `caplog` fixture captures log records, for behaviour whose only visible
effect is a log line:

```python
# tests/pipeline/test_parse.py
def test_summary_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="coffee_review")
    ...
    assert "parse finished: 1 pages · 1 roasted" in caplog.text
```

## 9. Configuration, markers and async

From `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = ["--strict-markers", "-m", "not network"]
markers = [
    "slow: takes more than a second or two",
    "network: talks to a real external service; never run in CI",
]
```

- **Markers** label tests so they can be picked or left out. `-m "not
  network"` in `addopts` leaves out `@pytest.mark.network` tests unless you
  ask for them with `uv run pytest -m network`. The one such test makes a
  single real request, to check the site still looks the way the parser
  expects.
- **`--strict-markers`** turns a misspelt marker into an error. Without it,
  `@pytest.mark.netwrok` would quietly run a live test in CI.
- **`asyncio_mode = "auto"`** (from pytest-asyncio) runs `async def` tests in
  an event loop, so they need no decorator.

Coverage is measured with `uv run pytest --cov`, with `branch = true`, so
each side of every `if` has to run, not just each line. Coverage tells you
what *ran*, not what was *checked*. A test with no assertion still adds
coverage. Use it to find untested code, not as proof that code is tested.

## 10. Organisation

- **`tests/` mirrors `src/coffee_review/`.** `src/coffee_review/clients/http.py`
  is tested in `tests/clients/test_http.py`, so you can find a module's tests
  without searching. Every test folder has an `__init__.py`, so tests can import
  shared helpers as `from tests.fakes import ...`.
- **Inside a file, tests are grouped by behaviour**, under comment headings.
  `test_roasters.py`, for example, runs from the matching key, to the name
  map, to the lookup tables. A test file should read like a description of
  what the module promises.
- **Shared doubles and helpers** go in `tests/fakes.py`,
  `tests/economic_samples.py` and `tests/transforms/helpers.py`. Shared
  fixtures go in `tests/conftest.py`, and data files in `tests/fixtures/`.
- **Constants** with names such as `T`, `OLD`, `FIRST_RUN` and `DAY` keep
  dates readable. `lastmod=T - HOUR, now=T + DAY` reads as a story.
- **Test the public interface.** Private functions (with a leading `_`) are
  normally covered through the public ones. Test one directly only when it's
  complex and hard to reach. If you keep wanting to, it should probably be
  public. [conventions.md](../conventions.md#what-to-test) has the full rule.
- **Deleting a test is fine.** A test that repeats another, or only checks
  that Python works, costs reading time and adds nothing.

## Checklist for a new test

1. Is the name the behaviour, with no "and"?
2. Did you see it fail, for the right reason?
3. Is the input the smallest that shows the behaviour?
4. Does it check the result, not how the code got there?
5. No network, real clock, real sleep or developer settings?
6. If it uses a fake, is the fake held to a contract test?
7. Would a boundary value, a parametrized table or a property catch more?

## Further reading

- Gerard Meszaros, *xUnit Test Patterns*: where the double names come from.
- Martin Fowler, ["Mocks Aren't Stubs"](https://martinfowler.com/articles/mocksArentStubs.html):
  checking state versus checking interactions.
- The [pytest docs](https://docs.pytest.org/) on fixtures and
  parametrization.
- The [Hypothesis docs](https://hypothesis.readthedocs.io/), especially
  "What you can generate and how".
