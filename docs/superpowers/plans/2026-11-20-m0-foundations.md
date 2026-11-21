# M0 Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing skeleton trustworthy: it imports, its settings load, its HTTP plumbing is tested, the CLI runs, every check passes locally, and the project's working docs exist. No new pipeline features.

**Architecture:** No new packages. Fix `config.py` and `clients/forex_api.py`, add a `tests/` tree mirroring `src/`, wire a Typer app in `cli.py` as the entry point, add a pre-commit config and GitHub Actions workflow, and write `README.md`, `docs/roadmap.md`, `docs/conventions.md`, `docs/decisions/`.

**Tech Stack:** Python 3.14, uv 0.13, pydantic-settings, httpx, aiolimiter, Typer, pytest + pytest-asyncio + pytest-cov, ruff, mypy (with the pydantic plugin), pre-commit, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-20-pipeline-roadmap-design.md` (section "M0. Foundations", plus "How we work", "Structure", "Tooling", "pytest as a through-line").

## Global Constraints

- Python `>=3.14`; dependencies added with `uv add` / `uv add --dev` so `uv.lock` stays in sync.
- **Each task ends with a pause for Tyler's review.** Claude writes the change, explains what changed, why, and what to look at closely, and commits only after Tyler has reviewed it and their questions are answered. One concern per commit.
- No secrets and no scraped data in git. `.env` and `data/` stay ignored.
- Env var prefix is `COFFEE_REVIEW_`, nested delimiter `__`.
- Scraper defaults: `rate_per_sec = 1.0`, `max_concurrency = 2`.
- User-Agent is a placeholder until the public repo exists: `coffee-review/0.1 (personal research project; repository link to follow)`. No email address in code.
- Tests never touch the network. Tests marked `network` are deselected by default.
- `NOTES.md` is Tyler's working file: don't edit it in this milestone.
- Every task ends with `uv run pytest`, `uv run ruff check`, `uv run ruff format --check` passing. `uv run mypy src` must pass from Task 6 on.

## Review Focus

1. **A local `.env` leaking into tests.** A developer's real `.env` must not change test results. → autouse fixture in Task 1 clears `COFFEE_REVIEW_*` env vars, and settings tests pass `_env_file=None` (Task 2).
2. **Running a command that doesn't need the FX key, with no key set.** `coffee-review --help` and `Settings()` must work with an empty environment. → `test_settings_load_with_empty_environment` (Task 2), `test_help_works_without_env` (Task 5).
3. **A server sending a huge `Retry-After`.** The wait must be capped at `backoff_max_seconds`, not hang for an hour. → `test_retry_after_is_capped` (Task 4).
4. **A 4xx response.** It must raise at once with no retries, or a missing page costs six requests. → `test_client_error_is_not_retried` (Task 4).
5. **Caller params colliding with the FX auth param.** Caller params must be kept and the key always sent. → `test_caller_params_are_merged_with_app_id` (Task 3).

---

### Task 1: pytest harness

Teaches: test layout mirroring `src/`, `conftest.py`, autouse fixtures, markers, coverage.

**Files:**
- Modify: `pyproject.toml` (dev deps, `[tool.pytest.ini_options]`, `[tool.coverage.*]`)
- Create: `tests/conftest.py`, `tests/test_package.py`

**Interfaces:**
- Produces: autouse fixture `clean_env` (removes every `COFFEE_REVIEW_*` env var via `monkeypatch`); fixture `recorded_sleeps() -> list[float]` that monkeypatches `asyncio.sleep` in `coffee_review.clients.http` with an async stub appending the delay and returning immediately.

- [ ] **Step 1:** `uv add --dev pytest pytest-asyncio pytest-cov`
- [ ] **Step 2:** In `pyproject.toml` add:
  ```toml
  [tool.pytest.ini_options]
  testpaths = ["tests"]
  asyncio_mode = "auto"
  addopts = ["--strict-markers", "-m", "not network"]
  markers = [
      "slow: takes more than a second or two",
      "network: talks to a real external service; never run in CI",
  ]

  [tool.coverage.run]
  source = ["coffee_review"]
  branch = true
  ```
- [ ] **Step 3:** Write `tests/test_package.py::test_package_imports` asserting `import coffee_review` succeeds. Write `tests/conftest.py` with the two fixtures above.
- [ ] **Step 4:** Run `uv run pytest -v` → 1 passed. Run `uv run pytest --cov` → coverage table prints.
- [ ] **Step 5:** Pause for review, then commit: `test: add pytest harness and shared fixtures`

### Task 2: Settings that load

Fixes the import crash (`HttpUrl("www.base")`), makes every section default-constructible, renames the env prefix, rewrites `.env.example`.

**Files:**
- Modify: `src/coffee_review/config.py`, `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `HttpClientSettings.user_agent` default = the placeholder in Global Constraints.
  - `CoffeeSiteSettings`: `base_url = "https://www.coffeereview.com/"`, `sitemap_url = "https://www.coffeereview.com/sitemap_index.xml"`, `rate_per_sec = 1.0`, `max_concurrency = 2`.
  - `ForexApiSettings`: `base_url = "https://openexchangerates.org/api/"`, `api_key: SecretStr | None = None`.
  - `BLSApiSettings`: `base_url = "https://api.bls.gov/publicAPI/v2/"`, `rate_per_sec = 0.5`, `max_concurrency = 1`.
  - `Settings`: `env_prefix="COFFEE_REVIEW_"`; `coffee_site`, `forex_api`, `bls_api` each with `default_factory`; `log_level: str = "INFO"`.
  - `get_settings() -> Settings` unchanged (cached).

- [ ] **Step 1: Write failing tests** in `tests/test_config.py` (always construct with `Settings(_env_file=None)`):
  - `test_settings_load_with_empty_environment`: no exception; `settings.forex_api.api_key is None`.
  - `test_coffee_site_defaults_are_polite`: `rate_per_sec == 1.0`, `max_concurrency == 2`, `str(sitemap_url) == "https://www.coffeereview.com/sitemap_index.xml"`.
  - `test_nested_env_override(monkeypatch)`: set `COFFEE_REVIEW_COFFEE_SITE__RATE_PER_SEC=0.25` → `0.25`.
  - `test_api_key_read_from_env_and_hidden(monkeypatch)`: set `COFFEE_REVIEW_FOREX_API__API_KEY=abc` → `get_secret_value() == "abc"` and `"abc" not in repr(settings)`.
  - `test_invalid_value_rejected(monkeypatch)`: `..._MAX_CONCURRENCY=0` → `pytest.raises(ValidationError)`.
- [ ] **Step 2:** `uv run pytest tests/test_config.py -v` → FAIL (import error from the bad URLs).
- [ ] **Step 3:** Update `config.py` to the interface above, and fix its module docstring (it still says `myproject`, `weather_api`). Rewrite `.env.example` with `COFFEE_REVIEW_FOREX_API__API_KEY=replace-me` plus commented examples of `COFFEE_REVIEW_COFFEE_SITE__RATE_PER_SEC` and `COFFEE_REVIEW_LOG_LEVEL`.
- [ ] **Step 4:** `uv run pytest -v` → all pass.
- [ ] **Step 5:** Pause for review, then commit: `fix: make settings load and use project env prefix`

### Task 3: Forex client auth

OpenExchangeRates takes the key as `app_id`. Also fixes the mypy error: `BaseHttpClient` has no `get`.

**Files:**
- Modify: `src/coffee_review/clients/forex_api.py`
- Test: `tests/clients/test_forex_api.py`

**Interfaces:**
- Consumes: `BaseHttpClient.request(method: str, path: str, **kwargs) -> httpx.Response`; `ForexApiSettings` from Task 2.
- Produces: `class MissingApiKeyError(Exception)`; `ForexApiClient(settings, *, transport=None)` raises it when `settings.api_key is None`; `ForexApiClient.request(...)` override that merges `{"app_id": key}` into `params`, caller params kept, `app_id` always taking the configured value.

- [ ] **Step 1: Write failing tests** using `httpx.MockTransport` that captures the request:
  - `test_app_id_sent_as_query_param`: `request.url.params["app_id"] == "test-key"`.
  - `test_caller_params_are_merged_with_app_id`: call with `params={"base": "USD"}` → both `base` and `app_id` present.
  - `test_missing_key_raises`: `pytest.raises(MissingApiKeyError)` on construction with `api_key=None`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement per the interface.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Pause for review, then commit: `fix: send OpenExchangeRates key as app_id`

### Task 4: Tests for the HTTP client

No production changes expected. If a test exposes a bug, stop and raise it with Tyler before fixing.

**Files:**
- Test: `tests/clients/test_http.py`

**Interfaces:**
- Consumes: `BaseHttpClient`, `RetriesExhaustedError`, `_make_limiter`, `_parse_retry_after` from `coffee_review.clients.http`; `recorded_sleeps` fixture. Build settings as `HttpClientSettings(base_url="https://example.test/", max_retries=2, backoff_base_seconds=0.01, backoff_max_seconds=5, rate_per_sec=1000)`.

- [ ] **Step 1: Write tests** (a MockTransport handler that pops queued responses and counts calls):
  - `_parse_retry_after`, parametrized: `"5"` → `5.0`; `None`/`""` → `None`; `"garbage"` → `None`; `"-3"` → `0.0`; an HTTP-date 10 s ahead → between 8 and 10; an HTTP-date in the past → `0.0`.
  - `test_make_limiter_below_one_per_second`: `_make_limiter(0.5)` has `max_rate == 1.0`, `time_period == 2.0`.
  - `test_retries_then_succeeds`: 503, 503, 200 → returns the 200; 3 calls; `len(recorded_sleeps) == 2`.
  - `test_raises_after_retries_exhausted`: always 503 → `RetriesExhaustedError` with `attempts == 3` and `"HTTP 503"` in `last_error`.
  - `test_client_error_is_not_retried`: 404 → `httpx.HTTPStatusError`; exactly 1 call; no sleeps.
  - `test_network_error_is_retried`: handler raises `httpx.ConnectError` twice then returns 200 → succeeds.
  - `test_retry_after_is_honored`: 429 with `Retry-After: 3`, then 200 → `recorded_sleeps[0] >= 3`.
  - `test_retry_after_is_capped`: 429 with `Retry-After: 3600`, then 200 → `recorded_sleeps[0] <= 5`.
  - `test_request_before_open_raises`: `RuntimeError`.
  - `test_context_manager_closes_client`: after `async with`, `client._client is None`.
- [ ] **Step 2:** `uv run pytest tests/clients/test_http.py -v` → all pass; `uv run pytest --cov` shows `http.py` coverage ≥ 95%.
- [ ] **Step 3:** Pause for review, then commit: `test: cover retry, backoff and Retry-After in HTTP client`

### Task 5: Typer CLI entry point

**Files:**
- Modify: `pyproject.toml` (`typer` dependency; `[project.scripts] coffee-review = "coffee_review.cli:app"`), `src/coffee_review/cli.py`, `src/coffee_review/__init__.py` (remove the hello-world `main`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `app: typer.Typer` in `coffee_review.cli` with `help="Scrape, parse, clean and enrich coffeereview.com reviews."` and a `--version` option (eager callback) printing `coffee-review <version>`, version read from `importlib.metadata.version("coffee-review")`. Stage commands (`scrape`, `parse`, …) are added in their own milestones, not now.

- [ ] **Step 1:** `uv add typer`
- [ ] **Step 2: Write failing tests** with `typer.testing.CliRunner`:
  - `test_version_option`: exit 0, output contains `"coffee-review 0.1.0"`.
  - `test_help_works_without_env`: with `clean_env` active and `--help`, exit 0, output contains `"coffeereview.com"`.
- [ ] **Step 3:** Run → FAIL.
- [ ] **Step 4:** Implement; `uv sync`.
- [ ] **Step 5:** `uv run pytest -v` → PASS; `uv run coffee-review --help` and `--version` work from the shell.
- [ ] **Step 6:** Pause for review, then commit: `feat: add Typer CLI entry point`

### Task 6: Type checking, pre-commit and CI

**Files:**
- Modify: `pyproject.toml` (`[tool.mypy]`), `src/coffee_review/clients/coffee_site.py`
- Create: `.pre-commit-config.yaml`, `.github/workflows/ci.yml`

**Interfaces:**
- `[tool.mypy]`: `strict = true`, `plugins = ["pydantic.mypy"]`, `files = ["src", "tests"]`.

- [ ] **Step 1:** Add the mypy config and run `uv run mypy` to see what's flagged.
- [ ] **Step 2:** Fix what's flagged with the smallest changes. In `coffee_site.py` that means:
  - Remove the unused `headers` parameter.
  - Give `fetch_sitemap(self) -> str` and `fetch_review_page(self, slug: str) -> str` real signatures with `raise NotImplementedError` bodies.
  - Annotate `fetch_review_pages`.
  - Leave its gather/error behavior for M1 and note that in a `# TODO(M1):` comment.
  Add return types to test functions as needed.
- [ ] **Step 3:** `uv run mypy` → `Success`.
- [ ] **Step 4:** `uv add --dev pre-commit`. Write `.pre-commit-config.yaml` with:
  - `astral-sh/ruff-pre-commit`: `ruff-check --fix`, `ruff-format`.
  - Standard hooks `check-yaml`, `end-of-file-fixer`, `trailing-whitespace`.
  - A local `mypy` hook: `uv run mypy`, `pass_filenames: false`.

  Run `uv run pre-commit install` and `uv run pre-commit run --all-files` → all pass.
- [ ] **Step 5:** Write `.github/workflows/ci.yml`:
  - Triggers: push and pull_request.
  - Runner: `ubuntu-latest`.
  - Steps: `actions/checkout`, `astral-sh/setup-uv` (with caching), `uv sync --locked`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest --cov`.

  It can't be run until the remote exists, so check it with `uv run pre-commit run check-yaml --files .github/workflows/ci.yml`.
- [ ] **Step 6:** Pause for review, then commit: `ci: add strict mypy, pre-commit hooks and GitHub Actions workflow`

### Task 7: Project docs

**Files:**
- Create: `docs/roadmap.md`, `docs/conventions.md`, `docs/decisions/0001-record-decisions.md`, `docs/decisions/0002-src-layout-and-dependency-rule.md`, `docs/decisions/0003-stage-by-stage-build.md`, `docs/decisions/0004-tooling.md`
- Modify: `README.md`, `pyproject.toml` (`description`)

**Content each must carry:**
- [ ] **Step 1: `docs/roadmap.md`.**
  - Milestones M0–M5 as checkbox lists, using the steps and "Done when" lines from the spec, with M0's items checked as completed.
  - "CI green on GitHub" left unchecked with the note "after remote is created".
  - M6–M8 listed as later.
  - An empty `## Backlog` section.
- [ ] **Step 2: `docs/conventions.md`.** The spec's package list, dependency rule, data layer names and naming conventions, plus test conventions:
  - `tests/` mirrors `src/`.
  - Test names read `test_<behavior>`.
  - No network in tests.
  - Fixtures live in `tests/fixtures/`.
- [ ] **Step 3: Decision records**, each with the sections Context, Decision, Why, Alternatives considered, taken from the spec's "Approaches considered", "Structure" and "Tooling" sections. 0004 covers the whole tooling table in one record.
- [ ] **Step 4: `README.md`.**
  - What the project is (two sentences).
  - The four pipeline stages.
  - Setup: `uv sync`, copy `.env.example` to `.env`.
  - The check commands.
  - Links to the roadmap, conventions and decisions.

  Set the `pyproject.toml` `description` to `"Scrape, parse, clean and enrich coffeereview.com reviews."`.
- [ ] **Step 5:** `uv run pre-commit run --all-files` → pass.
- [ ] **Step 6:** Pause for review, then commit: `docs: add roadmap, conventions and decision records`

### Done when (from the spec)

`uv run coffee-review --help` works; `uv run pre-commit run --all-files` and `uv run pytest` pass; `http.py` is tested; `.github/workflows/ci.yml` exists; the docs exist. CI going green on GitHub is checked off once Tyler creates the remote.
