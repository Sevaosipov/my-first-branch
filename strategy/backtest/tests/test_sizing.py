"""Position sizing: lot granularity, margin, and the 500 EUR minimum-ticket wall."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swingfx import MAJORS, MICRO_LOT_BROKER, BrokerSpec  # noqa: E402
from swingfx.sizing import min_equity_for_risk, size_position  # noqa: E402

DATE = pd.Timestamp("2024-01-02")
EURUSD = MAJORS["EURUSD"]


class FixedConverter:
    """1 EUR = 1.10 USD, 1 EUR = 160 JPY."""

    missing: set = set()
    RATES = {"EUR": 1.0, "USD": 1.10, "JPY": 160.0, "CAD": 1.45, "GBP": 0.86,
             "CHF": 0.95, "AUD": 1.65, "NZD": 1.80}

    def units_per_eur(self, currency, date):
        return self.RATES[currency]

    def to_eur(self, amount, currency, date):
        return amount / self.RATES[currency]


CONV = FixedConverter()


def size(**kwargs):
    defaults = dict(
        equity_eur=500.0,
        risk_fraction=0.01,
        stop_distance_price=0.0140,  # 140 pips
        entry_price=1.10,
        instrument=EURUSD,
        broker=BrokerSpec(),
        converter=CONV,
        date=DATE,
    )
    return size_position(**{**defaults, **kwargs})


def test_risked_amount_matches_the_budget():
    result = size()
    # 5 EUR budget / (0.0140 USD per unit / 1.10) = ~392 units, floored to 300.
    assert result.units == 300
    assert result.risk_eur == pytest.approx(300 * 0.0140 / 1.10)
    assert result.risk_eur < 5.0


def test_units_are_rounded_down_to_the_lot_step():
    result = size(equity_eur=5000.0, broker=BrokerSpec(lot_step_units=1000, min_units=1000))
    assert result.units % 1000 == 0
    assert result.risk_eur <= 50.0


def test_minimum_ticket_is_raised_to_a_whole_lot_step():
    """A broker dealing in 1,000s cannot fill 100 units, whatever its stated
    minimum."""
    broker = BrokerSpec(lot_step_units=1000, min_units=100)
    result = size(equity_eur=1500.0, broker=broker)
    assert result.tradeable
    assert result.units % 1000 == 0


def test_micro_lot_broker_forces_oversized_risk_and_is_refused():
    """The headline constraint for a 500 EUR account.

    A 0.01-lot minimum on a 140-pip stop risks ~12.7 EUR -- 2.5% of a 500 EUR
    account when 1% was intended -- so the trade is skipped, not silently
    oversized.
    """
    result = size(broker=MICRO_LOT_BROKER)
    assert not result.tradeable
    assert "minimum ticket" in result.skipped_reason


def test_micro_lot_broker_is_fine_on_a_larger_account():
    result = size(equity_eur=2000.0, broker=MICRO_LOT_BROKER)
    assert result.tradeable
    assert result.risk_eur <= 2000.0 * 0.01


def test_small_overshoot_is_tolerated_rather_than_skipped():
    """A minimum ticket slightly above budget is taken, not refused."""
    result = size(equity_eur=1150.0, broker=MICRO_LOT_BROKER)
    assert result.tradeable
    assert result.risk_eur <= 1150.0 * 0.01 * 1.25


def test_margin_cap_limits_a_very_tight_stop():
    """With a 1-pip stop the risk budget would buy an absurd position."""
    result = size(stop_distance_price=0.0001)
    max_notional_eur = 500.0 * 30.0 * 0.35
    assert result.units <= max_notional_eur * CONV.RATES["EUR"] + 1e-9
    assert result.units > 0


def test_jpy_pair_conversion():
    result = size(instrument=MAJORS["USDJPY"], stop_distance_price=1.60, entry_price=150.0)
    # 1.60 JPY per unit / 160 JPY per EUR = 0.01 EUR per unit; 5 EUR budget = 500 units.
    assert result.units == 500
    assert result.risk_eur == pytest.approx(5.0)


def test_zero_and_negative_inputs_are_refused():
    assert not size(stop_distance_price=0.0).tradeable
    assert not size(equity_eur=0.0).tradeable


def test_min_equity_for_risk_inverts_the_constraint():
    needed = min_equity_for_risk(
        risk_fraction=0.01,
        stop_distance_price=0.0140,
        instrument=EURUSD,
        broker=MICRO_LOT_BROKER,
        quote_per_eur=1.10,
    )
    assert needed == pytest.approx(1000 * 0.0140 / 1.10 / 0.01)
    # Sizing at exactly this equity must produce a tradeable position.
    assert size(equity_eur=needed, broker=MICRO_LOT_BROKER).tradeable
