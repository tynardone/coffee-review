import marimo

__generated_with = "0.26.0"
app = marimo.App(
    width="medium",
    app_title="coffee-review",
    sql_output="native",
)


@app.cell
def _():
    from pathlib import Path

    import polars as pl

    ROOT_DIR = Path(__file__).resolve().parents[1]
    DATA_DIR = ROOT_DIR / "data"
    ROASTED_FILE = DATA_DIR / "clean" / "roasted" / "reviews.parquet"
    CLEAN_LOG = DATA_DIR / "clean" / "cleaning_log.parquet"

    print(DATA_DIR)
    return ROASTED_FILE, pl


@app.cell
def _(ROASTED_FILE, pl):
    df = pl.read_parquet(ROASTED_FILE)
    df


@app.cell
def _():
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
