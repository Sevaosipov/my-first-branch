"""Broker model: fills, commission, and overnight financing.

Multi-week holds change which costs matter. Measured on this strategy (see
RESULTS.md), overnight financing is 94% of total trading cost and spread,
slippage and commission together are the other 6%. A backtest that models the
spread carefully and waves at swap has mis-modelled almost the entire bill.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .instruments import Instrument


@dataclass
class BrokerSpec:
    """Execution assumptions.

    Defaults describe a retail ECN-style account that supports *nano* lots.
    `lot_step_units` is the single most consequential number in this file for a
    small account: see sizing.py.
    """

    # 100 units = 0.001 lot ("nano"). Use 1000 for a 0.01-lot ("micro") broker.
    lot_step_units: int = 100
    min_units: int = 100
    max_leverage: float = 30.0  # ESMA retail cap on majors
    margin_utilisation_cap: float = 0.35  # never commit more than this of equity

    # Slippage added to the spread on every fill, in pips. Stop exits get an
    # extra dose because they execute into a move that is going against you.
    slippage_pips: float = 0.2
    stop_slippage_pips: float = 0.5

    # Round-turn commission as a fraction of notional, charged per side.
    # 3.50 per 100k per side is a common raw-spread rate.
    commission_rate_per_side: float = 3.5 / 100_000

    # Overnight financing, in pips per night. Positive = charged against you.
    #
    # Measured on this strategy, swap is ~95% of total trading cost: about
    # 0.09R per trade against 0.005R for spread, slippage and commission
    # combined. At a 15-day holding period the spread everyone shops for is
    # noise and the financing nobody reads is the whole bill.
    #
    # `swap_table` maps SYMBOL -> (long_pips_per_night, short_pips_per_night),
    # where a NEGATIVE value is a credit you receive. Copy the numbers from
    # your broker's contract specification; they differ by a factor of three
    # between brokers and they change with central bank rates. Leave it empty
    # and the flat conservative default applies to both directions.
    swap_pips_per_night: float = 0.5
    swap_table: dict[str, tuple[float, float]] = field(default_factory=dict)
    triple_swap_weekday: int = 2  # Wednesday carries the weekend (Mon=0)

    def swap_pips(self, symbol: str, direction: int) -> float:
        """Financing in pips per night for this symbol and side."""
        entry = self.swap_table.get(symbol)
        if entry is None:
            return self.swap_pips_per_night
        return entry[0] if direction == 1 else entry[1]

    def spread_price(self, inst: Instrument) -> float:
        return inst.typical_spread_pips * inst.pip

    def fill_price(
        self,
        mid: float,
        direction: int,
        inst: Instrument,
        *,
        is_stop: bool = False,
    ) -> float:
        """Price actually paid/received, given a mid-market reference price.

        Buys cross the spread upward, sells downward, and both give up
        slippage. Applied identically on entry and exit, so a round trip pays
        the full spread once.
        """
        slip_pips = self.stop_slippage_pips if is_stop else self.slippage_pips
        adverse = (self.spread_price(inst) / 2.0) + slip_pips * inst.pip
        return mid + direction * adverse

    def commission_eur(self, units: float, price: float, inst: Instrument, converter, date) -> float:
        """One side's commission, in EUR."""
        notional_quote = abs(units) * price
        notional_eur = converter.to_eur(notional_quote, inst.quote, date)
        return notional_eur * self.commission_rate_per_side

    def financing_eur(
        self,
        units: float,
        inst: Instrument,
        converter,
        start: pd.Timestamp,
        end: pd.Timestamp,
        direction: int = 1,
    ) -> float:
        """Net swap over a hold, in EUR. Negative means a credit received.

        Counts calendar nights between the two dates and triples Wednesday to
        match the T+2 settlement convention every retail broker applies.
        """
        if end <= start:
            return 0.0
        nights = 0
        for day in pd.date_range(start, end - pd.Timedelta(days=1), freq="D"):
            nights += 3 if day.weekday() == self.triple_swap_weekday else 1
        charge_quote = nights * self.swap_pips(inst.symbol, direction) * inst.pip * abs(units)
        return converter.to_eur(charge_quote, inst.quote, end)


# A broker whose smallest ticket is a micro lot. Used in the report to show
# what happens to a 500 EUR account that cannot trade nano lots.
MICRO_LOT_BROKER = BrokerSpec(lot_step_units=1000, min_units=1000)
