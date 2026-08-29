"""Position sizing under a real broker's lot granularity.

This is where a 500 EUR account lives or dies, and it is the part almost every
published forex strategy waves through with "risk 1% per trade".

Worked example. EURUSD, ATR(14) = 70 pips, stop = 2 x ATR = 140 pips.
Risking 1% of 500 EUR is 5 EUR, so you need a position that loses 5 EUR over
140 pips -- about 0.036 EUR per pip, which is **389 units** of EURUSD.

  * A broker with 0.01-lot (1,000-unit) minimums cannot sell you that. Its
    smallest ticket loses 12.84 EUR on the same stop: **2.6% of the account**,
    not 1%. Six losers in a row -- routine for a 40%-win trend system -- is
    then -15% instead of -6%.
  * A broker with 0.001-lot (100-unit) steps fills 300 units, risking 3.85 EUR.
    Rounding down costs you a quarter of the intended size, which is the price
    of never exceeding the budget.

So the sizing rule has a third outcome besides "buy N units": *refuse the
trade*. `size_position` returns 0 units when the smallest legal ticket would
breach the risk budget, and the backtest records the skip. A strategy that
cannot be sized is not a strategy you can trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from .broker import BrokerSpec
from .instruments import Instrument


@dataclass(frozen=True)
class SizingResult:
    units: float
    risk_eur: float
    skipped_reason: str | None = None

    @property
    def tradeable(self) -> bool:
        return self.units > 0


def size_position(
    *,
    equity_eur: float,
    risk_fraction: float,
    stop_distance_price: float,
    entry_price: float,
    instrument: Instrument,
    broker: BrokerSpec,
    converter,
    date: pd.Timestamp,
    max_risk_overshoot: float = 1.25,
) -> SizingResult:
    """Units to trade, constrained by risk budget, lot step, and margin.

    `max_risk_overshoot` is the tolerance for the minimum-ticket problem: if
    the broker's smallest position risks more than 1.25x the intended budget,
    the trade is skipped rather than silently oversized.
    """
    if stop_distance_price <= 0 or equity_eur <= 0:
        return SizingResult(0.0, 0.0, "non-positive stop distance or equity")

    budget_eur = equity_eur * risk_fraction
    quote_per_eur = converter.units_per_eur(instrument.quote, date)
    # Losing `stop_distance_price` of quote currency per unit held.
    loss_per_unit_eur = stop_distance_price / quote_per_eur
    if loss_per_unit_eur <= 0:
        return SizingResult(0.0, 0.0, "invalid conversion rate")

    raw_units = budget_eur / loss_per_unit_eur

    # Margin cap: notional is `units` of the base currency.
    base_per_eur = converter.units_per_eur(instrument.base, date)
    max_notional_eur = equity_eur * broker.max_leverage * broker.margin_utilisation_cap
    max_units_by_margin = max_notional_eur * base_per_eur

    target_units = min(raw_units, max_units_by_margin)

    step = broker.lot_step_units
    units = math.floor(target_units / step) * step

    if units < broker.min_units:
        # The smallest legal ticket is the minimum rounded UP to a whole lot
        # step -- you cannot trade 100 units at a broker that deals in 1,000s.
        smallest = float(math.ceil(broker.min_units / step) * step)
        smallest_risk = smallest * loss_per_unit_eur
        if smallest_risk <= budget_eur * max_risk_overshoot and smallest <= max_units_by_margin:
            units = smallest
        else:
            return SizingResult(
                0.0,
                0.0,
                f"minimum ticket ({broker.min_units:,.0f} units) risks "
                f"{smallest_risk:.2f} EUR vs budget {budget_eur:.2f} EUR",
            )

    return SizingResult(float(units), units * loss_per_unit_eur, None)


def min_equity_for_risk(
    *,
    risk_fraction: float,
    stop_distance_price: float,
    instrument: Instrument,
    broker: BrokerSpec,
    quote_per_eur: float,
) -> float:
    """Smallest account that can take this trade at the intended risk.

    Inverts `size_position`: the minimum ticket must not risk more than
    `risk_fraction` of equity.
    """
    loss_per_unit_eur = stop_distance_price / quote_per_eur
    min_ticket_risk = broker.min_units * loss_per_unit_eur
    return min_ticket_risk / risk_fraction
