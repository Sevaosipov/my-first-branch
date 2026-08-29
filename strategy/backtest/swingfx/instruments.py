"""Instrument specifications and account-currency conversion.

The account is denominated in EUR. A forex position's P&L is earned in the
pair's *quote* currency (the second one: USD for EURUSD, JPY for USDJPY), so
every price move has to be converted to EUR before it touches the equity curve.
Backtests that skip this step are wrong by however much EURUSD moved over the
sample -- roughly 30% across a decade, which is not a rounding error.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Instrument:
    """A tradeable FX pair.

    `pip` is the conventional pip size (0.0001, or 0.01 for JPY quotes).
    `typical_spread_pips` is a retail-broker round-turn spread on the daily
    close for a small account -- deliberately pessimistic, since a 500 EUR
    account is not getting institutional pricing.
    """

    symbol: str
    base: str
    quote: str
    pip: float
    typical_spread_pips: float

    @property
    def is_jpy_quote(self) -> bool:
        return self.quote == "JPY"


# The five tightest-spread majors. A 500 EUR account cannot afford the 2-3 pip
# spreads on crosses like GBPJPY, and cannot afford their 150+ pip stops either.
MAJORS: dict[str, Instrument] = {
    "EURUSD": Instrument("EURUSD", "EUR", "USD", 0.0001, 0.6),
    "GBPUSD": Instrument("GBPUSD", "GBP", "USD", 0.0001, 0.9),
    "USDJPY": Instrument("USDJPY", "USD", "JPY", 0.01, 0.7),
    "AUDUSD": Instrument("AUDUSD", "AUD", "USD", 0.0001, 0.8),
    "USDCAD": Instrument("USDCAD", "USD", "CAD", 0.0001, 1.0),
    "USDCHF": Instrument("USDCHF", "USD", "CHF", 0.0001, 1.0),
    "NZDUSD": Instrument("NZDUSD", "NZD", "USD", 0.0001, 1.3),
}


class EurConverter:
    """Converts an amount in some quote currency into EUR, per date.

    Rates are derived from whatever price series the backtest already loaded --
    EURUSD gives USD/EUR directly, and combining it with USDJPY gives JPY/EUR.
    When a currency cannot be derived, `fallback` is used and the currency is
    recorded in `missing` so the report can disclose the approximation rather
    than hiding it.
    """

    # Rough long-run averages, used only when a series is unavailable.
    FALLBACKS = {
        "EUR": 1.0,
        "USD": 1.15,
        "JPY": 145.0,
        "GBP": 0.85,
        "CHF": 1.00,
        "CAD": 1.50,
        "AUD": 1.65,
        "NZD": 1.80,
    }

    def __init__(self, panels: dict[str, pd.DataFrame]):
        self._per_eur: dict[str, pd.Series] = {}
        self.missing: set[str] = set()

        closes = {sym: df["close"] for sym, df in panels.items() if "close" in df}
        eurusd = closes.get("EURUSD")

        if eurusd is not None:
            self._per_eur["USD"] = eurusd
            # X per EUR = (USD per EUR) * (X per USD)
            for sym, series in closes.items():
                inst = MAJORS.get(sym)
                if inst is None:
                    continue
                if inst.base == "USD":
                    self._per_eur[inst.quote] = eurusd * series
                elif inst.quote == "USD" and inst.base != "EUR":
                    # EURUSD / BASEUSD = BASE per EUR
                    self._per_eur[inst.base] = eurusd / series

    def units_per_eur(self, currency: str, date: pd.Timestamp) -> float:
        """How many units of `currency` one EUR buys on `date`."""
        if currency == "EUR":
            return 1.0
        series = self._per_eur.get(currency)
        if series is not None:
            # `asof` walks back to the last known value, so a missing bar on a
            # local holiday does not blow up the conversion.
            value = series.asof(date)
            if pd.notna(value) and value > 0:
                return float(value)
        self.missing.add(currency)
        return self.FALLBACKS.get(currency, 1.0)

    def to_eur(self, amount: float, currency: str, date: pd.Timestamp) -> float:
        return amount / self.units_per_eur(currency, date)
