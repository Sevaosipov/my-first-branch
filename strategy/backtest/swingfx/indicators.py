"""Indicators, implemented to match TradingView's Pine `ta.*` functions.

The Pine script in `strategy/pine/` and this backtester must agree bar for bar,
otherwise a TradingView backtest and a Python backtest of "the same" strategy
quietly diverge. Pine seeds its recursive averages with an SMA of the first `n`
bars rather than with the first value, so that is what we do here too.

Every function is causal: the value at index i depends only on rows <= i.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, length: int) -> pd.Series:
    """Simple moving average. NaN until `length` observations exist."""
    return series.rolling(length, min_periods=length).mean()


def _recursive_average(series: pd.Series, length: int, alpha: float) -> pd.Series:
    """Shared engine for EMA/RMA: seed with SMA(length), then recurse.

    Pine's `ta.ema` and `ta.rma` differ only in alpha (2/(n+1) vs 1/n).
    """
    values = series.to_numpy(dtype=float)
    out = np.full(values.shape, np.nan)
    if len(values) < length:
        return pd.Series(out, index=series.index)

    seed = np.mean(values[:length])
    if np.isnan(seed):
        return pd.Series(out, index=series.index)
    out[length - 1] = seed
    for i in range(length, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return pd.Series(out, index=series.index)


def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential moving average, matching Pine `ta.ema`."""
    return _recursive_average(series, length, 2.0 / (length + 1.0))


def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothing, matching Pine `ta.rma`."""
    return _recursive_average(series, length, 1.0 / length)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True range. The first bar has no previous close, so it is high - low."""
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1, skipna=True)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Average true range, matching Pine `ta.atr`."""
    return rma(true_range(high, low, close), length)


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Relative strength index, matching Pine `ta.rsi`."""
    delta = series.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)
    # Pine feeds ta.rma with the change series starting at bar 1, so drop the
    # leading NaN before smoothing and reindex afterwards.
    avg_gain = rma(gains.iloc[1:], length).reindex(series.index)
    avg_loss = rma(losses.iloc[1:], length).reindex(series.index)

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # An all-gains window means avg_loss == 0, which is RSI 100 rather than NaN.
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(~((avg_loss == 0.0) & (avg_gain == 0.0)), 50.0)
    return out.where(avg_gain.notna())


def roc(series: pd.Series, length: int) -> pd.Series:
    """Rate of change over `length` bars, as a fraction (0.05 == +5%)."""
    return series / series.shift(length) - 1.0


def rolling_max(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).max()


def rolling_min(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).min()
