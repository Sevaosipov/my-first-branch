"""The Trend Pullback Continuation (TPC) signal rules.

The edge this tries to harvest is time-series momentum in FX -- the one effect
in currencies with a long, repeatedly-replicated literature behind it at
multi-week horizons (Moskowitz/Ooi/Pedersen 2012; Menkhoff/Sarno/Schmeling/
Schrimpf 2012). Everything here is in service of two goals:

  1. Only take trades aligned with an established trend, because trend is
     where the documented drift lives.
  2. Enter on a pullback rather than a breakout, so the entry price sits
     lower in the trend and the trade has room to run before the trailing
     stop catches it. (The stop distance itself is 2 x ATR either way -- the
     pullback buys a better entry, not a tighter stop.) Measured effect: the
     dip filter halves the trade count and roughly doubles gross edge per
     trade, the largest structural effect found in testing.

Signals are computed from bar t using only bars <= t, and are always executed
at the open of bar t+1. No function in this module looks forward.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd

from . import indicators as ind


@dataclass(frozen=True)
class StrategyParams:
    """Every tunable rule in one place. Defaults are the shipped configuration.

    These are round numbers on purpose. A parameter set tuned to three decimal
    places on one price history is a description of that history, not a
    strategy.
    """

    # Trend regime
    ema_fast: int = 50
    ema_slow: int = 200
    mom_lookback: int = 60  # ~3 months of trading days

    # Pullback and re-entry trigger
    rsi_length: int = 14
    rsi_dip: float = 42.0  # long setups need RSI to have dipped below this
    dip_lookback: int = 5
    breakout_bars: int = 2  # close must clear the high of the last N bars

    # Volatility gate: skip dead markets and blown-out ones alike
    atr_length: int = 14
    min_atr_pct: float = 0.0030
    max_atr_pct: float = 0.0200

    # Risk geometry.
    #
    # Trend systems earn their living in the right tail: a handful of trades
    # that run for weeks pay for the many that scratch. Every rule that caps a
    # winner early -- scaling out at 1R, jumping to breakeven at the first sign
    # of profit -- trims that tail, and the backtest shows it plainly (see
    # RESULTS.md). So partial exits are OFF by default and the stop stays where
    # it was until the trade has proved itself.
    atr_stop_mult: float = 2.0  # initial stop distance
    atr_trail_mult: float = 3.0  # chandelier trail once trailing starts
    trail_start_r: float = 1.0  # start trailing after this much favourable movement
    breakeven_at_r: float | None = None  # None = never force breakeven
    partial_fraction: float = 0.0  # 0 disables scaling out
    partial_at_r: float = 1.0
    time_stop_bars: int = 15  # give up on a trade that has gone nowhere
    time_stop_min_r: float = 0.5  # ...unless it is at least this far in profit

    allow_long: bool = True
    allow_short: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


def compute_features(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """Attach indicator columns to an OHLC frame indexed by date."""
    out = df.copy()
    close, high, low = out["close"], out["high"], out["low"]

    out["ema_fast"] = ind.ema(close, p.ema_fast)
    out["ema_slow"] = ind.ema(close, p.ema_slow)
    out["mom"] = ind.roc(close, p.mom_lookback)
    out["rsi"] = ind.rsi(close, p.rsi_length)
    out["atr"] = ind.atr(high, low, close, p.atr_length)
    out["atr_pct"] = out["atr"] / close

    out["rsi_min"] = ind.rolling_min(out["rsi"], p.dip_lookback)
    out["rsi_max"] = ind.rolling_max(out["rsi"], p.dip_lookback)
    # Prior-bar extremes: shift(1) keeps today's own high/low out of the level
    # today has to break.
    out["prior_high"] = ind.rolling_max(high, p.breakout_bars).shift(1)
    out["prior_low"] = ind.rolling_min(low, p.breakout_bars).shift(1)
    return out


def generate_signals(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """Add `long_signal` / `short_signal` boolean columns.

    A True on bar t is an instruction to enter at the open of bar t+1.
    """
    out = compute_features(df, p)

    vol_ok = out["atr_pct"].between(p.min_atr_pct, p.max_atr_pct)

    uptrend = (out["ema_fast"] > out["ema_slow"]) & (out["close"] > out["ema_slow"])
    downtrend = (out["ema_fast"] < out["ema_slow"]) & (out["close"] < out["ema_slow"])

    long_dip = out["rsi_min"] < p.rsi_dip
    short_dip = out["rsi_max"] > (100.0 - p.rsi_dip)

    long_trigger = out["close"] > out["prior_high"]
    short_trigger = out["close"] < out["prior_low"]

    out["long_signal"] = (
        uptrend & (out["mom"] > 0) & vol_ok & long_dip & long_trigger
        if p.allow_long
        else False
    )
    out["short_signal"] = (
        downtrend & (out["mom"] < 0) & vol_ok & short_dip & short_trigger
        if p.allow_short
        else False
    )

    out["long_signal"] = out["long_signal"].fillna(False).astype(bool)
    out["short_signal"] = out["short_signal"].fillna(False).astype(bool)
    return out
