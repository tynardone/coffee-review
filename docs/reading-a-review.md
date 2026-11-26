# Reading a review

What the scores and roast readings on a Coffee Review page mean, as far as
this project needs to know. It's a summary in our own words; the site's own
explanations are [How to read a review](https://www.coffeereview.com/interpret-coffee/)
and [Roast definitions](https://www.coffeereview.com/roast-definitions/).

## Scores

Every roasted coffee is scored in five categories: **aroma**, **acidity**,
**body**, **flavor** and **aftertaste**, each from 1 to 10. Each score rates
both how strong a quality is and how good it is. Espressos and coffees meant
for milk may also get a **with milk** score.

Most scores are whole numbers. Reviews from 1997–99 use one decimal place
(`6.3`), so the clean stage keeps scores as decimals.

The **rating** is the overall score, from 50 to 100. It sums up the reviewer's
judgment rather than adding the five categories. Roughly:

| Rating | Means |
|---|---|
| 95–100 | exceptional |
| 90–94 | very good to outstanding |
| 85–89 | good |
| 80–84 | fair |
| below 80 | poor |

About 95% of reviews score 85 or more, so the useful differences are
at the top of the scale. `NR` means not rated.

## Roast

**Roast level** is one of six names, from lightest to darkest: Light,
Medium-Light, Medium, Medium-Dark, Dark and Very Dark.

**Agtron** readings measure roast colour with a spectrophotometer (Coffee
Review uses the "Gourmet" scale). The higher the number, the lighter the
roast. A page gives two readings, `55/68`: the whole bean first, then the same
beans ground. Ground coffee is lighter inside than out, so the second number
is usually higher.

| Agtron | Roughly |
|---|---|
| above 80 | light |
| 70–80 | medium-light |
| 60–70 | medium |
| 50–60 | medium-dark |
| 40–50 | dark |
| 25–40 | very dark |
| below 25 | extremely dark |

How the clean stage handles the readings the site printed oddly (the
2002–03 swap, typos, `0` for not measured) is in
[`transforms/roast.py`](../src/coffee_review/transforms/roast.py).
