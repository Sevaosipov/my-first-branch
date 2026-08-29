"""Event-driven daily backtester for the TPC strategy.

Modelling choices, all deliberately pessimistic where there was a choice:

* Signals fire on bar t's close; orders fill at bar t+1's open, and the stop
  distance uses bar t's ATR -- the value actually known when the order is
  placed. Using the entry day's own ATR would quietly leak that day's range
  into the stop.
* Entries are processed *before* exits on a given day, so a slot freed by
  today's exit is not reusable until tomorrow.
* If a bar's range contains both the stop and the profit target, the stop is
  assumed to have been hit first. Daily bars cannot tell us the order, and
  guessing in our own favour is how backtests lie.
* A bar that gaps through the stop fills at the open, not at the stop level.
* Scaling out respects the broker's lot step. On a small account the runner
  often cannot be split at all, and the engine says so rather than pretending
  fractional lots exist.
* Overnight financing is charged every night, tripled on Wednesdays, in both
  directions -- carry is never assumed to pay us.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .broker import BrokerSpec
from .instruments import EurConverter, Instrument
from .sizing import size_position
from .strategy import StrategyParams, generate_signals


@dataclass(frozen=True)
class PortfolioParams:
    """Account-level risk rules, tuned for a small account."""

    starting_equity: float = 500.0
    risk_fraction: float = 0.01
    max_open_positions: int = 3
    max_total_risk: float = 0.03
    max_currency_exposure: int = 2  # net long/short units of any one currency
    weekly_loss_limit: float = 0.05  # pause new entries after this weekly loss
    drawdown_throttle: float = 0.15  # halve risk below this drawdown
    throttle_factor: float = 0.5

    # Carry gate. Financing is ~94% of this strategy's trading cost, so the
    # cheapest available improvement is refusing trades that bleed swap for
    # weeks. Skips a signal whose expected financing over a full holding
    # period exceeds this share of the risk budget. Requires a populated
    # `BrokerSpec.swap_table`; with the flat default rate it is left off,
    # because a flat rate cannot distinguish a good side from a bad one.
    max_financing_r: float | None = None


@dataclass
class PendingOrder:
    symbol: str
    direction: int
    atr_at_signal: float
    momentum: float


@dataclass
class Position:
    symbol: str
    direction: int
    entry_date: pd.Timestamp
    entry_price: float
    units: float
    initial_units: float
    initial_stop: float
    stop: float
    r_price: float  # price distance representing 1R
    initial_risk_eur: float
    extreme: float
    partial_done: bool = False
    bars_held: int = 0
    realized_eur: float = 0.0
    costs_eur: float = 0.0
    financing_eur: float = 0.0

    @property
    def partial_target(self) -> float:
        return self.entry_price + self.direction * self.r_price


@dataclass
class Trade:
    symbol: str
    direction: int
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    units: float
    bars_held: int
    pnl_eur: float
    costs_eur: float
    financing_eur: float
    r_multiple: float
    exit_reason: str


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: list[Trade]
    skips: list[dict] = field(default_factory=list)
    params: dict = field(default_factory=dict)
    missing_fx: set = field(default_factory=set)
    starting_equity: float = 0.0
    open_at_end: int = 0
    unrealized_at_end: float = 0.0

    @property
    def trades_frame(self) -> pd.DataFrame:
        columns = [
            "symbol", "direction", "entry_date", "exit_date", "entry_price",
            "exit_price", "units", "bars_held", "pnl_eur", "costs_eur",
            "financing_eur", "r_multiple", "exit_reason",
        ]
        if not self.trades:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame([t.__dict__ for t in self.trades])[columns]


class Backtest:
    def __init__(
        self,
        panels: dict[str, pd.DataFrame],
        instruments: dict[str, Instrument],
        strategy_params: StrategyParams | None = None,
        portfolio: PortfolioParams | None = None,
        broker: BrokerSpec | None = None,
    ):
        self.panels = panels
        self.instruments = instruments
        self.sp = strategy_params or StrategyParams()
        self.pp = portfolio or PortfolioParams()
        self.broker = broker or BrokerSpec()
        self.converter = EurConverter(panels)
        self.signals = {sym: generate_signals(df, self.sp) for sym, df in panels.items()}

    # ---------------------------------------------------------------- helpers

    def _nights_between(self, start: pd.Timestamp, end: pd.Timestamp) -> int:
        """Financing nights between two consecutive bars, Wednesday tripled."""
        if end <= start:
            return 0
        nights = 0
        for day in pd.date_range(start, end - pd.Timedelta(days=1), freq="D"):
            nights += 3 if day.weekday() == self.broker.triple_swap_weekday else 1
        return nights

    def _to_eur(self, amount_quote: float, symbol: str, date: pd.Timestamp) -> float:
        return self.converter.to_eur(amount_quote, self.instruments[symbol].quote, date)

    def _unrealized_eur(self, pos: Position, price: float, date: pd.Timestamp) -> float:
        move = (price - pos.entry_price) * pos.direction
        return self._to_eur(move * pos.units, pos.symbol, date)

    def _currency_exposure(self, positions: dict[str, Position]) -> dict[str, int]:
        exposure: dict[str, int] = {}
        for pos in positions.values():
            inst = self.instruments[pos.symbol]
            exposure[inst.base] = exposure.get(inst.base, 0) + pos.direction
            exposure[inst.quote] = exposure.get(inst.quote, 0) - pos.direction
        return exposure

    def _scale_out_units(self, pos: Position) -> float:
        """Units to close at the partial target, rounded to the broker's step.

        Returns 0 when the position is too small to split -- the common case on
        a 500 EUR account, where the whole position may be a single minimum
        ticket.
        """
        step = self.broker.lot_step_units
        raw = pos.units * self.sp.partial_fraction
        units = math.floor(raw / step) * step
        if units <= 0:
            return 0.0
        if pos.units - units < self.broker.min_units:
            return 0.0
        return float(units)

    # ------------------------------------------------------------------- run

    def run(self) -> BacktestResult:
        dates = sorted(set().union(*[set(df.index) for df in self.panels.values()]))
        cash = self.pp.starting_equity
        equity = self.pp.starting_equity
        peak_equity = equity
        positions: dict[str, Position] = {}
        pending: list[PendingOrder] = []
        trades: list[Trade] = []
        skips: list[dict] = []
        equity_points: list[tuple[pd.Timestamp, float]] = []

        week_anchor: tuple | None = None
        week_start_equity = equity
        prev_date: pd.Timestamp | None = None

        for date in dates:
            year, week, _ = date.isocalendar()
            if week_anchor != (year, week):
                week_anchor = (year, week)
                week_start_equity = equity

            # --- financing for the nights since the previous bar ------------
            if prev_date is not None and positions:
                nights = self._nights_between(prev_date, date)
                for pos in positions.values():
                    inst = self.instruments[pos.symbol]
                    swap_pips = self.broker.swap_pips(symbol=pos.symbol, direction=pos.direction)
                    charge_quote = nights * swap_pips * inst.pip * pos.units
                    charge = self._to_eur(charge_quote, pos.symbol, date)
                    cash -= charge
                    pos.costs_eur += charge
                    pos.financing_eur += charge

            # --- entries (before exits: today's exit frees a slot tomorrow) --
            weekly_halt = equity <= week_start_equity * (1.0 - self.pp.weekly_loss_limit)
            risk_fraction = self.pp.risk_fraction
            if equity < peak_equity * (1.0 - self.pp.drawdown_throttle):
                risk_fraction *= self.pp.throttle_factor

            for order in pending:
                symbol, direction = order.symbol, order.direction
                df = self.panels[symbol]
                if date not in df.index:
                    continue
                inst = self.instruments[symbol]

                if weekly_halt:
                    skips.append({"date": date, "symbol": symbol, "reason": "weekly loss limit"})
                    continue
                if symbol in positions:
                    continue
                if len(positions) >= self.pp.max_open_positions:
                    skips.append({"date": date, "symbol": symbol, "reason": "position slots full"})
                    continue

                open_risk = sum(p.initial_risk_eur for p in positions.values())
                if open_risk + equity * risk_fraction > equity * self.pp.max_total_risk:
                    skips.append({"date": date, "symbol": symbol, "reason": "portfolio risk cap"})
                    continue

                exposure = self._currency_exposure(positions)
                base_after = exposure.get(inst.base, 0) + direction
                quote_after = exposure.get(inst.quote, 0) - direction
                if (
                    abs(base_after) > self.pp.max_currency_exposure
                    or abs(quote_after) > self.pp.max_currency_exposure
                ):
                    skips.append({"date": date, "symbol": symbol, "reason": "currency concentration"})
                    continue

                entry_price = self.broker.fill_price(float(df.loc[date, "open"]), direction, inst)
                stop_distance = self.sp.atr_stop_mult * order.atr_at_signal
                sizing = size_position(
                    equity_eur=equity,
                    risk_fraction=risk_fraction,
                    stop_distance_price=stop_distance,
                    entry_price=entry_price,
                    instrument=inst,
                    broker=self.broker,
                    converter=self.converter,
                    date=date,
                )
                if not sizing.tradeable:
                    skips.append({"date": date, "symbol": symbol, "reason": sizing.skipped_reason})
                    continue

                if self.pp.max_financing_r is not None:
                    # Nights per bar is ~7/5: five trading days span seven
                    # calendar nights, and Wednesday counts triple.
                    planned_nights = self.sp.time_stop_bars * 1.4 + 2
                    swap_pips = self.broker.swap_pips(symbol, direction)
                    expected_quote = planned_nights * swap_pips * inst.pip * sizing.units
                    expected_eur = self._to_eur(expected_quote, symbol, date)
                    if expected_eur > sizing.risk_eur * self.pp.max_financing_r:
                        skips.append(
                            {
                                "date": date,
                                "symbol": symbol,
                                "reason": f"negative carry ({expected_eur / sizing.risk_eur:.2f}R "
                                f"of swap over a full hold)",
                            }
                        )
                        continue

                commission = self.broker.commission_eur(
                    sizing.units, entry_price, inst, self.converter, date
                )
                cash -= commission
                positions[symbol] = Position(
                    symbol=symbol,
                    direction=direction,
                    entry_date=date,
                    entry_price=entry_price,
                    units=sizing.units,
                    initial_units=sizing.units,
                    initial_stop=entry_price - direction * stop_distance,
                    stop=entry_price - direction * stop_distance,
                    r_price=stop_distance,
                    initial_risk_eur=sizing.risk_eur,
                    extreme=entry_price,
                    costs_eur=commission,
                )

            pending = []

            # --- exits ------------------------------------------------------
            for symbol in list(positions):
                pos = positions[symbol]
                df = self.panels[symbol]
                if date not in df.index:
                    continue
                bar = df.loc[date]
                inst = self.instruments[symbol]
                d = pos.direction
                exit_reason: str | None = None
                exit_mid: float | None = None

                gapped = (
                    bar["open"] <= pos.stop if d == 1 else bar["open"] >= pos.stop
                )
                touched = bar["low"] <= pos.stop if d == 1 else bar["high"] >= pos.stop

                if gapped:
                    exit_mid, exit_reason = float(bar["open"]), "stop (gap)"
                elif touched:
                    exit_mid, exit_reason = float(pos.stop), "stop"

                # Partial profit, if enabled. Only reachable when the stop was
                # not touched in the same bar -- see the pessimism note above.
                if exit_reason is None and self.sp.partial_fraction > 0 and not pos.partial_done:
                    target = pos.partial_target
                    hit = bar["high"] >= target if d == 1 else bar["low"] <= target
                    if hit:
                        part_units = self._scale_out_units(pos)
                        if part_units > 0:
                            fill = self.broker.fill_price(target, -d, inst)
                            move = (fill - pos.entry_price) * d
                            pnl = self._to_eur(move * part_units, symbol, date)
                            commission = self.broker.commission_eur(
                                part_units, fill, inst, self.converter, date
                            )
                            cash += pnl - commission
                            pos.realized_eur += pnl
                            pos.costs_eur += commission
                            pos.units -= part_units
                        else:
                            skips.append(
                                {
                                    "date": date,
                                    "symbol": symbol,
                                    "reason": "position too small to scale out",
                                }
                            )
                        pos.partial_done = True

                # Time stop: a trade that has gone nowhere is occupying one of
                # three slots and paying swap every night to do it.
                if exit_reason is None and pos.bars_held >= self.sp.time_stop_bars:
                    current_r = (float(bar["close"]) - pos.entry_price) * d / pos.r_price
                    if current_r < self.sp.time_stop_min_r:
                        exit_mid, exit_reason = float(bar["close"]), "time stop"

                if exit_reason is not None:
                    fill = self.broker.fill_price(
                        exit_mid, -d, inst, is_stop=exit_reason.startswith("stop")
                    )
                    move = (fill - pos.entry_price) * d
                    pnl = self._to_eur(move * pos.units, symbol, date)
                    commission = self.broker.commission_eur(
                        pos.units, fill, inst, self.converter, date
                    )
                    cash += pnl - commission
                    pos.realized_eur += pnl
                    pos.costs_eur += commission
                    net = pos.realized_eur - pos.costs_eur
                    trades.append(
                        Trade(
                            symbol=symbol,
                            direction=d,
                            entry_date=pos.entry_date,
                            exit_date=date,
                            entry_price=pos.entry_price,
                            exit_price=fill,
                            units=pos.initial_units,
                            bars_held=pos.bars_held,
                            pnl_eur=net,
                            costs_eur=pos.costs_eur,
                            financing_eur=pos.financing_eur,
                            r_multiple=net / pos.initial_risk_eur if pos.initial_risk_eur else 0.0,
                            exit_reason=exit_reason,
                        )
                    )
                    del positions[symbol]
                    continue

                # --- age the position, then move the stop -------------------
                #
                # Both use this bar's completed high/low to set a stop that
                # applies from the *next* bar onward, so nothing is read early.
                pos.bars_held += 1
                pos.extreme = (
                    max(pos.extreme, float(bar["high"]))
                    if d == 1
                    else min(pos.extreme, float(bar["low"]))
                )
                best_r = (pos.extreme - pos.entry_price) * d / pos.r_price

                if self.sp.breakeven_at_r is not None and best_r >= self.sp.breakeven_at_r:
                    pos.stop = max(pos.stop, pos.entry_price) if d == 1 else min(pos.stop, pos.entry_price)

                if best_r >= self.sp.trail_start_r:
                    atr_value = float(self.signals[symbol].loc[date, "atr"])
                    if np.isfinite(atr_value) and atr_value > 0:
                        chandelier = pos.extreme - d * self.sp.atr_trail_mult * atr_value
                        # A trail only ever tightens; it never loosens a stop.
                        if d == 1:
                            pos.stop = max(pos.stop, chandelier)
                        else:
                            pos.stop = min(pos.stop, chandelier)

            # --- mark to market --------------------------------------------
            unrealized = 0.0
            for pos in positions.values():
                df = self.panels[pos.symbol]
                if date in df.index:
                    unrealized += self._unrealized_eur(pos, float(df.loc[date, "close"]), date)
            equity = cash + unrealized
            peak_equity = max(peak_equity, equity)
            equity_points.append((date, equity))

            # --- queue tomorrow's orders from today's signals ---------------
            for symbol, sig_df in self.signals.items():
                if date not in sig_df.index or symbol in positions:
                    continue
                row = sig_df.loc[date]
                atr_value = float(row["atr"]) if np.isfinite(row["atr"]) else 0.0
                if atr_value <= 0:
                    continue
                momentum = float(row["mom"]) if np.isfinite(row["mom"]) else 0.0
                if bool(row["long_signal"]):
                    pending.append(PendingOrder(symbol, 1, atr_value, momentum))
                elif bool(row["short_signal"]):
                    pending.append(PendingOrder(symbol, -1, atr_value, momentum))

            # Strongest trend first when more signals arrive than we have slots.
            pending.sort(key=lambda o: abs(o.momentum), reverse=True)
            prev_date = date

        equity_series = pd.Series(
            [v for _, v in equity_points], index=pd.DatetimeIndex([d for d, _ in equity_points])
        )
        return BacktestResult(
            equity=equity_series,
            trades=trades,
            skips=skips,
            params={
                "strategy": self.sp.as_dict(),
                "portfolio": dict(self.pp.__dict__),
                "broker": dict(self.broker.__dict__),
            },
            missing_fx=self.converter.missing,
            starting_equity=self.pp.starting_equity,
            open_at_end=len(positions),
            unrealized_at_end=unrealized if equity_points else 0.0,
        )
