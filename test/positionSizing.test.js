import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  computePositionSize,
  parseFxSymbol,
  sizeAlert,
  sizingConfigFromEnv,
} from '../src/positionSizing.js';

// EURUSD long with a 140-pip stop -- the worked example from strategy/README.md.
const EURUSD_TRADE = { symbol: 'EURUSD', entryPrice: 1.1, stopPrice: 1.086 };

test('parses TradingView FX tickers', () => {
  assert.deepEqual(parseFxSymbol('OANDA:EURUSD'), {
    symbol: 'EURUSD',
    base: 'EUR',
    quote: 'USD',
    pip: 0.0001,
  });
  assert.equal(parseFxSymbol('eur/usd').symbol, 'EURUSD');
  assert.equal(parseFxSymbol('USDJPY').pip, 0.01);
});

test('rejects symbols that are not known FX pairs', () => {
  assert.equal(parseFxSymbol('BINANCE:BTCUSDT'), null);
  assert.equal(parseFxSymbol(''), null);
  assert.equal(parseFxSymbol(null), null);
});

test('sizes a 500 EUR account within its risk budget', () => {
  const result = computePositionSize(EURUSD_TRADE);
  assert.equal(result.skippedReason, null);
  assert.equal(result.units % 100, 0);
  assert.ok(result.riskEur <= 5, `risked ${result.riskEur} EUR against a 5 EUR budget`);
  assert.ok(result.units >= 300 && result.units <= 500, `unexpected size ${result.units}`);
  assert.equal(result.stopDistancePips, 140);
});

test('refuses the trade when the broker minimum blows the risk budget', () => {
  // A 0.01-lot broker: smallest ticket is 1,000 units, risking ~12.8 EUR.
  const result = computePositionSize(EURUSD_TRADE, { lotStepUnits: 1000, minUnits: 1000 });
  assert.equal(result.units, 0);
  assert.match(result.skippedReason, /minimum ticket/);
  assert.match(result.skippedReason, /% of the account/);
});

test('the same broker is fine once the account is large enough', () => {
  const result = computePositionSize(EURUSD_TRADE, {
    lotStepUnits: 1000,
    minUnits: 1000,
    equityEur: 2000,
  });
  assert.equal(result.skippedReason, null);
  assert.ok(result.riskEur <= 20);
});

test('raises the minimum ticket to a whole lot step', () => {
  const result = computePositionSize(EURUSD_TRADE, {
    lotStepUnits: 1000,
    minUnits: 100,
    equityEur: 1500,
  });
  assert.equal(result.units % 1000, 0);
});

test('converts JPY-quoted pairs', () => {
  // 1.60 JPY stop / 158 JPY per EUR = ~0.0101 EUR per unit; 5 EUR budget.
  const result = computePositionSize({ symbol: 'USDJPY', entryPrice: 150, stopPrice: 148.4 });
  assert.equal(result.skippedReason, null);
  assert.ok(result.riskEur <= 5);
  assert.equal(result.stopDistancePips, 160);
});

test('caps size by available margin on a very tight stop', () => {
  const result = computePositionSize({ symbol: 'EURUSD', entryPrice: 1.1, stopPrice: 1.0999 });
  const maxUnits = 500 * 30 * 0.35 * 1; // equity * leverage * cap * EUR per EUR
  assert.ok(result.units > 0);
  assert.ok(result.units <= maxUnits, `${result.units} exceeds the margin cap`);
});

test('rejects malformed input rather than guessing', () => {
  assert.match(computePositionSize({ symbol: 'EURUSD', entryPrice: 1.1, stopPrice: 1.1 }).skippedReason, /zero/);
  assert.match(computePositionSize({ symbol: 'EURUSD', entryPrice: 1.1 }).skippedReason, /numbers/);
  assert.match(computePositionSize({ symbol: 'BTCUSDT', entryPrice: 1, stopPrice: 2 }).skippedReason, /known FX pair/);
  assert.match(
    computePositionSize(EURUSD_TRADE, { equityEur: 0 }).skippedReason,
    /equity must be positive/,
  );
});

test('sizeAlert reads direction from the alert action', () => {
  const long = sizeAlert({ symbol: 'EURUSD', action: 'buy', price: 1.1, stop: 1.086 });
  const short = sizeAlert({ symbol: 'EURUSD', action: 'sell', price: 1.1, stop: 1.114 });
  assert.equal(long.direction, 1);
  assert.equal(short.direction, -1);
  assert.ok(long.units > 0 && short.units > 0);
});

test('sizeAlert returns null for alerts it cannot size', () => {
  assert.equal(sizeAlert({ symbol: 'BINANCE:BTCUSDT', price: 65000, stop: 64000 }), null);
  assert.equal(sizeAlert({ symbol: 'EURUSD', price: 1.1 }), null, 'no stop');
  assert.equal(sizeAlert(null), null);
});

test('reads configuration from the environment', () => {
  const config = sizingConfigFromEnv({
    ACCOUNT_EQUITY_EUR: '1200',
    RISK_PER_TRADE: '0.005',
    LOT_STEP_UNITS: '1000',
    QUOTE_RATES_JSON: '{"USD":1.2}',
  });
  assert.equal(config.equityEur, 1200);
  assert.equal(config.riskFraction, 0.005);
  assert.equal(config.lotStepUnits, 1000);
  assert.equal(config.quoteRates.USD, 1.2);
});

test('ignores unset and malformed environment values', () => {
  assert.deepEqual(sizingConfigFromEnv({}), {});
  const config = sizingConfigFromEnv({ ACCOUNT_EQUITY_EUR: 'abc', QUOTE_RATES_JSON: '{oops' });
  assert.equal(config.equityEur, undefined);
  assert.equal(config.quoteRates, undefined);
});
