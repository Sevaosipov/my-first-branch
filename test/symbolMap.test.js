import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeSymbol } from '../src/symbolMap.js';

test('normalizes a plain symbol with a common quote currency', () => {
  assert.equal(normalizeSymbol('BTCUSDT'), 'BTC_USDT');
});

test('strips the exchange prefix', () => {
  assert.equal(normalizeSymbol('BINANCE:ETHUSDT'), 'ETH_USDT');
});

test('strips perpetual futures suffixes', () => {
  assert.equal(normalizeSymbol('BTCUSDT.P'), 'BTC_USDT');
  assert.equal(normalizeSymbol('BTCUSDTPERP'), 'BTC_USDT');
});

test('handles symbols that are already separated', () => {
  assert.equal(normalizeSymbol('btc_usdt'), 'BTC_USDT');
  assert.equal(normalizeSymbol('BTC/USDT'), 'BTC_USDT');
});

test('returns null for an unrecognized symbol', () => {
  assert.equal(normalizeSymbol('NOTAREALSYMBOL123'), null);
});

test('returns null for empty or missing input', () => {
  assert.equal(normalizeSymbol(''), null);
  assert.equal(normalizeSymbol(undefined), null);
});
