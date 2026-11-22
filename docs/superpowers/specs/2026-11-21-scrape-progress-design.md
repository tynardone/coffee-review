# Scrape progress bar: design

Date: 2026-11-21 · Status: approved by Tyler in conversation (Rich chosen over tqdm)

## Intent

A full scrape takes about 31 minutes, and until now it ran with no sign of
progress until the summary at the end. A progress bar shows how far through it
is, how fast it's going, how long is left, and whether pages are failing, while
the run is still going.

## Decisions

- **Rich, not tqdm.** Rich is already installed (Typer depends on it), it can
  share the terminal with log lines, and it matches how Typer draws `--help`
  and errors. No new dependency.
- **The pipeline reports progress; the CLI draws it.**
  - `pipeline/progress.py` defines a `ScrapeProgress` Protocol with
    `start(total)`, `advance(outcome)` and `finish()`, plus `NoProgress`, which
    does nothing and is the default.
  - `run_scrape(..., progress=...)` calls it. `pipeline/` imports no display
    library, just as it imports no storage library.
  - `progress_display.py` holds `RichScrapeProgress`, the Rich
    implementation. The CLI builds it.
- **What gets reported**
  - `start(total)` is called once the reviews to fetch are chosen (after
    `--limit`), so the total is real.
  - `advance` is called after each page's result is recorded, as `saved`,
    `failed` or `gone`.
  - A pushback result doesn't advance: that page wasn't processed, and the run
    is stopping.
  - `finish()` is always called, error or not, before the summary is logged,
    so the summary prints below the finished bar.
- **The bar shows** the counts (`4,120/9,412`), percentage, pages per second,
  elapsed time, time left, and running saved / failed / gone counts.
- **No bar when stderr isn't a terminal** (piped to a file, run on a
  schedule). Logs and the summary are unchanged.
- **Log lines appear above the bar.** The terminal log handler becomes a
  `RichHandler` on the same Rich console, showing plain messages as before.
  The log file is unchanged.

## Testing

- **Pipeline:** a recording fake checks the total (with and without
  `--limit`), one `advance` per recorded page with the right outcome, no
  `advance` for pushback, and `finish` called on every path, including errors.
- **Display:** a Rich `Console` writing to a string checks the counts, and
  that nothing is drawn when the console isn't a terminal.
- **Logging:** with a console, messages go through it.
- **CLI:** `scrape` is given a progress display.
