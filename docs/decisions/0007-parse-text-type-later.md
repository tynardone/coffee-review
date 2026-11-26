# 0007. Parsing finds values as text; cleaning interprets them

Date: 2026-11-22 · Status: accepted

## Context

Every coffeereview.com review since 1997 uses one page template, but its
contents vary:

- fields have had different names (`Acidity` / `Acidity/Structure`,
  `Est. Price` / `Estimated Price`, and *Who Should Drink It*, which became
  *Bottom Line* in 2016);
- some fields exist only on some reviews (`With Milk` on espresso; the lot,
  seller and harvest fields on newer green reviews);
- values come in many formats (`$34.00/12 ounces`, `NT $399/227 grams`,
  `NA`; Agtron `58/77`, `0/0`, `/`; 1997 scores like `6.3`).

The first version of this project showed that pages vary, so the parser
should be ready for variation it hasn't seen yet. The clean stage turns these
values into numbers, dates and units, and its rules keep changing as odd
formats turn up.

## Decision

- **The parse stage (M2) only *finds* values,** and keeps them as cleaned text:
  tags removed, entities decoded, whitespace collapsed, blank values `None`.
  It never converts a value or guesses one.
- **The clean stage (M3) *interprets* them,** working from the parsed Parquet
  tables, not the HTML.
- **One alias table** (`parsers/field_aliases.py`) holds every name a field has
  had. A label or heading it doesn't know is kept in `extra` and reported,
  never dropped.
- **The site's `green-coffee-review` class decides the dataset:** roasted or
  green, each with its own model and its own Parquet tables. Roasted reviews
  whose title says the coffee was sold green (1997–1999) stay in roasted,
  flagged `sold_green`.
- **Every parse writes a report:** field coverage, unknown labels and headings,
  issues, and the *value shapes* of each short field (digits become `9`, each
  run of letters becomes `a`). The report is how new variation shows up, and
  it's the starting input for designing M3.

## Why

- **It keeps two kinds of failure apart.** "This page has no price" is a parse
  question; "this price string is odd" is a cleaning question. Mixing them
  would make both harder to see and to fix.
- **It makes format fixes cheap.** A new price format means changing one clean
  function and re-running M3 on a small table in seconds. Nothing has to
  re-read 9,440 HTML pages, though even that takes only about 6 seconds.
- **It's honest about what's on the page.** Text values can be traced straight
  back to the page; any interpretation happens later, in one testable place.
- **The first full parse confirmed the approach:** 9,440 pages, 0 unknown
  labels or headings, and 1 issue, which was the site's own empty score.

## Alternatives considered

- **A separate parser for each era of the site.** Rejected: the survey found
  one template throughout, so there was no structural variation to switch on.
- **Parse and convert in one step,** straight to typed records. Rejected: it
  merges "missing" with "strange format", and every format fix would mean
  parsing every page again.
- **Recognising green reviews from their fields, or from their title.**
  Rejected: most older green reviews have almost no fields, and the title marks
  42 roasted-style reviews of coffees sold green. The site's own class is the
  reliable signal.
