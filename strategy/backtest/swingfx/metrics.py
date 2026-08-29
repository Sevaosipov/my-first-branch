"""Performance metrics and a bootstrap of what a 500 EUR account should expect.

The point-estimate CAGR of a backtest with ~200 trades carries an enormous
standard error, and quoting it alone is the main way strategy reports mislead.
`monte_carlo` resamples the realised R-multiples to show the *distribution* of
outcomes the same edge can produce -- including how often it produces a
drawdown that would end a small account.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0


def summarize(result) -> dict:
    """Headline metrics for a completed backtest."""
    equity = result.equity
    trades = result.trades_frame
    out: dict = {"trades": int(len(trades))}

    if equity.empty:
        return out

    start, end = float(equity.iloc[0]), float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    returns = equity.pct_change().dropna()

    out["start_equity"] = start
    out["end_equity"] = end
    out["total_return"] = end / start - 1.0
    out["cagr"] = (end / start) ** (1.0 / years) - 1.0 if start > 0 and end > 0 else float("nan")
    out["years"] = years
    out["max_drawdown"] = float(drawdown_series(equity).min())

    if len(returns) > 1 and returns.std() > 0:
        out["sharpe"] = float(returns.mean() / returns.std() * np.sqrt(TRADING_DAYS))
        downside = returns[returns < 0]
        out["sortino"] = (
            float(returns.mean() / downside.std() * np.sqrt(TRADING_DAYS))
            if len(downside) > 1 and downside.std() > 0
            else float("nan")
        )
        out["volatility"] = float(returns.std() * np.sqrt(TRADING_DAYS))
    else:
        out["sharpe"] = out["sortino"] = out["volatility"] = float("nan")

    out["mar"] = out["cagr"] / abs(out["max_drawdown"]) if out["max_drawdown"] < 0 else float("nan")

    if trades.empty:
        return out

    r = trades["r_multiple"]
    wins, losses = r[r > 0], r[r <= 0]
    out["win_rate"] = float(len(wins) / len(r))
    out["avg_win_r"] = float(wins.mean()) if len(wins) else 0.0
    out["avg_loss_r"] = float(losses.mean()) if len(losses) else 0.0
    out["expectancy_r"] = float(r.mean())
    gross_win = float(trades.loc[trades["pnl_eur"] > 0, "pnl_eur"].sum())
    gross_loss = float(-trades.loc[trades["pnl_eur"] <= 0, "pnl_eur"].sum())
    out["profit_factor"] = gross_win / gross_loss if gross_loss > 0 else float("inf")
    out["avg_bars_held"] = float(trades["bars_held"].mean())
    out["median_bars_held"] = float(trades["bars_held"].median())
    out["total_costs_eur"] = float(trades["costs_eur"].sum())
    out["total_financing_eur"] = float(trades["financing_eur"].sum())
    # Costs expressed in R are the number that matters: they are what the
    # gross edge has to clear before a single euro reaches the account.
    risk_per_trade = (trades["pnl_eur"] / trades["r_multiple"].replace(0.0, np.nan)).abs()
    avg_risk = float(risk_per_trade.median()) if risk_per_trade.notna().any() else float("nan")
    if avg_risk and np.isfinite(avg_risk) and avg_risk > 0:
        out["avg_cost_r"] = float(trades["costs_eur"].mean() / avg_risk)
        out["avg_financing_r"] = float(trades["financing_eur"].mean() / avg_risk)
    out["trades_per_year"] = len(trades) / years
    out["exit_reasons"] = trades["exit_reason"].value_counts().to_dict()
    return out


def per_symbol(result) -> pd.DataFrame:
    trades = result.trades_frame
    if trades.empty:
        return pd.DataFrame()
    grouped = trades.groupby("symbol").agg(
        trades=("r_multiple", "size"),
        win_rate=("r_multiple", lambda s: float((s > 0).mean())),
        expectancy_r=("r_multiple", "mean"),
        total_r=("r_multiple", "sum"),
        pnl_eur=("pnl_eur", "sum"),
        avg_bars=("bars_held", "mean"),
    )
    return grouped.sort_values("total_r", ascending=False)


def monte_carlo(
    r_multiples: np.ndarray | pd.Series,
    *,
    starting_equity: float = 500.0,
    risk_fraction: float = 0.01,
    trades_per_year: float = 40.0,
    years: float = 3.0,
    simulations: int = 5000,
    ruin_threshold: float = 0.5,
    seed: int = 7,
) -> dict:
    """Bootstrap the trade sequence under fixed-fractional sizing.

    Each simulation draws `trades_per_year * years` R-multiples with
    replacement and compounds `equity *= 1 + R * risk_fraction`. This keeps the
    edge fixed and varies only the order and mix of trades, which is the honest
    way to ask "what range of outcomes does this edge produce?".
    """
    r = np.asarray(r_multiples, dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return {}

    rng = np.random.default_rng(seed)
    n = max(int(round(trades_per_year * years)), 1)
    draws = rng.choice(r, size=(simulations, n), replace=True)

    growth = 1.0 + draws * risk_fraction
    # A single trade cannot lose more than the account.
    growth = np.clip(growth, 0.0, None)
    paths = starting_equity * np.cumprod(growth, axis=1)

    running_peak = np.maximum.accumulate(paths, axis=1)
    max_dd = (paths / running_peak - 1.0).min(axis=1)
    final = paths[:, -1]
    ruined = (paths <= starting_equity * ruin_threshold).any(axis=1)

    return {
        "simulations": simulations,
        "trades_simulated": n,
        "risk_fraction": risk_fraction,
        "median_final_equity": float(np.median(final)),
        "p05_final_equity": float(np.percentile(final, 5)),
        "p95_final_equity": float(np.percentile(final, 95)),
        "median_cagr": float(np.median((final / starting_equity) ** (1.0 / years) - 1.0)),
        "prob_profit": float((final > starting_equity).mean()),
        "median_max_drawdown": float(np.median(max_dd)),
        "p95_max_drawdown": float(np.percentile(max_dd, 5)),
        f"prob_drawdown_below_{int(ruin_threshold * 100)}pct": float(ruined.mean()),
    }


def risk_dial(r_multiples, *, starting_equity=500.0, trades_per_year=40.0, years=3.0,
              fractions=(0.005, 0.01, 0.02, 0.03, 0.05)) -> pd.DataFrame:
    """The core trade-off table: more risk per trade buys return and ruin alike."""
    rows = []
    for f in fractions:
        mc = monte_carlo(
            r_multiples,
            starting_equity=starting_equity,
            risk_fraction=f,
            trades_per_year=trades_per_year,
            years=years,
        )
        if not mc:
            continue
        rows.append(
            {
                "risk_per_trade": f"{f:.1%}",
                "median_final_eur": round(mc["median_final_equity"], 0),
                "p05_final_eur": round(mc["p05_final_equity"], 0),
                "median_cagr": f"{mc['median_cagr']:.1%}",
                "prob_profit": f"{mc['prob_profit']:.0%}",
                "median_max_dd": f"{mc['median_max_drawdown']:.0%}",
                "worst_5pct_dd": f"{mc['p95_max_drawdown']:.0%}",
                "prob_halved": f"{mc['prob_drawdown_below_50pct']:.0%}",
            }
        )
    return pd.DataFrame(rows)
