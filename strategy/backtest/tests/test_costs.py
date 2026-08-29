"""Financing: per-side swap rates, credits, and the carry gate.

Swap is ~94% of this strategy's trading cost, so these are not edge cases.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swingfx import MAJORS, Backtest, BrokerSpec, PortfolioParams, StrategyParams  # noqa: E402
from swingfx import synthetic  # noqa: E402
from swingfx.instruments import EurConverter  # noqa: E402

EURUSD = MAJORS["EURUSD"]


def test_flat_rate_applies_to_both_sides():
    broker = BrokerSpec(swap_pips_per_night=0.4)
    assert broker.swap_pips("EURUSD", 1) == 0.4
    assert broker.swap_pips("EURUSD", -1) == 0.4


def test_swap_table_overrides_per_side():
    broker = BrokerSpec(swap_table={"EURUSD": (0.9, -0.3)})
    assert broker.swap_pips("EURUSD", 1) == 0.9
    assert broker.swap_pips("EURUSD", -1) == -0.3
    assert broker.swap_pips("GBPUSD", 1) == broker.swap_pips_per_night


def test_wednesday_is_charged_triple():
    broker = BrokerSpec(swap_pips_per_night=1.0)
    converter = EurConverter({})
    # Tuesday -> Wednesday is one night; Wednesday -> Thursday is three.
    tue_wed = broker.financing_eur(
        10_000, EURUSD, converter, pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")
    )
    wed_thu = broker.financing_eur(
        10_000, EURUSD, converter, pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-04")
    )
    assert wed_thu == pytest.approx(3 * tue_wed)


def test_weekend_costs_three_nights():
    broker = BrokerSpec(swap_pips_per_night=1.0)
    converter = EurConverter({})
    fri_mon = broker.financing_eur(
        10_000, EURUSD, converter, pd.Timestamp("2024-01-05"), pd.Timestamp("2024-01-08")
    )
    thu_fri = broker.financing_eur(
        10_000, EURUSD, converter, pd.Timestamp("2024-01-04"), pd.Timestamp("2024-01-05")
    )
    assert fri_mon == pytest.approx(3 * thu_fri)


def test_a_positive_carry_side_is_a_credit():
    broker = BrokerSpec(swap_table={"EURUSD": (1.0, -0.5)})
    converter = EurConverter({})
    args = (10_000, EURUSD, converter, pd.Timestamp("2024-01-08"), pd.Timestamp("2024-01-09"))
    assert broker.financing_eur(*args, direction=1) > 0
    assert broker.financing_eur(*args, direction=-1) < 0


def test_carry_gate_rejects_expensive_sides():
    """With a punitive long swap and the gate on, long trades are skipped."""
    panels = synthetic.trending_panel(["EURUSD", "GBPUSD"], n_days=700, seed=11)
    broker = BrokerSpec(swap_table={"EURUSD": (6.0, 0.0), "GBPUSD": (6.0, 0.0)})

    def run(max_financing_r):
        return Backtest(
            panels=panels,
            instruments={s: MAJORS[s] for s in panels},
            strategy_params=StrategyParams(),
            portfolio=PortfolioParams(
                starting_equity=500.0, risk_fraction=0.01, max_financing_r=max_financing_r
            ),
            broker=broker,
        ).run()

    ungated = run(None)
    gated = run(0.25)

    assert any(t.direction == 1 for t in ungated.trades), "fixture needs long trades"
    assert not any(t.direction == 1 for t in gated.trades)
    assert any("negative carry" in str(s["reason"]) for s in gated.skips)


def test_carry_gate_off_by_default():
    panels = synthetic.trending_panel(["EURUSD"], n_days=500, seed=12)
    result = Backtest(
        panels=panels,
        instruments={"EURUSD": EURUSD},
        strategy_params=StrategyParams(),
        portfolio=PortfolioParams(starting_equity=500.0),
        broker=BrokerSpec(),
    ).run()
    assert not any("negative carry" in str(s["reason"]) for s in result.skips)


def test_financing_dominates_transaction_cost_at_this_holding_period():
    """The measurement that drives the whole cost section of the README."""
    panels = synthetic.trending_panel(
        ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"], n_days=1500, seed=13
    )
    result = Backtest(
        panels=panels,
        instruments={s: MAJORS[s] for s in panels},
        strategy_params=StrategyParams(),
        portfolio=PortfolioParams(starting_equity=500.0),
        broker=BrokerSpec(),
    ).run()
    trades = result.trades_frame
    assert not trades.empty

    swap = trades["financing_eur"].sum()
    total = trades["costs_eur"].sum()
    assert swap / total > 0.8, "swap should dominate cost on multi-week holds"
