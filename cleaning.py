"""Cleaning rules for the World Bank historical tourism panel.

These pure pandas transformations are the rules used to build
``data/clean_tourism.csv`` (and are covered by ``tests/test_cleaning.py``):

1. keep only 2010-2023;
2. drop World Bank aggregates (World, income groups, regions, Euro area, ...);
3. drop rows where *both* indicators are missing;
4. derive ``receipts_per_arrival`` and ``recovery_vs_2019``;
5. never fill a missing value with 0 - missing stays NaN.

No I/O happens here, so the rules can be verified without the network.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

COLUMNS: tuple[str, ...] = (
    "country",
    "region",
    "year",
    "arrivals",
    "receipts",
    "receipts_per_arrival",
    "recovery_vs_2019",
)

BASE_YEAR: int = 2019
DEFAULT_YEARS: tuple[int, int] = (2010, 2023)

# Aggregates present in the World Bank ``country/all`` feed. Real economies are
# identified by having their own region label; aggregates carry "Aggregates".
AGGREGATE_NAMES: frozenset[str] = frozenset(
    {
        "World",
        "High income",
        "Low income",
        "Lower middle income",
        "Upper middle income",
        "Middle income",
        "Euro area",
        "European Union",
        "Arab World",
        "East Asia & Pacific",
        "East Asia & Pacific (excluding high income)",
        "East Asia & Pacific (IDA & IBRD countries)",
        "Europe & Central Asia",
        "Europe & Central Asia (excluding high income)",
        "Europe & Central Asia (IDA & IBRD countries)",
        "Latin America & Caribbean",
        "Latin America & Caribbean (excluding high income)",
        "Latin America & Caribbean (IDA & IBRD countries)",
        "Middle East & North Africa",
        "Middle East & North Africa (excluding high income)",
        "Middle East & North Africa (IDA & IBRD countries)",
        "South Asia",
        "South Asia (IDA & IBRD)",
        "Sub-Saharan Africa",
        "Sub-Saharan Africa (excluding high income)",
        "Sub-Saharan Africa (IDA & IBRD countries)",
        "Africa Eastern and Southern",
        "Africa Western and Central",
        "IDA & IBRD total",
        "IBRD only",
        "IDA only",
        "IDA total",
        "Least developed countries: UN classification",
        "Heavily indebted poor countries (HIPC)",
        "Fragile and conflict affected situations",
        "OECD members",
        "Small states",
        "Other small states",
        "Pacific island small states",
        "Caribbean small states",
        "Not classified",
    }
)


def compute_receipts_per_arrival(
    receipts: Sequence[float] | pd.Series,
    arrivals: Sequence[float] | pd.Series,
) -> pd.Series:
    """Receipts divided by arrivals.

    NaN when either input is missing or when arrivals is 0, so the ratio is
    never fabricated from a zero denominator.
    """
    receipts_num = pd.to_numeric(pd.Series(receipts), errors="coerce").astype("float64")
    arrivals_num = pd.to_numeric(pd.Series(arrivals), errors="coerce").astype("float64")
    denominator = arrivals_num.where(arrivals_num > 0)
    return receipts_num / denominator


def compute_recovery_vs_2019(df: pd.DataFrame, base_year: int = BASE_YEAR) -> pd.Series:
    """Arrivals as a percentage of the same country's base-year arrivals.

    The base is the country's ``base_year`` arrivals; countries without a usable
    base get NaN instead of a zero or a global average.
    """
    arrivals = pd.to_numeric(df["arrivals"], errors="coerce").astype("float64")
    years = pd.to_numeric(df["year"], errors="coerce")
    base = arrivals.where(years == base_year).groupby(df["country"]).transform("max")
    return arrivals / base.where(base > 0) * 100.0


def drop_aggregates(df: pd.DataFrame) -> pd.DataFrame:
    """Drop World Bank aggregates by name and by region label."""
    keep = ~df["country"].isin(AGGREGATE_NAMES)
    if "region" in df.columns:
        keep &= df["region"].fillna("").astype(str).ne("Aggregates")
    return df.loc[keep].copy()


def clean_panel(
    df: pd.DataFrame, years: tuple[int, int] = DEFAULT_YEARS
) -> pd.DataFrame:
    """Apply every cleaning rule and return the panel in column order."""
    out = df.copy()
    if "region" not in out.columns:
        out["region"] = pd.NA

    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    out["arrivals"] = pd.to_numeric(out["arrivals"], errors="coerce")
    out["receipts"] = pd.to_numeric(out["receipts"], errors="coerce")

    out = out[out["year"].between(years[0], years[1])]
    out = drop_aggregates(out)
    out = out.dropna(subset=["arrivals", "receipts"], how="all")
    out["year"] = out["year"].astype("Int64")
    out = out.sort_values(["country", "year"]).reset_index(drop=True)

    out["receipts_per_arrival"] = compute_receipts_per_arrival(
        out["receipts"], out["arrivals"]
    ).to_numpy()
    out["recovery_vs_2019"] = compute_recovery_vs_2019(out).to_numpy()
    return out.loc[:, list(COLUMNS)]
