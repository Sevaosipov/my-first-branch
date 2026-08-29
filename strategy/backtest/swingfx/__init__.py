"""Trend Pullback Continuation -- a daily-bar FX swing strategy and backtester."""

from .broker import MICRO_LOT_BROKER, BrokerSpec
from .engine import Backtest, BacktestResult, PortfolioParams, Position, Trade
from .instruments import MAJORS, EurConverter, Instrument
from .metrics import monte_carlo, per_symbol, risk_dial, summarize
from .sizing import min_equity_for_risk, size_position
from .strategy import StrategyParams, compute_features, generate_signals

__all__ = [
    "Backtest",
    "BacktestResult",
    "BrokerSpec",
    "EurConverter",
    "Instrument",
    "MAJORS",
    "MICRO_LOT_BROKER",
    "PortfolioParams",
    "Position",
    "StrategyParams",
    "Trade",
    "compute_features",
    "generate_signals",
    "min_equity_for_risk",
    "monte_carlo",
    "per_symbol",
    "risk_dial",
    "size_position",
    "summarize",
]
