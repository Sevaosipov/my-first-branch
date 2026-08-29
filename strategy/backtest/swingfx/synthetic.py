"""Synthetic price paths, used to test the *engine* rather than the market.

Two generators, for two different questions:

`random_walk_panel` -- a driftless random walk with no serial correlation.
There is no trend to follow, so a correctly implemented trend strategy must
lose roughly its transaction costs on this data. If it makes money here, the
backtester has a lookahead bug. This is the single most useful test in the
repo.

`trending_panel` -- a regime-switching drift, so momentum genuinely exists.
A correct implementation must extract some of it. This proves the machinery
works; it says nothing about whether real FX contains the same drift.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Daily volatility of the majors sits around 6-9% annualised.
DEFAULT_ANNUAL_VOL = 0.075
STEPS_PER_DAY = 24  # intraday steps used to build a realistic high/low


def _business_days(n_days: int, start: str) -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n_days)


def _path_to_ohlc(prices: np.ndarray, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Fold intraday steps into daily OHLC bars."""
    steps = prices.reshape(len(index), STEPS_PER_DAY)
    return pd.DataFrame(
        {
            "open": steps[:, 0],
            "high": steps.max(axis=1),
            "low": steps.min(axis=1),
            "close": steps[:, -1],
        },
        index=index,
    )


def random_walk_panel(
    symbols: list[str],
    *,
    n_days: int = 2500,
    start: str = "2015-01-01",
    annual_vol: float = DEFAULT_ANNUAL_VOL,
    start_prices: dict[str, float] | None = None,
    seed: int = 0,
) -> dict[str, pd.DataFrame]:
    """Geometric Brownian motion: zero drift, independent increments."""
    rng = np.random.default_rng(seed)
    index = _business_days(n_days, start)
    step_vol = annual_vol / np.sqrt(252 * STEPS_PER_DAY)
    start_prices = start_prices or {}

    panels = {}
    for symbol in symbols:
        p0 = start_prices.get(symbol, 150.0 if symbol.endswith("JPY") else 1.10)
        shocks = rng.normal(0.0, step_vol, n_days * STEPS_PER_DAY)
        prices = p0 * np.exp(np.cumsum(shocks))
        panels[symbol] = _path_to_ohlc(prices, index)
    return panels


def trending_panel(
    symbols: list[str],
    *,
    n_days: int = 2500,
    start: str = "2015-01-01",
    annual_vol: float = DEFAULT_ANNUAL_VOL,
    trend_strength: float = 0.6,
    mean_regime_days: int = 60,
    start_prices: dict[str, float] | None = None,
    seed: int = 0,
) -> dict[str, pd.DataFrame]:
    """Same volatility, but drift flips between up/flat/down regimes.

    `trend_strength` is the regime drift expressed as a fraction of annual
    volatility, so 0.6 means a trending regime has a ~0.6 Sharpe while it
    lasts. Regime length is geometric with mean `mean_regime_days`.
    """
    rng = np.random.default_rng(seed)
    index = _business_days(n_days, start)
    step_vol = annual_vol / np.sqrt(252 * STEPS_PER_DAY)
    switch_prob = 1.0 / mean_regime_days
    start_prices = start_prices or {}

    panels = {}
    for symbol in symbols:
        p0 = start_prices.get(symbol, 150.0 if symbol.endswith("JPY") else 1.10)
        drifts = np.empty(n_days)
        state = rng.choice([-1, 0, 1])
        for i in range(n_days):
            if rng.random() < switch_prob:
                state = rng.choice([-1, 0, 1])
            drifts[i] = state
        daily_drift = drifts * trend_strength * annual_vol / 252.0
        step_drift = np.repeat(daily_drift / STEPS_PER_DAY, STEPS_PER_DAY)
        shocks = rng.normal(0.0, step_vol, n_days * STEPS_PER_DAY) + step_drift
        prices = p0 * np.exp(np.cumsum(shocks))
        panels[symbol] = _path_to_ohlc(prices, index)
    return panels
