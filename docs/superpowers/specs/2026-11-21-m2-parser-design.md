# M2 Parser: Design

Date: 2026-11-21
Status: approved by Tyler, 2026-11-21 (builder check done)
Parent: [pipeline roadmap design](2026-11-20-pipeline-roadmap-design.md), milestone M2.
Input: the raw layer from M1 (9,440 saved review pages behind `RawStore`).

## Intent

Turn every saved review page into a structured record, so the later stages
(clean, enrich, dashboard, ML) never touch HTML again.

Tyler knows from v1 that pages vary: some have slightly different structure,
some fields go by different names, and some fields exist only on some pages.
The parser has to be **ready for variation it hasn't seen yet**. That means:

- it finds fields under every name they have used;
- it never guesses a value;
- it never silently drops a field it doesn't recognise;
- it reports what it found, so new variation is visible straight away.

This stage *finds* values. It doesn't *interpret* them: turning
`"$34.00/12 ounces"` into numbers is M3's job.

## Goals

- `coffee-review parse` reads every saved page and writes two parsed
  datasets as Parquet: **roasted** (espresso included) and **green**.
- Every field the site has used is found under its canonical name. Unknown
  labels and headings are kept, not dropped.
- A run never stops because of one bad page. Problems are recorded as issues.
- Every run writes a **report**:
  - field coverage;
  - unknown labels and headings;
  - the issues found;
  - a **value-shape summary** per field, to design M3 from.
- Each layout variant is pinned by a test with a trimmed sample page.

## Non-goals

- **Typing or interpreting values.** No splitting prices, no parsing dates,
  no turning scores into numbers. That's M3; values stay as text here.
- **Incremental parsing.** Parsing every page takes well under a minute
  locally, so every run re-parses everything.
- **Classifying espresso separately.** Espresso reviews stay in the roasted
  dataset, and `with_milk` tells them apart. A `kind` column can come in M3 if
  it turns out to be needed.
- **Fetching anything.** Parse reads only the raw store.

## Anti-goals

- A guessed or invented value. A missing field is `None`.
- A label or heading the parser doesn't know silently disappearing.
- One malformed page aborting the run.
- The parser reading files or HTTP directly. `parsers/` stays pure, and only
  `storage/` knows formats and paths.

## What the survey found (2026-11-21, 8,524 then 9,440 pages)

- **One page template from 1997 to 2026,** with these regions:
  - `div.review-template`, which also carries the class
    `green-coffee-review` on green reviews;
  - `span.review-template-rating`, the score;
  - `p.review-roaster` and `h1.review-title`;
  - a logo link in `div.col-3 a[href]`, the roaster's website;
  - one or more `table.review-template-table`, with `<td>Label:</td><td>value</td>`
    rows;
  - `h2` sections, each followed by paragraphs until the next `h2`.
- **The HTML is slightly malformed even on new pages,** for example
  `<td>9 </tr>` with no `</td>`. A parser that handles HTML the way browsers
  do (selectolax, which uses Lexbor) copes with this.
- **Labels and how often they appear:**

  | Label | Share of pages |
  |---|---|
  | Review Date | 100% |
  | Agtron | 99.5% |
  | Roast Level | 99.5% |
  | Roaster Location | 99.4% |
  | Body | 99.3% |
  | Flavor | 99.3% |
  | Coffee Origin | 98.7% |
  | Aroma | 98.7% |
  | Aftertaste | 97.3% |
  | Est. Price | 87.0% |
  | Acidity/Structure | 49.3% |
  | Acidity | 35.5% |
  | With Milk | 13.9% |
  | Estimated Price | 0.4% |
  | Harvested, Seller, Variety, Lot Size, Processing Method, Lot # or ID (each) | 0.4% |

- **Headings:**
  - *Blind Assessment* and *Notes* are on every page.
  - *Bottom Line* (61.6%) and *Who Should Drink It* (37.7%) never appear
    together. *Who Should Drink It* is used through 2015, the two are mixed in
    2016, and only *Bottom Line* is used from 2017.
  - *Explore Similar Coffees* and *Primary Sidebar* belong to the site's
    navigation, not the review.
- **Green reviews (builder check, all 9,440 pages):**

  | Pages | Green class? | Title says "– Green"? | Fields |
  |---|---|---|---|
  | 110 | yes | 70 yes, 40 no | Review Date, Coffee Origin, Estimated Price; on the 33 newer ones, also the six lot/seller/harvest fields. Never Roaster Location, Aroma, Acidity, Body, Flavor or Aftertaste. |
  | 42 | no | yes | 1997–1999, scored like roasted reviews (Roast Level, Agtron, Aroma, Body, …): green coffees the panel roasted and cupped |

  "Green" means two different things on the site: its own green review
  layout (the class), and a coffee sold green but cupped like any roasted
  coffee (the title only).

## Decisions made with Tyler

- **Approach A:** one template-driven parser. Values come out as text and are
  typed later, in M3 (see *Approaches*).
- **Green reviews get their own parsed dataset,** a separate set of Parquet
  tables.
- **The site's class decides which dataset a page goes in:** 110 green. The 42
  title-only "– Green" reviews go in **roasted**, with `sold_green = true`,
  keeping all their cupping scores as normal columns (Tyler, after the builder
  check).
- **`Acidity` and `Acidity/Structure` are the same field.**
- **`Bottom Line` and `Who Should Drink It`:** Tyler didn't know. The survey
  shows a clean rename in 2016, so they map to one field (*my call, from the
  data*).
- **The report includes a value-shape summary** for M3.

## Approaches considered

- **A. One template-driven parser, text out, typed later. (Chosen.)** It
  matches the data, which uses one template, and it matches the roadmap's
  layers. A failure to *find* a field stays separate from a failure to
  *interpret* it, and fixing a value format means re-running M3, not
  re-parsing HTML.
- **B. A separate parser per era of the site.** Rejected: the survey found no
  structural eras, so this would be more code for variation that isn't there.
- **C. Parse and type in one step.** Rejected: it merges "missing" with
  "strange format", and every format fix would mean re-parsing every page.

## Design

### Modules

These follow the dependency rule and the storage boundary
(`cli → pipeline → {parsers, storage} → {models, config}`).

| Module | Job | I/O? |
|---|---|---|
| `parsers/review_page.py` | HTML → `ParsedReview` and any issues, for one page | no |
| `parsers/field_aliases.py` | the alias table: page labels and headings → canonical names | no |
| `parsers/value_shape.py` | `value_shape(text) -> str` | no |
| `models.py` (additions) | `ParsedReview`, `ParseIssue`, `ParseReport`, `ParseSummary` | no |
| `storage/parsed_store.py` | `ParsedStore` Protocol, Parquet implementation, `open_parsed_store` | files |
| `storage/raw_store.py` (change) | `read_page` validates its slug against `SLUG_PATTERN` | files |
| `pipeline/parse.py` | read the manifest and pages, parse, build the report, write | wires |
| `cli.py` | `coffee-review parse` | no logic |

### Which pages are parsed

- **Every manifest row with a `fetched_at`,** meaning a saved copy exists,
  whatever its `status`. A page whose last re-fetch failed still has a good
  copy on disk.
- **Delisted reviews are included,** with `delisted = true`.
- **Rows without `fetched_at` are skipped,** because there's nothing to parse,
  and the report counts them.

### Reading a page (`parsers/review_page.py`)

`parse_review_page(slug, url, html, *, delisted) -> ParsedPage`. It's pure,
and it returns the record together with that page's issues.

1. **Find the template:** `div.review-template`. If it's missing, return no
   record and the issue `no_review_template`.
2. **Decide the dataset:** green if the template div has the class
   `green-coffee-review`, otherwise roasted. The class is the site's own
   classification; the title is not a cross-check, because the two
   legitimately disagree (see the survey).
   - For roasted pages, `sold_green` is true when the title marks the coffee
     green: `Green` as a word after a dash ("– Green", "- Green by"),
     case-insensitive.
   - Disagreements are therefore not issues.
3. **Fixed regions:**
   - `rating` from `span.review-template-rating`;
   - `roaster` from `p.review-roaster`;
   - `title` from `h1.review-title`;
   - `roaster_url` from the `href` of the first `a` in `div.col-3`.
4. **Table rows:** each `tr` in every `table.review-template-table`. The first
   cell is the label and the second is the value.
   - **Labels are normalised:** trimmed, trailing colon removed, whitespace
     collapsed, compared without regard to case. Then they're looked up in the
     alias table.
   - **A known label** sets its canonical field.
   - **An unknown label** goes into `extra`, keyed by the label as it appeared.
   - **A label that's known but doesn't belong to this dataset** (a green-only
     field on a roasted page) also goes into `extra`.
   - **A repeated label** keeps its first value, and the issue
     `duplicate_label` is recorded.
5. **Sections:** each `h2` inside the template's container, up to the end of
   the review (not the sidebar). The text runs until the next `h2`.
   - Headings are mapped through the alias table.
   - Ignored headings (*Explore Similar Coffees*, *Primary Sidebar*) end the
     review; nothing after them is parsed.
   - An unknown heading goes into `extra` with the key `section: <heading>`.
6. **Text cleaning, for every value:**
   - tags removed and HTML entities decoded (`&#8211;` becomes `–`);
   - whitespace collapsed;
   - paragraphs within a section joined with a blank line;
   - an empty string becomes `None`.
7. **Required fields:** `title`, `review_date` and `rating`. If one is
   missing, the record is still kept with `None` there, and the issue
   `missing_required` is recorded.

### The alias table (`parsers/field_aliases.py`)

This is the one place that knows every name a field has had. Its first
version comes from the survey above:

| Canonical field | Page labels / headings | Datasets |
|---|---|---|
| `review_date` | Review Date | both |
| `roaster_location` | Roaster Location | roasted |
| `coffee_origin` | Coffee Origin | both |
| `roast_level` | Roast Level | roasted |
| `agtron` | Agtron | roasted |
| `price` | Est. Price · Estimated Price | both |
| `aroma` | Aroma | roasted |
| `acidity` | Acidity/Structure · Acidity | roasted |
| `body` | Body | roasted |
| `flavor` | Flavor | roasted |
| `aftertaste` | Aftertaste | roasted |
| `with_milk` | With Milk | roasted |
| `harvested` | Harvested | green |
| `seller` | Seller | green |
| `variety` | Variety | green |
| `lot_size` | Lot Size | green |
| `processing_method` | Processing Method | green |
| `lot_id` | Lot # or ID | green |
| `blind_assessment` | *Blind Assessment* (heading) | both |
| `notes` | *Notes* (heading) | both |
| `bottom_line` | *Bottom Line* · *Who Should Drink It* (headings) | both |

The first full parse's report finalises this table. New aliases are added
here, each one with a test.

*My call (from the builder check's data):* the cupping fields and Roaster
Location are **roasted** only, because no green-layout page has ever used
them. If one ever appears on a green page, it goes to `extra`, so nothing is
lost, and the report shows it.

### Datasets and tables

All values are text (Polars `Utf8`), apart from `delisted` and `sold_green`
(`Boolean`) and `extra_count` (`Int32`).

| Dataset | Path | Columns |
|---|---|---|
| roasted | `data/parsed/roasted/reviews.parquet` | `slug, url, delisted, sold_green, title, roaster, roaster_url, rating, review_date, roaster_location, coffee_origin, roast_level, agtron, price, aroma, acidity, body, flavor, aftertaste, with_milk, blind_assessment, notes, bottom_line, extra_count` |
| roasted | `data/parsed/roasted/extra_fields.parquet` | `slug, label, value` |
| green | `data/parsed/green/reviews.parquet` | `slug, url, delisted, title, roaster, roaster_url, rating, review_date, coffee_origin, price, harvested, seller, variety, lot_size, processing_method, lot_id, blind_assessment, notes, bottom_line, extra_count` |
| green | `data/parsed/green/extra_fields.parquet` | `slug, label, value` |
| — | `data/parsed/parse_issues.parquet` | `slug, kind, detail` |
| — | `data/parsed/report.md` | the human-readable report (below) |

- **Every run replaces all of these files.** Each one is written to a temp
  file and then moved into place, so a crash never leaves half a table.
- **Rows are sorted by slug,** so two runs over the same pages produce the
  same files.
- **`ParsedStore` is the only code that knows these paths and formats**
  (ADR 0006's rule). The pipeline hands it records, issues and the report.

### The report

`ParseReport`, built in the pipeline from all records and issues. It's
printed briefly at the end of a run and written in full to `report.md`.

- **Totals:** pages read, roasted, green, skipped (no saved copy), and issues
  by kind.
- **Coverage per dataset:** for each canonical field, how many records have
  it, and the percentage.
- **Unknown labels and headings:** each with a count and up to 3 example
  slugs.
- **Value shapes:** for each short field (every table field, plus `rating`
  and `roaster_url`), the 10 most common shapes, each with a count and an
  example slug and value.
  - `value_shape` turns each digit into `9` and each letter into `a`, and
    keeps punctuation and spaces. For example, `$34.00/12 ounces` becomes
    `$99.99/99 aaaaaa`.
  - Long text sections are left out.
- **Issues:** every issue, grouped by kind.

### CLI

`coffee-review parse`:

- **Output:** progress uses the same Rich bar as `scrape`. The run ends with a
  summary line, for example:
  ```
  parse finished: 9,440 pages · 9,330 roasted (42 sold green), 110 green · 7 issues · 3 unknown labels · in 24s · report: data/parsed/report.md
  ```
- **Exit codes:** 0 when it finished, issues included; 1 on an error, such as
  an unreadable manifest or a write failure.
- **Logs:** a run log in `data/logs/parse-<UTC>.log`, as for scrape.

## Testing

pytest skills introduced in M2, from the roadmap:

- a corpus of saved HTML fixtures;
- `@pytest.mark.parametrize` over it;
- snapshot tests with **inline-snapshot** (ADR 0004);
- `pytest.raises`;
- checking recorded issues.

**The fixture corpus** lives in `tests/fixtures/pages/`, one file per variant.
Each is cut down to the review template (no site navigation), and the review
prose is replaced with short placeholder sentences, so the public repo doesn't
republish reviews. Labels, structure and short values are kept as they are.
The variants, at least:

1. a modern roasted review (`Acidity/Structure`, `Bottom Line`);
2. an older roasted review (`Acidity`, `Who Should Drink It`);
3. an espresso review (`With Milk`, no acidity);
4. a 1998–2001 review with neither closing heading;
5. an old green-layout review (table with only `Review Date`);
6. a new green-layout review (lot, seller and harvest fields), whose title
   doesn't say "Green";
7. a 1997–1999 roasted review titled "– Green" (`sold_green = true`);
8. a page with the unclosed-`td` quirk;
9. a synthetic page with no review template;
10. a synthetic page with an unknown label and an unknown heading, plus a
    roasted-only label on a green-layout page.

**What gets tested:**

- **Parser:** one snapshot test parametrized over the corpus (record plus
  issues), plus focused tests for:
  - label normalisation;
  - each alias;
  - the extra-field rules;
  - text cleaning;
  - required fields.
- **`value_shape`:** a parametrized table, plus a hypothesis property: the
  output always has the same length as the input, and contains no digits or
  letters other than `9` and `a`.
- **`ParsedStore`:** written in `tmp_path` and read back with Polars. The
  schemas are exactly as in the table above, writes are atomic, and output is
  sorted by slug.
- **Pipeline:**
  - built from `InMemoryRawStore` plus an in-memory parsed store, with fakes
    as in M1;
  - pages without `fetched_at` are skipped;
  - delisted pages are flagged;
  - one bad page doesn't stop the run;
  - the report counts are right.
- **CLI:** exit codes and the summary, with the pipeline replaced by a spy.

## Done when

- Every fixture variant parses as its snapshot says.
- `coffee-review parse` over the real 9,440 pages finishes.
- Tyler has read the report, and every issue and unknown label in it is either
  fixed (an alias added, with a test) or understood and noted.
- The parsed-record contract (this document's table) matches the final alias
  table.
- The roadmap is updated, and there's an ADR if a decision made here deserves
  one (e.g. text-out / typed-later).

## Left to the builder

- selectolax selector details, and how to tell the review's `h2`s from the
  sidebar's, as long as the ignored headings end the review.
- The exact wording of the report and the order of its sections.
- Internal helper names.
- Whether `ParseReport` is built with a pass over the records or with Polars,
  as long as `parsers/` stays pure.
