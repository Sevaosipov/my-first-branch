"""Indicator correctness, checked against hand-computed values."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swingfx import indicators as ind  # noqa: E402


def test_sma_is_nan_until_window_is_full():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    out = ind.sma(s, 3)
    assert out.isna().tolist() == [True, True, False, False]
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[3] == pytest.approx(3.0)


def test_ema_seeds_with_sma_then_recurses():
    s = pd.Series([1.0, 2.0, 3.0, 10.0])
    out = ind.ema(s, 3)
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)  # SMA(1,2,3)
    alpha = 2.0 / 4.0
    assert out.iloc[3] == pytest.approx(alpha * 10.0 + (1 - alpha) * 2.0)


def test_rma_uses_wilder_alpha():
    s = pd.Series([1.0, 2.0, 3.0, 10.0])
    out = ind.rma(s, 3)
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[3] == pytest.approx(10.0 / 3.0 + 2.0 * 2.0 / 3.0)


def test_true_range_uses_previous_close():
    high = pd.Series([10.0, 12.0])
    low = pd.Series([9.0, 11.5])
    close = pd.Series([9.5, 12.0])
    tr = ind.true_range(high, low, close)
    assert tr.iloc[0] == pytest.approx(1.0)  # no previous close
    # |12 - 9.5| = 2.5 beats the 0.5 bar range
    assert tr.iloc[1] == pytest.approx(2.5)


def test_atr_is_positive_and_warms_up():
    rng = np.random.default_rng(0)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 100)))
    high, low = close + 1.0, close - 1.0
    out = ind.atr(high, low, close, 14)
    assert out.iloc[:13].isna().all()
    assert (out.dropna() > 0).all()


def test_rsi_bounds_and_extremes():
    rising = pd.Series(np.arange(1.0, 40.0))
    assert ind.rsi(rising, 14).dropna().iloc[-1] == pytest.approx(100.0)

    falling = pd.Series(np.arange(40.0, 1.0, -1.0))
    assert ind.rsi(falling, 14).dropna().iloc[-1] == pytest.approx(0.0)

    rng = np.random.default_rng(1)
    noisy = pd.Series(100 + np.cumsum(rng.normal(0, 1, 200)))
    values = ind.rsi(noisy, 14).dropna()
    assert values.between(0, 100).all()


def test_indicators_are_causal():
    """Appending future bars must not change any earlier value."""
    rng = np.random.default_rng(2)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 300)))
    high, low = close + 0.5, close - 0.5
    cut = 200

    for name, full, short in [
        ("ema", ind.ema(close, 50), ind.ema(close.iloc[:cut], 50)),
        ("rsi", ind.rsi(close, 14), ind.rsi(close.iloc[:cut], 14)),
        (
            "atr",
            ind.atr(high, low, close, 14),
            ind.atr(high.iloc[:cut], low.iloc[:cut], close.iloc[:cut], 14),
        ),
    ]:
        pd.testing.assert_series_equal(
            full.iloc[:cut], short, check_names=False, obj=f"{name} changed by future data"
        )
