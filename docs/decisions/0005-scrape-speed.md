# 0005. Scrape speed: a sensible default, tunable from the CLI, measured every run

Date: 2026-11-21 · Status: accepted

## Context

The first design set the scraper to 1 request per second with 2 concurrent
requests. That was a cautious guess, not based on evidence: a full pass of
about 9,400 reviews would take about 2.6 hours.

The first version of this project scraped the same site in about 30
minutes, which means roughly 5 requests per second. The site handled that
without pushing back.

The right speed is something to find out by trying, not something to fix in
code. Whoever runs the scraper needs to:

- change the speed without editing code;
- be unable to choose settings that are wasteful or that can't reach the rate
  they asked for;
- see what a run actually did to the site, so different speeds can be
  compared.

The rate and the concurrency work together. Requests per second are at most
`min(rate, concurrency ÷ response time)`. That means two separate settings can
easily be combined into a pair that can't reach its own rate (too little
concurrency), or that only adds requests waiting at the rate limiter while
holding connections open (too much).

## Decision

- **Default rate is 5 requests per second.** That makes a full pass about 31
  minutes, in line with v1.
- **Hard ceiling of 10 per second.** Anything faster is rejected. This was my
  call, not a measured limit; raise it with a new record if real runs show the
  site is comfortable above it.
- **The rate is the one setting most people need.**
  `coffee-review scrape --rate R` works out the concurrency as
  `max(1, ceil(rate))`, which is one second's worth of requests in flight.
- **Both settings can be given for experiments,** with
  `--rate R --concurrency C`, as long as `ceil(rate) ≤ C ≤ 2 × ceil(rate)`.
  Each rejection says what to do instead. `--concurrency` without `--rate` is
  rejected, because it only means something relative to a rate.
- **The rules live in `CoffeeSiteSettings`,** so the environment variables and
  the CLI follow exactly the same validation.
- **Every run reports its load on the site.** That covers total HTTP requests
  (sitemaps and retries included), elapsed time, the average rate achieved,
  the average response time, and the rate and concurrency used. It's on the
  summary line and in the run log.
- **The stop rules are unchanged.** A 429, a challenge page, or 5 failures in
  a row still ends the run, however fast it's going.

## Why

- **A single setting can't be combined wrongly.** "How hard am I hitting the
  site" is the question a runner actually has.
- **Allowing both settings, within bounds, keeps experiments possible,** for
  example extra concurrency for a slow site, without allowing pairs that make
  no sense.
- **Measuring every run turns "try different speeds" into evidence.**
  - An achieved rate close to the setting means the site is keeping up.
  - A much lower achieved rate, together with rising response times, means the
    site is struggling, and the speed should come down.

## Alternatives considered

- **Keep 1 per second.** Safe, but about five times slower than the site has
  been shown to handle, for no observed benefit.
- **Named presets only** (`--speed gentle|normal|fast`). Simple, but too coarse
  to experiment with: trying 4 per second would mean editing code.
- **Two independent settings with no validation.** The most flexible, but most
  combinations are either unreachable or wasteful, and using them well depends
  on understanding how the two interact.
