#!/usr/bin/env python3
"""Sanity checks on the backtester itself, using generated prices.

    python validate.py            # both checks
    python validate.py --seeds 12 # more paths, tighter error bars

Two questions, neither of which is "is this strategy profitable?":

NULL TEST -- run the strategy on driftless random walks. There is no trend to
follow, so the only correct answer is "loses roughly its costs". A strategy
that makes money on random walks has a lookahead bug, and no amount of
plausible-looking equity curve elsewhere would redeem it.

POWER TEST -- run it on paths whose drift switches between up/flat/down
regimes, so momentum genuinely exists. A correct implementation should extract
some of it. Passing says the machinery works. It says nothing about real FX:
the generator's trends are an assumption, not evidence.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from swingfx import MAJORS, Backtest, BrokerSpec, PortfolioParams, StrategyParams  # noqa: E402
from swingfx import synthetic  # noqa: E402

UNIVERSE = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]


def collect(generator, seeds: list[int], days: int, broker: BrokerSpec):
    r_multiples, costs = [], []
    for seed in seeds:
        panels = generator(UNIVERSE, n_days=days, seed=seed)
        result = Backtest(
            panels=panels,
            instruments={s: MAJORS[s] for s in panels},
            strategy_params=StrategyParams(),
            portfolio=PortfolioParams(starting_equity=500.0, risk_fraction=0.01),
            broker=broker,
        ).run()
        frame = result.trades_frame
        if frame.empty:
            continue
        r_multiples.append(frame["r_multiple"].to_numpy())
        risk = (frame["pnl_eur"] / frame["r_multiple"].replace(0.0, np.nan)).abs().median()
        if np.isfinite(risk) and risk > 0:
            costs.append(frame["costs_eur"].to_numpy() / risk)
    if not r_multiples:
        return np.array([]), np.array([])
    return np.concatenate(r_multiples), (np.concatenate(costs) if costs else np.array([]))


def describe(label: str, r: np.ndarray, cost_r: np.ndarray) -> dict:
    if r.size == 0:
        print(f"{label}: no trades generated")
        return {}
    mean = float(r.mean())
    stderr = float(r.std(ddof=1) / np.sqrt(r.size))
    t = mean / stderr if stderr > 0 else float("nan")
    cost = float(cost_r.mean()) if cost_r.size else float("nan")
    print(
        f"{label:<10} trades={r.size:>5}  expectancy={mean:+.3f}R"
        f"  (+/-{1.96 * stderr:.3f} at 95%)  t={t:+.2f}"
        f"  win={100 * (r > 0).mean():.1f}%  cost={cost:.3f}R"
    )
    return {"mean": mean, "stderr": stderr, "t": t, "cost": cost, "n": int(r.size)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=int, default=6)
    parser.add_argument("--days", type=int, default=2000)
    args = parser.parse_args(argv)

    seeds = list(range(1, args.seeds + 1))
    broker = BrokerSpec()

    print(f"\n{args.seeds} paths x {args.days} bars x {len(UNIVERSE)} pairs\n")
    print("-- NULL TEST (random walk: no trend exists) " + "-" * 24)
    null_r, null_cost = collect(synthetic.random_walk_panel, seeds, args.days, broker)
    null = describe("random", null_r, null_cost)

    free_r, _ = collect(
        synthetic.random_walk_panel,
        seeds,
        args.days,
        BrokerSpec(
            slippage_pips=0.0,
            stop_slippage_pips=0.0,
            commission_rate_per_side=0.0,
            swap_pips_per_night=0.0,
        ),
    )
    gross = describe("costless", free_r, np.array([]))

    print("\n-- POWER TEST (regime drift: trend exists) " + "-" * 25)
    trend_r, trend_cost = collect(synthetic.trending_panel, seeds, args.days, broker)
    trend = describe("trending", trend_r, trend_cost)

    print("\n-- VERDICT " + "-" * 56)
    ok = True

    if gross:
        if abs(gross["t"]) < 2.0:
            print("PASS  costless expectancy on random walks is indistinguishable from zero")
        else:
            ok = False
            print(f"FAIL  costless random-walk expectancy is {gross['mean']:+.3f}R "
                  f"(t={gross['t']:+.2f}) -- suspect lookahead")

    if null:
        implied = null["mean"] - (gross["mean"] if gross else 0.0)
        print(f"INFO  costs cut expectancy by {abs(implied):.3f}R per trade "
              f"(modelled cost {null['cost']:.3f}R)")

    if trend and null:
        edge = trend["mean"] - null["mean"]
        print(f"INFO  gross edge attributable to trend: {edge:+.3f}R per trade")
        if trend["mean"] > null["mean"]:
            print("PASS  the strategy extracts more from trending paths than from random ones")
        else:
            ok = False
            print("FAIL  trending paths are no better than random -- entry logic is not working")
        if abs(trend["t"]) < 2.0:
            print("NOTE  net expectancy on trending paths is NOT statistically significant. "
                  "\n      These synthetic trends are weaker than real FX trends have been, "
                  "\n      but do not read this as evidence of a live edge either way.")

    print("-" * 68)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
