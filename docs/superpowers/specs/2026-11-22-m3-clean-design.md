# M3 Clean: Design

Date: 2026-11-22
Status: approved by Tyler, 2026-11-22 (builder check done)
Parent: [pipeline roadmap design](2026-11-20-pipeline-roadmap-design.md), milestone M3.
Input: the parsed layer from M2 (`data/parsed/`: roasted 9,330 rows, green 110,
every value as cleaned text).
Background: [ADR 0007](../../decisions/0007-parse-text-type-later.md) (parse
finds, clean interprets), Tyler's notes [agtron.md](../../agtron.md) and
[interpreting_reviews.md](../../interpreting_reviews.md).

## Intent

Turn the parsed text into **typed, trustworthy columns** for the dashboard
(M7) and machine learning (M8): dates, numbers, prices in parts, Agtron as two
readings, and places as country and region, so origins and roaster locations
can be **compared by region**.

M2 kept every value exactly as the page had it. M3 is where interpretation
happens, in one testable place. Every interpretation is visible: a reader can
see which rule changed which value, and why.

This is also a **data-engineering portfolio piece**, so the layers should read
as a recognisable pattern:

| Layer | Where | What | Built by |
|---|---|---|---|
| Raw (bronze) | `data/raw/` | gzipped HTML + manifest | M1, Python |
| Parsed | `data/parsed/` | one row per review, all text | M2, Python |
| **Clean (silver)** | **`data/clean/`** | **typed columns + cleaning log** | **M3, Polars** |
| Marts (gold) | later | joined to FX and CPI, dimensions | M5+, dbt revisited |

## Goals

- `coffee-review clean` reads both parsed datasets and writes clean
  **roasted** and **green** tables as Parquet, plus a **cleaning log** and a
  **clean report**.
- Every field from the value-shape summary has a rule: typed, split, or
  carried through as text.
- **Every change is recorded.** When a rule nulls, swaps, corrects or flags a
  value, a cleaning-log row says which slug, which field, which rule, and the
  original text.
- Values M3 can't interpret are **listed in the report**, so the rules can
  grow the way M2's alias table did.
- The clean tables **validate against a schema contract** (types, allowed
  values, ranges) on every run.
- Transforms have **unit tests and hypothesis property tests**.

## Non-goals

- **Currency conversion and inflation.** FX and CPI are M4 (reference data)
  and M5 (enrich). M3 splits a price into parts and never makes a network call.
- **Merging region names.** `Yirgacheffe` and `Yirgacheffe growing region`
  become the same text after light tidying, but M3 doesn't build a full
  region gazetteer. The report lists region counts, so merging can be
  designed from real data later.
- **Analysing the review text** (`blind_assessment`, `notes`, `bottom_line`).
  It's carried through for ML; M3 only removes zero-width characters.
- **dbt.** Chosen against for M3 (see Approaches); revisited for M5's
  relational modelling.
- **Incremental cleaning.** The whole parsed layer cleans in seconds.

## Anti-goals

- **A value silently disappearing.** Any non-null parsed value that becomes
  null in M3 has a cleaning-log row naming the rule.
- **A guess presented as fact.** Corrections are flagged (`agtron_swapped`,
  `agtron_corrected`), and doubtful values are flagged, not hidden
  (`agtron_suspect`).
- **Clean reading HTML or the raw layer.** M3 reads only the parsed store.
- **One odd value stopping the run.** An unparseable value becomes null with
  a log row; the run carries on.

## Decisions made with Tyler

1. **Polars for M3.** The hard part of M3 is messy string cleaning (price,
   places, Agtron), which is awkward in SQL. dbt is revisited for the later
   modelling layers, where the work is relational (joins to FX and CPI, the
   final table, dimensions).
2. **Agtron typos: correct and flag when a known pattern explains them**
   (changed 2026-11-22, see Decision 9; it was "keep and flag"). Any other
   doubtful reading is kept as written, with `agtron_suspect = true`.
3. **Agtron June 2002 – March 2003: correct and flag, per row.** In that
   window the site printed the readings the other way round (ground/whole
   bean). A row in the window whose ground reading is darker than its whole
   bean is swapped, with `agtron_swapped = true`. A row in the window left
   unswapped is flagged `agtron_suspect`. Rows outside the window are never
   swapped.
4. **Origin: country and growing region,** with as much detail as is there.
   More than one country, or a broad area or blend, becomes `"Multiple"`.
5. **Roaster location: city, region and country,** as much as is there.
6. **Hawaiʻi and Puerto Rico are their own origin countries.**
7. **Currency is inferred from the roaster when the symbol is unclear,** and
   flagged. A bare `$` is USD, except for roasters in a country with its own
   dollar (Canada, Australia, Hong Kong, Singapore, New Zealand), where it's
   that currency. `¥` follows the roaster: Japan → JPY, China → CNY. Every
   inferred currency has `price_currency_inferred = true` and is counted in
   the report.
8. **`review_month`, not `review_date`.** The site gives only month and year,
   so the clean column is a `Date` that's always the first of the month, named
   so nobody reads it as an exact day. The contract checks the day is 1.
9. **Agtron typo patterns (added during Task 5).** A reading above 150 is
   corrected to its first two digits only when one of four typing slips
   explains the extra digits: the other reading typed onto its end
   (`6489/89` → 64), the other reading's first digit slipped on (`547/74` → 54),
   the number typed twice (`5252/70` → 52), or its last digit doubled
   (`555/73` → 55). The corrected pair must then read normally in the order it
   was printed. Corrected readings get `agtron_corrected = true` and rule
   `typo_corrected`, with the original in the log. `48/689` (no pattern) and
   `43.5/47` (a half reading) stay as written and suspect.
10. **New spellings and places in future scrapes are handled, not just today's
    (added during Task 6).** A table typed once won't keep up, so:
    - **the country list is complete:** `countries.csv` holds every ISO 3166
      country and its spellings, generated by `scripts/generate_countries.py`
      (pycountry, a development dependency only), with common English names;
    - **a country inside a part is found:** as its last or first words
      (`Huila Colombia`, `Kenya Gatugi AB`) or after "of"
      (`Harrar region of eastern Ethiopia`), the other words kept as region;
    - **close spellings are matched and flagged:** when nothing matched
      exactly, a known spelling at least 80% alike (difflib; 90% before a part
      is split into words; at least 5 letters) is accepted with rule
      `close_spelling`, and the report lists each as `Calfornia → California`;
    - **the report marks what's new since the last run** (Tasks 8–10), so after
      a rescrape only new unplaced values and close spellings need a look.
      When anything is new, the run logs a **warning** with the count (it still
      succeeds: unknown values are expected and handled).
11. **Region detail keeps direction words (Task 12).** A known region, island
    or state in an origin is written with its standard name, but any direction
    words before it stay as written: `North Sumatra` (a province), `West Guji`
    (a zone), `southern Minas Gerais`. Only filler (`Zone`, `State`, `growing
    region`) is dropped. Capitals follow the page.

## What the data shows (profiled 2026-11-22)

From the parse report's value shapes and direct counts over `data/parsed/`.

- **`review_date`:** every value is `Month YYYY` (all 12 month names, 9,440 of
  9,440).
- **`rating`:** 52–98 as whole numbers (1997–99 range 60–94); 4 are `NR`.
- **The five scores (aroma, acidity, body, flavor, aftertaste):** whole
  numbers 1–10. 55 reviews from 1997–99 (37 of them sold green) use one
  decimal place (`6.3`). Odd values: `NA`, `na`, `n/a`, `NR`, and for acidity
  the words `Low`, `Very Low`, `Moderate` (9). Aftertaste is missing on 871
  older reviews. `with_milk` is whole numbers, plus 8 written as
  `Flavor in milk: 5`.
- **`roast_level`:** exactly `Light`, `Medium-Light`, `Medium`, `Medium-Dark`,
  `Dark`, `Very Dark`, and `NA` (408).
- **`agtron`** (all 9,330 roasted rows have a value):
  - 8,618 are two numbers (whole bean / ground);
  - unknown is written `/` (273), `0/…` or `…/0` (396: 0 means not
    measured), `NA/NA`, `NA/`, `38/na`;
  - **June 2002 – March 2003:** 71 readings have ground darker than whole
    bean, and about 5 (July and October 2002, February 2003) don't; Feb–May
    2002 and May–Dec 2003 are all normal (about 120 rows), and every other
    year combined has 9 of about 9,000. Four Jan–Feb 2003 pages have the
    literal text `g/wb`, the template's label for that period;
  - typo-like readings (8, found in Task 5): `6489/89`, `4254/54`, `547/74`,
    `578/80`, `5252/70`, `555/73`, `48/689`, and the half reading `43.5/47`;
  - ground above 100 is real (Light roasts, `82/104`).
- **`price`** (7,459 of 9,440 have one):
  - most are `<currency> <amount>/<quantity> <unit>`: `$13.50/12 ounces`,
    `NT $700/200 grams`;
  - about 30 currency spellings: `$`, `US$`, `NT$`, `NTD$`, `NT`, `TWD$`,
    `CAD$`, `HK$`, `HKD$`, `RMB`, `CNY`, `CN¥`, `KRW`, `THB`, `AUD$`, `AU$`,
    `RM`, `AED$`, `IDR$`, `£`, `€`, `Euros`, `Kč`, `LAK`, `GTQ`, `SGD$`, and
    `¥` (Japanese yen or Chinese yuan: 11 from Japanese roasters, 3 from a
    Shanghai one);
  - a bare `$` comes from roasters all over: Taiwan 119, Guatemala 90,
    Canada 48, China 34, and a long tail. Taiwan amounts look like USD; the
    Canadian ones could be CAD;
  - units: weights (`ounces`, `oz`, `grams`, `g`, `kg`, `kilogram`, `pound`),
    volumes (`fluid ounces`, `ml`), and counts (`k-cups`, `capsules`, `pods`,
    `single-serve brew bags`, `packets`, `cans`, `bottles`);
  - some carry a note: `grams (8.8 oz.)`, `ounces (sold only as a two-pack)`;
  - not a price: `NA` (136), `Not Available`, `See website…`, a bare `$15.00`.
- **`coffee_origin`** (8,825 of 9,330 roasted have one): mostly ends in a
  country (Ethiopia 1,780, Colombia 1,033, Kenya 735…); about 620 are
  `Not disclosed`; 203 distinct endings, including about 12 spellings of
  Hawaiʻi (`"Big Island" of Hawai’i`, `Hawaii`, `Hawai'i`…), islands for
  countries (`Sumatra`, `Java`), and broad areas (`Latin America`, `Africa`,
  `Blend`, `…and South America`).
- **`roaster_location`:** `City, State` (US), `City, Province, Canada`,
  `City, Country`, or just `Country` (`Taiwan`).
- **Green-only fields** (110 rows): `harvested` (`2024`, `April 2023`,
  `January to March 2021`), `lot_size` (`300 kilograms`, `1,900 kg (4,189 lbs)`,
  `6 boxes of 76 lbs each…`), and free-text `seller`, `variety`,
  `processing_method`, `lot_id`.

## Approaches considered

| | Plain Python per row | **Polars (chosen)** | dbt-duckdb |
|---|---|---|---|
| Fits messy string cleaning | well | well (`str.extract_groups`, joins to lookup tables) | poorly (SQL regex) |
| Data-engineering signal | low | good | highest |
| Testing | pytest + hypothesis | pytest + hypothesis over small frames | dbt tests, little pytest |
| Fits the roadmap | — | Dagster Python assets (M6) | dbt marts (M5+) |

- **Plain Python**, building a pydantic model per row: clean and very
  testable, but reads as a Python app, not a data pipeline.
- **dbt-duckdb**: the standard transform layer, with lineage and docs, but
  M3's hardest work (price, places) would be its least readable part. Its
  strengths (relational modelling, tests, docs) fit M5 better.
- **Polars (chosen by Tyler):** column expressions for most rules; hand-kept
  lookup tables joined in for places, currencies and units; a few small pure
  functions where a rule is truly procedural. This is the common pattern:
  Python cleanup to silver Parquet, then dbt over it for modelled layers.

## Design

### Order of transforms

Each transform is independent, except that **price needs the roaster's
country** (Decision 7): `places` runs before `price`, and `price` reads
`roaster_country`. Every other transform reads only its own parsed field
(plus `review_month` for Agtron's window).

### Modules

Following the existing dependency rule
(`cli → pipeline → {storage, parsers, transforms} → {models, config}`):

| Module | Job |
|---|---|
| `transforms/__init__.py` | the package: pure Polars transforms, no I/O |
| `transforms/dates.py` | `review_date`, `harvested` |
| `transforms/scores.py` | `rating`, the five scores, `with_milk` |
| `transforms/roast.py` | `roast_level`, `agtron` |
| `transforms/price.py` | `price` → amount, currency, quantity, unit |
| `transforms/places.py` | `coffee_origin`, `roaster_location` |
| `transforms/lookups.py` | loads the hand-kept lookup tables (below) |
| `transforms/lookups/*.csv` | `places.csv`, `currencies.csv`, `units.csv` |
| `transforms/rules.py` | the cleaning-log plumbing shared by all transforms |
| `models/clean.py` | the clean-table contract (schemas), `CleanRule` names |
| `pipeline/clean.py` | composition root + `run_clean` |
| `pipeline/clean_report.py` | builds and renders the clean report |
| `storage/clean_store.py` | `CleanStore` Protocol, Parquet store, factory |
| `cli.py` | `coffee-review clean` |

**Lookup tables are code, not data.** They're small CSVs versioned with the
code and read with `importlib.resources`, like M2's alias table but easier to
review and extend as rows. They aren't pipeline data, so the "only `storage/`
knows formats" rule doesn't apply to them (*my call*).

### How a transform works

Each field's transform takes the parsed frame and returns the frame with:

- the **typed output columns** (e.g. `agtron_whole`, `agtron_ground`);
- one **rule column** per input field (e.g. `_rule_agtron`), holding the name
  of the rule that changed or rejected the value, or null when the value was
  taken as written.

`transforms/rules.py` turns the rule columns into cleaning-log rows (`slug`,
`dataset`, `field`, `rule`, `raw_value`) and drops them from the clean table.
A rule name is a short fixed string from `models/clean.py` (`not_measured`,
`unknown_marker`, `swapped_2002_2003`, `suspect_value`, `unparsed`,
`unplaced`, `ambiguous_currency`…), so the report can count them and the
contract can check them.

**Example: `agtron`** (`48/40`, a December 2002 review):

1. split on `/` into whole and ground text;
2. each side: blank, `NA`, `na`, `0` → null (rule `not_measured` or
   `unknown_marker`); `g`/`wb` → null (rule `template_label`); otherwise a
   number;
3. review month between June 2002 and March 2003 (inclusive), both present:
   ground < whole → swap (`agtron_swapped = true`, rule `swapped_2002_2003`);
   otherwise → `agtron_suspect = true` (rule `unswapped_in_window`);
4. a reading above 150 that a known typo pattern explains → its first two
   digits, `agtron_corrected = true` (rule `typo_corrected`; Decision 9; this
   runs before step 3);
5. any other decimal, or a value above 150 → kept, `agtron_suspect = true`
   (rule `suspect_value`).

The swap window is two named constants in `transforms/roast.py`. A swapped row
has ground > whole, so running the transform on its own output changes
nothing.

Result: `agtron_whole = 40`, `agtron_ground = 48`, `agtron_swapped = true`.

### Field rules

Columns not listed (`slug`, `url`, `delisted`, `sold_green`, `title`,
`roaster`, `roaster_url`, `seller`, `variety`, `processing_method`, `lot_id`)
are carried through as they are. `extra` isn't carried; `extra_count` is.

| Parsed field | Clean columns | Rule (*my call* unless in Decisions) |
|---|---|---|
| `review_date` | `review_month: Date` | always the first day of the month (`May 2020` → 2020-05-01); renamed so it isn't read as an exact day (Tyler) |
| `rating` | `rating: Int16` | 50–100; `NR` → null (`not_rated`) |
| aroma, acidity, body, flavor, aftertaste | same names, `Float32` | 1–10, decimals kept (1997–99); `NA`/`na`/`n/a`/`NR` → null (`unknown_marker`); words (`Low`…) → null (`word_score`), original in the log |
| `with_milk` | `with_milk: Float32` | as the scores; `Flavor in milk: 5` → 5 (`labelled_score`) |
| `roast_level` | `roast_level: Enum` | the six names, in order light → very dark; `NA` → null |
| `agtron` | `agtron_whole`, `agtron_ground: Int16`; `agtron_swapped`, `agtron_corrected`, `agtron_suspect: Boolean` | as above |
| `price` | `price_amount: Float64`, `price_currency: String` (ISO 4217), `price_currency_inferred: Boolean`, `price_quantity: Float64`, `price_unit: String`, `price_grams: Float64`, `price_note: String` | see Price |
| `coffee_origin` | `origin_country: String`, `origin_region: String` | see Places |
| `roaster_location` | `roaster_city`, `roaster_region`, `roaster_country: String` | see Places |
| `harvested` (green) | `harvest_year: Int16`, `harvest_text: String` | the last 4-digit year; the text kept for ranges |
| `lot_size` (green) | `lot_size_kg: Float64`, `lot_size_text: String` | first weight converted to kg when it's a plain weight; otherwise null (`unparsed`), text kept |
| blind_assessment, notes, bottom_line | same | zero-width characters removed |

### Price

`<currency><amount>/<quantity> <unit> [(note)]` in its spellings, read with
one regular expression with named groups, plus a short list of other forms
seen (`<amount> <currency>/…`, `£6.50 for 250g`, `Price: …`).

- **Currency:** the prefix or suffix looked up in `currencies.csv`
  (`NT$`, `NTD$`, `NT`, `TWD$` → TWD; `US$`, `USD$` → USD; `£` → GBP…).
  Two symbols need the roaster's country (Decision 7), so **places run before
  price**:
  - bare `$` → USD, or the roaster country's own dollar for Canada, Australia,
    Hong Kong, Singapore and New Zealand (`price_currency_inferred = true`,
    rule `currency_from_roaster`; a US roaster's `$` is plain USD, not
    inferred);
  - `¥` → JPY for a Japanese roaster, CNY for a Chinese one (inferred, same
    rule); any other roaster, or none placed → null, rule
    `ambiguous_currency`, the amount still kept.

  `currencies.csv` holds the spellings; a `dollar_countries` list (country →
  currency) holds the inference. An unknown spelling → null currency, rule
  `unknown_currency`, listed in the report.
- **Quantity and unit:** the unit looked up in `units.csv`, which maps each
  spelling to a canonical unit (`ounce`, `gram`, `kilogram`, `pound`,
  `fluid_ounce`, `millilitre`, `capsule`, `pod`, `bag`, `packet`, `can`,
  `bottle`) and, for weights, grams per unit. Missing quantity → 1.
- **`price_grams`:** quantity × grams per unit, for weights only; otherwise
  null.
- **`price_note`:** text in brackets after the unit, kept as written.
- `NA`, `Not Available`, `See website…`, `See review note` → all null
  (`not_a_price`). A bare amount (`$15.00`) keeps amount and currency, null
  quantity (`no_quantity`). Anything else → all null (`unparsed`), listed in
  the report.

### Places

Two tables (Decision 10): `countries.csv`, generated, every country and its
spellings; and `places.csv`, hand-kept, one row per other **place spelling**:
`alias`, `name` (the standard spelling, so regions compare as one place),
`country`, `kind` (`country`, `state`, `province`, `island`, `area`) and
`roaster_country` (blank unless it differs: Hawaiʻi). Examples:

| alias | country | kind |
|---|---|---|
| Ethiopia | Ethiopia | country |
| Hawaii, Hawai’i, Hawai'i, Big Island of Hawai’i… | Hawaii | country |
| Puerto Rico | Puerto Rico | country |
| Sumatra, Java | Indonesia | island |
| Arkansas, California… | United States | state |
| Ontario, British Columbia… | Canada | province |
| Latin America, Central America, Africa, Blend | Multiple | area |

Country names follow common English usage (`Democratic Republic of the
Congo`, `Taiwan`), one spelling per country.

**Origin:**

1. `Not disclosed` (any case, with or without a full stop) → both null
   (`not_disclosed`).
2. Split on commas, semicolons and `and`; strip direction words (`southern`,
   `far western`, `south-central`…) and quotes from each part; look each part
   up in `places.csv`.
3. Countries found: none → `origin_country` null (`unplaced`, listed in the
   report); one → that country; more than one, or any `area` → `"Multiple"`.
4. `origin_region`: the parts that aren't a country, joined with `, `, with
   filler removed (`growing region`, `region`, `zone`, `department`, `state`
   when they're trailing words). `Guji Zone, Oromia Region, southern
   Ethiopia` → country `Ethiopia`, region `Guji, Oromia`. For `"Multiple"`,
   the region is null and the full text is in the log.

**Roaster location:**

1. The last part is looked up: a country → `roaster_country`; a US state or
   Canadian province → `roaster_region`, with the country from its row.
2. Of the remaining parts, the first is `roaster_city`; any part between city
   and country is `roaster_region`.
3. One part that's a country (`Taiwan`) → country only.
4. Nothing placed → all null except `roaster_city` (the first part), rule
   `unplaced`, listed in the report.

For roasters, Hawaiʻi is a US state: `Holualoa, Hawai’i Island, Hawai’i` →
city `Holualoa`, region `Hawaii`, country `United States` (*my call*: Tyler's
"its own country" decision is about coffee origin; a roaster is a business
address). The lookup has a separate `roaster_country` column for the few
aliases where the two differ.

### Datasets and tables

```
data/clean/
  roasted/reviews.parquet
  green/reviews.parquet
  cleaning_log.parquet      slug, dataset, field, rule, raw_value
  report.md
```

Every run rewrites all four, each atomically (the M2 store's
`_write_atomically` pattern). Rows sorted by slug.

### The contract

`models/clean.py` defines each clean table's schema once, as a
**pandera `DataFrameModel` (Polars backend)** (*my call*: pandera is the
common open-source way to state a DataFrame contract and check it, and its
Polars support is mature). It checks:

- column names, order and types;
- allowed values (`roast_level`, ISO currency codes from `currencies.csv`,
  rule names);
- `review_month` always falls on the 1st;
- ranges (`rating` 50–100, scores 1–10, Agtron 0 < x ≤ 7000 so suspect
  values pass but negatives don't);
- `slug` unique and non-null.

`run_clean` validates both tables before writing. A failure is a bug in a
transform: the run stops with the failing checks and writes nothing.

### The clean report

`data/clean/report.md`, built like the parse report:

- **Totals:** rows per dataset, cleaning-log rows.
- **Rules applied:** per field and rule, how many rows, with up to three
  example slugs and raw values.
- **To review:** every distinct raw value with `unplaced`, `unparsed`,
  `unknown_currency`, `ambiguous_currency`, `unknown_unit` or
  `close_spelling` (shown as `raw → placed as`), with counts, each marked
  **new** when the previous run's log didn't have it. This is the list for
  growing the lookup tables.
- **Coverage:** non-null share per clean column, per dataset.
- **Distributions** (a quick sanity view): origin countries, roaster
  countries, currencies, units, roast levels × Agtron range.

The summary line follows the parse command's style:
`clean finished: 9,440 rows · 9,330 roasted, 110 green · 1,234 log rows · 18 unplaced · in 2s`.

### CLI

`coffee-review clean`, logging to `run_log_path(data_dir, "clean", now)` like
`parse`. No progress bar (the run takes seconds). Exit 0 when done; exit 1
when the parsed layer is missing (with "run parse first") or the contract
fails.

### A decision record

`docs/decisions/0008-clean-in-polars.md`: Polars for M3, the layers table
above, why dbt waits for M5, and dbt Python models as the escape hatch.

## Testing

- **Transforms, one test file each,** over small hand-built frames: every
  shape in the value-shape summary has a test row, plus the odd values listed
  in *What the data shows*.
- **Hypothesis properties:**
  - price: formatting random (currency, amount, quantity, unit) as
    `NT $700/200 grams` and parsing it gives the same parts back;
  - Agtron: swapping is only ever applied in June 2002 – March 2003, and
    applying the transform to its own output changes nothing;
  - currency: `price_currency_inferred` is true exactly when the symbol was
    `$` from a non-US roaster or `¥`;
  - scores: every non-null output is in 1–10;
  - every non-null input that becomes null has a log row.
- **Lookup tables:** no alias appears twice; every country used in
  `units.csv`/`currencies.csv` references exists; aliases are matched
  case-insensitively.
- **Contract:** a valid frame passes; each kind of violation fails.
- **Store contract tests,** parametrized over the Parquet store and an
  in-memory fake (the M2 pattern).
- **Pipeline:** `run_clean` with fakes; one end-to-end test from a tiny
  parsed fixture to clean tables, with inline-snapshot.
- **Real data check** (not in CI): a full clean of `data/parsed/`, with the
  unplaced and unparsed lists read by a person before M3 is called done.

## Done when

- `coffee-review clean` writes both clean tables, the log and the report from
  the real parsed layer, and both tables pass the contract.
- Unplaced origins are under 1% of disclosed origins, and unparsed prices
  under 1% of real prices, with every one listed in the report.
- Every non-null parsed value that became null has a log row (checked on the
  real data).
- The transforms have unit and property tests; ruff, mypy and pytest pass.
- ADR 0008 is written, and the roadmap and README are updated.

## Left to the builder

- The exact regular expressions, and how many alternative price forms need
  their own pattern, guided by the report's unparsed list.
- The full contents of `places.csv`, `currencies.csv` and `units.csv`, grown
  from the real data until the "Done when" thresholds are met.
- Whether a transform is a function returning expressions, or a function
  taking and returning a `LazyFrame`, as long as it stays pure and is tested
  on small frames.
- The exact filler-word list for regions.
