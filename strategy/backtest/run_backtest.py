#!/usr/bin/env python3
"""Run the TPC strategy over a directory of daily FX CSVs.

    python run_backtest.py --data ./data --start 2010-01-01 --end 2019-12-31
    python run_backtest.py --data ./data --start 2020-01-01   # out of sample
    python run_backtest.py --synthetic trend                  # no data needed

Fit on the in-sample window, then look at the out-of-sample window exactly
once. Every extra peek at out-of-sample results turns them into in-sample ones.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from swingfx import (  # noqa: E402
    MAJORS,
    MICRO_LOT_BROKER,
    Backtest,
    BrokerSpec,
    PortfolioParams,
    StrategyParams,
)
from swingfx import data as data_mod  # noqa: E402
from swingfx import metrics, synthetic  # noqa: E402


def build_args() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--data", help="directory of <SYMBOL>.csv daily OHLC files")
    src.add_argument("--synthetic", choices=["random", "trend"], help="generate prices instead")

    p.add_argument("--symbols", nargs="*", default=None, help="subset of pairs to trade")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--equity", type=float, default=500.0)
    p.add_argument("--risk", type=float, default=0.01, help="risk per trade as a fraction")
    p.add_argument("--max-positions", type=int, default=3)
    p.add_argument(
        "--broker",
        choices=["nano", "micro"],
        default="nano",
        help="nano = 0.001 lot steps, micro = 0.01 lot steps (see sizing.py)",
    )
    p.add_argument("--long-only", action="store_true")
    p.add_argument(
        "--swap-json",
        help='JSON file of {"EURUSD": [long_pips_per_night, short_pips_per_night]} '
        "from your broker's contract specs; negative means a credit. Swap is ~94%% "
        "of this strategy's cost, so real numbers here matter more than any "
        "indicator setting.",
    )
    p.add_argument(
        "--max-financing-r",
        type=float,
        default=None,
        help="skip signals whose expected swap over a full hold exceeds this share "
        "of the risk budget (needs --swap-json to be meaningful)",
    )
    p.add_argument("--seed", type=int, default=0, help="seed for --synthetic")
    p.add_argument("--days", type=int, default=2500, help="bars for --synthetic")
    p.add_argument("--json", help="write the summary to this path as JSON")
    p.add_argument("--trades-csv", help="write the trade list to this path")
    return p


def fmt_pct(x) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{x:.2%}"


def fmt_num(x, digits=2) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{x:,.{digits}f}"


def report(result, summary: dict) -> str:
    lines = ["", "=" * 68, "TREND PULLBACK CONTINUATION -- BACKTEST", "=" * 68]

    if result.equity.empty:
        return "\n".join(lines + ["No bars were processed."])

    lines += [
        f"Period            {result.equity.index[0].date()} -> {result.equity.index[-1].date()}"
        f"  ({summary.get('years', 0):.1f}y)",
        f"Equity            {fmt_num(summary.get('start_equity'))} -> {fmt_num(summary.get('end_equity'))} EUR",
        f"Total return      {fmt_pct(summary.get('total_return'))}",
        f"CAGR              {fmt_pct(summary.get('cagr'))}",
        f"Max drawdown      {fmt_pct(summary.get('max_drawdown'))}",
        f"Sharpe / Sortino  {fmt_num(summary.get('sharpe'))} / {fmt_num(summary.get('sortino'))}",
        f"MAR               {fmt_num(summary.get('mar'))}",
        "",
        f"Trades            {summary.get('trades', 0)}"
        f"  ({fmt_num(summary.get('trades_per_year'), 1)}/yr)",
    ]

    if summary.get("trades"):
        lines += [
            f"Win rate          {fmt_pct(summary.get('win_rate'))}",
            f"Expectancy        {fmt_num(summary.get('expectancy_r'), 3)} R per trade",
            f"Avg win / loss    {fmt_num(summary.get('avg_win_r'))}R / {fmt_num(summary.get('avg_loss_r'))}R",
            f"Profit factor     {fmt_num(summary.get('profit_factor'))}",
            f"Holding period    {fmt_num(summary.get('avg_bars_held'), 1)} bars avg,"
            f" {fmt_num(summary.get('median_bars_held'), 1)} median",
            f"Costs paid        {fmt_num(summary.get('total_costs_eur'))} EUR"
            f"  (of which {fmt_num(summary.get('total_financing_eur'))} EUR swap)",
            f"Cost per trade    {fmt_num(summary.get('avg_cost_r'), 3)} R"
            f"  (swap {fmt_num(summary.get('avg_financing_r'), 3)} R)",
            f"Exits             {summary.get('exit_reasons')}",
        ]

        by_symbol = metrics.per_symbol(result)
        if not by_symbol.empty:
            lines += ["", "Per pair:", by_symbol.round(3).to_string()]

        dial = metrics.risk_dial(
            result.trades_frame["r_multiple"],
            starting_equity=result.starting_equity,
            trades_per_year=summary.get("trades_per_year", 40.0),
            years=3.0,
        )
        if not dial.empty:
            lines += [
                "",
                "Bootstrapped 3-year outcomes by risk per trade",
                "(resampled from this run's own R-multiples; not a forecast):",
                dial.to_string(index=False),
            ]

    if result.skips:
        counts = pd.Series([s["reason"] for s in result.skips]).value_counts()
        lines += ["", f"Signals not taken ({len(result.skips)}):", counts.to_string()]

    if result.missing_fx:
        lines += [
            "",
            f"NOTE: no series for {sorted(result.missing_fx)} -- EUR conversion used "
            "fallback rates for those currencies.",
        ]

    lines.append("=" * 68)
    return "\n".join(lines)


def main(argv=None) -> int:
    args = build_args().parse_args(argv)
    symbols = [s.upper() for s in args.symbols] if args.symbols else None

    if args.synthetic:
        universe = symbols or ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]
        gen = synthetic.trending_panel if args.synthetic == "trend" else synthetic.random_walk_panel
        panels = gen(universe, n_days=args.days, seed=args.seed)
    else:
        panels = data_mod.load_directory(args.data, symbols)
        panels = data_mod.slice_dates(panels, args.start, args.end)

    unknown = [s for s in panels if s not in MAJORS]
    if unknown:
        raise SystemExit(f"No instrument spec for {unknown}. Add it to swingfx/instruments.py.")

    # `replace` copies the shared MICRO_LOT_BROKER rather than mutating it.
    broker = replace(MICRO_LOT_BROKER) if args.broker == "micro" else BrokerSpec()
    if args.swap_json:
        table = json.loads(Path(args.swap_json).read_text())
        broker.swap_table = {k.upper(): tuple(v) for k, v in table.items()}

    backtest = Backtest(
        panels=panels,
        instruments={s: MAJORS[s] for s in panels},
        strategy_params=StrategyParams(allow_short=not args.long_only),
        portfolio=PortfolioParams(
            starting_equity=args.equity,
            risk_fraction=args.risk,
            max_open_positions=args.max_positions,
            max_financing_r=args.max_financing_r,
        ),
        broker=broker,
    )
    result = backtest.run()
    summary = metrics.summarize(result)

    print(report(result, summary))

    if args.json:
        payload = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in summary.items()}
        Path(args.json).write_text(json.dumps(payload, indent=2, default=str))
        print(f"\nSummary written to {args.json}")
    if args.trades_csv:
        result.trades_frame.to_csv(args.trades_csv, index=False)
        print(f"Trades written to {args.trades_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
