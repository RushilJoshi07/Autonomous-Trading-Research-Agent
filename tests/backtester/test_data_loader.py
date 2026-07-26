import pandas as pd

from backtester.data_loader import load_price_data


def test_columns_and_index(seeded_db):
    """load_price_data returns exactly the columns and index type backtesting.py needs."""
    df = load_price_data("SYNTHETIC", seeded_db)

    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.name == "Date"
    assert len(df) == 500


def test_values_match_seeded_adj_close(seeded_db, synthetic_data):
    """Close column must equal the adj_close values we seeded — no raw/adj mix-up."""
    df = load_price_data("SYNTHETIC", seeded_db)

    for ts, row in df.iterrows():
        expected = float(synthetic_data.loc[ts, "Close"])
        assert abs(row["Close"] - expected) < 0.01, (
            f"Close mismatch on {ts}: got {row['Close']}, expected {expected}"
        )


def test_date_range_filter(seeded_db):
    """start and end arguments restrict the returned rows correctly."""
    from datetime import date

    df_full = load_price_data("SYNTHETIC", seeded_db)
    mid = df_full.index[len(df_full) // 2].date()

    df_from = load_price_data("SYNTHETIC", seeded_db, start=mid)
    assert df_from.index[0].date() >= mid

    df_to = load_price_data("SYNTHETIC", seeded_db, end=mid)
    assert df_to.index[-1].date() <= mid


def test_missing_ticker_raises(seeded_db):
    """ValueError is raised when the ticker has no data."""
    import pytest

    with pytest.raises(ValueError, match="No price data found"):
        load_price_data("NOTREAL", seeded_db)
