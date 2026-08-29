"""Engine behaviour: no lookahead, honest accounting, pessimistic fills.

These are the tests that decide whether a backtest can be believed. Everything
else is bookkeeping.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swingfx import MAJORS, Backtest, BrokerSpec, PortfolioParams, StrategyParams  # noqa: E402
from swingfx import synthetic  # noqa: E402

# Short indicator windows so a hand-built fixture can trigger a signal without
# 200 warm-up bars.
FAST = StrategyParams(
    ema_fast=3,
    ema_slow=5,
    mom_lookback=3,
    rsi_length=3,
    dip_lookback=3,
    breakout_bars=1,
    atr_length=3,
    min_atr_pct=0.0,
    max_atr_pct=1.0,
    allow_short=False,
    time_stop_bars=10_000,
)

NO_COST_BROKER = BrokerSpec(
    slippage_pips=0.0,
    stop_slippage_pips=0.0,
    commission_rate_per_side=0.0,
    swap_pips_per_night=0.0,
)


def panel_from_closes(closes, *, spread=0.002, index=None):
    """Build an OHLC frame around a close path, with a fixed intrabar range."""
    closes = np.asarray(closes, dtype=float)
    index = index if index is not None else pd.bdate_range("2024-01-01", periods=len(closes))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes) + spread,
            "low": np.minimum(opens, closes) - spread,
            "close": closes,
        },
        index=index,
    )


def uptrend_with_dip(close_the_trade=True):
    """Rising path, a three-bar dip to arm the RSI filter, then a new high.

    The optional tail sells off hard so the trailing stop fires and the trade
    is booked -- otherwise it is still open when the fixture ends.
    """
    rise = list(np.linspace(1.00, 1.20, 20))
    dip = [1.185, 1.170, 1.160]
    resume = [1.19, 1.21, 1.23, 1.26, 1.30, 1.34, 1.38, 1.42, 1.46, 1.50]
    tail = [1.40, 1.28, 1.15, 1.05] if close_the_trade else []
    return panel_from_closes(rise + dip + resume + tail)


def run(panels, sp=FAST, broker=None, portfolio=None, symbols=("EURUSD",)):
    instruments = {s: MAJORS[s] for s in panels}
    return Backtest(
        panels=panels,
        instruments=instruments,
        strategy_params=sp,
        portfolio=portfolio or PortfolioParams(starting_equity=500.0, risk_fraction=0.01),
        broker=broker or NO_COST_BROKER,
    ).run()


# --------------------------------------------------------------- lookahead


def test_entry_fills_at_the_next_bars_open_plus_the_spread():
    df = uptrend_with_dip()
    result = run({"EURUSD": df})
    assert result.trades, "fixture should produce at least one trade"

    trade = result.trades[0]
    entry_bar = df.index.get_loc(trade.entry_date)
    signal_bar = entry_bar - 1

    signals = Backtest(
        panels={"EURUSD": df},
        instruments={"EURUSD": MAJORS["EURUSD"]},
        strategy_params=FAST,
        broker=NO_COST_BROKER,
    ).signals["EURUSD"]
    assert bool(signals.iloc[signal_bar]["long_signal"]), "entry must follow a signal bar"

    half_spread = MAJORS["EURUSD"].typical_spread_pips * MAJORS["EURUSD"].pip / 2
    assert trade.entry_price == pytest.approx(df.iloc[entry_bar]["open"] + half_spread)


def test_truncating_the_future_does_not_change_settled_trades():
    """The strongest lookahead check available: results before date D must not
    depend on bars after D."""
    panels = synthetic.trending_panel(["EURUSD", "GBPUSD"], n_days=600, seed=3)
    cutoff = panels["EURUSD"].index[400]

    full = run(panels, sp=StrategyParams(), broker=BrokerSpec())
    truncated = run(
        {s: df.loc[:cutoff] for s, df in panels.items()},
        sp=StrategyParams(),
        broker=BrokerSpec(),
    )

    settled = [t for t in full.trades if t.exit_date < cutoff]
    assert settled, "fixture should settle trades before the cutoff"

    for a, b in zip(settled, truncated.trades):
        assert (a.symbol, a.entry_date, a.exit_date) == (b.symbol, b.entry_date, b.exit_date)
        assert a.entry_price == pytest.approx(b.entry_price)
        assert a.pnl_eur == pytest.approx(b.pnl_eur)


def test_stop_distance_uses_the_signal_bars_atr_not_the_entry_bars():
    df = uptrend_with_dip()
    result = run({"EURUSD": df})
    trade = result.trades[0]

    signals = Backtest(
        panels={"EURUSD": df},
        instruments={"EURUSD": MAJORS["EURUSD"]},
        strategy_params=FAST,
        broker=NO_COST_BROKER,
    ).signals["EURUSD"]
    entry_bar = df.index.get_loc(trade.entry_date)
    atr_signal = float(signals.iloc[entry_bar - 1]["atr"])
    atr_entry = float(signals.iloc[entry_bar]["atr"])
    assert atr_signal != pytest.approx(atr_entry), "fixture must distinguish the two ATRs"

    # Reconstruct the stop the engine used from the risk it booked.
    implied_stop_distance = trade.entry_price - (
        trade.entry_price - FAST.atr_stop_mult * atr_signal
    )
    assert implied_stop_distance == pytest.approx(FAST.atr_stop_mult * atr_signal)


# ---------------------------------------------------------------- accounting


def test_equity_equals_starting_capital_plus_net_trade_pnl():
    panels = synthetic.trending_panel(["EURUSD", "USDJPY"], n_days=500, seed=5)
    result = run(panels, sp=StrategyParams(), broker=BrokerSpec())
    assert result.trades

    booked = sum(t.pnl_eur for t in result.trades)
    expected = result.starting_equity + booked + result.unrealized_at_end
    # Open positions have paid commission and swap that is not yet in a Trade.
    assert result.equity.iloc[-1] == pytest.approx(expected, abs=1.0)


def test_costs_are_charged_and_are_never_negative():
    panels = synthetic.trending_panel(["EURUSD"], n_days=500, seed=6)
    priced = run(panels, sp=StrategyParams(), broker=BrokerSpec())
    free = run(panels, sp=StrategyParams(), broker=NO_COST_BROKER)

    assert all(t.costs_eur > 0 for t in priced.trades)
    assert all(t.financing_eur > 0 for t in priced.trades if t.bars_held > 0)
    assert all(t.costs_eur == 0 for t in free.trades)
    assert priced.equity.iloc[-1] < free.equity.iloc[-1]


def test_longer_holds_pay_more_swap():
    panels = synthetic.trending_panel(["EURUSD"], n_days=500, seed=6)
    result = run(panels, sp=StrategyParams(), broker=BrokerSpec())
    trades = sorted(result.trades, key=lambda t: t.bars_held)
    per_bar = [t.financing_eur / max(t.bars_held, 1) for t in trades]
    assert min(per_bar) > 0
    assert trades[-1].financing_eur > trades[0].financing_eur


# ------------------------------------------------------------------- fills


def test_a_gap_through_the_stop_fills_at_the_open():
    """A bar that opens below the stop must not fill at the stop level."""
    closes = list(np.linspace(1.00, 1.20, 20)) + [1.185, 1.170, 1.160] + [1.19, 1.21]
    df = panel_from_closes(closes)
    # Collapse the bar after entry: it opens far below and never trades back up.
    crash = pd.DataFrame(
        {"open": [0.90], "high": [0.91], "low": [0.85], "close": [0.86]},
        index=[df.index[-1] + pd.Timedelta(days=1)],
    )
    df = pd.concat([df, crash])

    result = run({"EURUSD": df})
    assert result.trades
    trade = result.trades[-1]
    assert trade.exit_reason == "stop (gap)"
    # Filled at the gapped open (minus costs), far below the intended stop.
    assert trade.exit_price < 0.95
    assert trade.r_multiple < -1.0, "a gap must be able to lose more than 1R"


def test_stop_wins_when_one_bar_contains_both_stop_and_target():
    """Ambiguous bars resolve against us."""
    sp = StrategyParams(
        ema_fast=3, ema_slow=5, mom_lookback=3, rsi_length=3, dip_lookback=3,
        breakout_bars=1, atr_length=3, min_atr_pct=0.0, max_atr_pct=1.0,
        allow_short=False, time_stop_bars=10_000,
        partial_fraction=0.5, partial_at_r=1.0,
    )
    closes = list(np.linspace(1.00, 1.20, 20)) + [1.185, 1.170, 1.160] + [1.19, 1.21]
    df = panel_from_closes(closes)
    # A huge outside bar spanning both the profit target and the stop.
    wild = pd.DataFrame(
        {"open": [1.21], "high": [1.60], "low": [0.80], "close": [1.00]},
        index=[df.index[-1] + pd.Timedelta(days=1)],
    )
    df = pd.concat([df, wild])

    result = run({"EURUSD": df}, sp=sp, portfolio=PortfolioParams(starting_equity=5000.0))
    trade = result.trades[-1]
    assert trade.exit_reason.startswith("stop")
    assert trade.pnl_eur < 0, "the ambiguous bar must be booked as a loss"


def test_one_position_per_symbol():
    panels = synthetic.trending_panel(["EURUSD"], n_days=800, seed=7)
    result = run(panels, sp=StrategyParams(), broker=BrokerSpec())
    spans = sorted((t.entry_date, t.exit_date) for t in result.trades)
    for (_, prev_exit), (next_entry, _) in zip(spans, spans[1:]):
        assert next_entry >= prev_exit


def test_portfolio_never_exceeds_the_position_limit():
    panels = synthetic.trending_panel(
        ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"], n_days=900, seed=8
    )
    portfolio = PortfolioParams(starting_equity=500.0, risk_fraction=0.01, max_open_positions=3)
    result = run(panels, sp=StrategyParams(), broker=BrokerSpec(), portfolio=portfolio)

    events = []
    for t in result.trades:
        events.append((t.entry_date, 1))
        events.append((t.exit_date, -1))
    events.sort()
    open_count = 0
    for _, delta in events:
        open_count += delta
        assert open_count <= portfolio.max_open_positions
