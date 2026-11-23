"""Unit tests for the historical cleaning rules."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import cleaning


def sample_frame() -> pd.DataFrame:
    """A tiny panel: one real economy plus three aggregates and a missing value."""
    return pd.DataFrame(
        {
            "country": [
                "France",
                "France",
                "World",
                "High income",
                "Japan",
                "Japan",
                "Nowhere",
            ],
            "region": [
                "Europe & Central Asia",
                "Europe & Central Asia",
                "Aggregates",
                "Aggregates",
                "East Asia & Pacific",
                "East Asia & Pacific",
                "Aggregates",
            ],
            "year": [2019, 2020, 2019, 2019, 2019, 2020, 2021],
            "arrivals": [100.0, 50.0, 10_000.0, 9_000.0, 80.0, np.nan, np.nan],
            "receipts": [200.0, np.nan, 50_000.0, 40_000.0, 90.0, 10.0, np.nan],
        }
    )


def test_receipts_per_arrival() -> None:
    ratio = cleaning.compute_receipts_per_arrival(
        pd.Series([200.0, np.nan, 5.0]), pd.Series([100.0, 10.0, 0.0])
    )
    assert ratio.iloc[0] == pytest.approx(2.0)
    assert np.isnan(ratio.iloc[1])  # missing receipts stay missing
    assert np.isnan(ratio.iloc[2])  # zero arrivals never produce inf


def test_recovery_vs_2019() -> None:
    df = pd.DataFrame(
        {
            "country": ["France", "France", "Japan"],
            "year": [2019, 2020, 2020],
            "arrivals": [100.0, 40.0, 70.0],
        }
    )
    recovery = cleaning.compute_recovery_vs_2019(df)
    assert recovery.iloc[0] == pytest.approx(100.0)
    assert recovery.iloc[1] == pytest.approx(40.0)
    assert np.isnan(recovery.iloc[2])  # no 2019 base for Japan


def test_aggregates_are_dropped() -> None:
    cleaned = cleaning.clean_panel(sample_frame())
    countries = set(cleaned["country"])
    assert "World" not in countries
    assert "High income" not in countries
    assert "Nowhere" not in countries  # aggregate region label
    assert {"France", "Japan"} <= countries


def test_nan_is_kept_and_never_filled_with_zero() -> None:
    cleaned = cleaning.clean_panel(sample_frame())
    japan_2020 = cleaned[
        (cleaned["country"] == "Japan") & (cleaned["year"] == 2020)
    ].iloc[0]
    assert pd.isna(japan_2020["arrivals"])
    assert pd.isna(japan_2020["receipts_per_arrival"])
    assert not (cleaned["arrivals"] == 0).any()
    assert not (cleaned["receipts"] == 0).any()


def test_rows_with_both_indicators_missing_are_dropped() -> None:
    cleaned = cleaning.clean_panel(sample_frame())
    assert not cleaned[["arrivals", "receipts"]].isna().all(axis=1).any()


def test_year_bounds_and_column_order() -> None:
    df = sample_frame().copy()
    extra = pd.DataFrame(
        {
            "country": ["France"],
            "region": ["Europe & Central Asia"],
            "year": [2009],
            "arrivals": [1.0],
            "receipts": [2.0],
        }
    )
    cleaned = cleaning.clean_panel(pd.concat([df, extra], ignore_index=True))
    assert cleaned["year"].min() >= 2010
    assert cleaned["year"].max() <= 2020
    assert list(cleaned.columns) == list(cleaning.COLUMNS)
