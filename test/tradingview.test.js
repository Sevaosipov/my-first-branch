import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseAlert, InvalidAlertError } from '../src/tradingview.js';

test('parses a valid alert', () => {
  const alert = parseAlert({ secret: 's3cr3t', symbol: 'BTCUSDT', action: 'buy', price: '65000.5' }, 's3cr3t');
  assert.equal(alert.symbol, 'BTCUSDT');
  assert.equal(alert.action, 'buy');
  assert.equal(alert.price, 65000.5);
  assert.ok(alert.receivedAt);
});

test('rejects a wrong secret', () => {
  assert.throws(() => parseAlert({ secret: 'wrong', symbol: 'BTCUSDT' }, 's3cr3t'), InvalidAlertError);
});

test('rejects a missing secret when one is required', () => {
  assert.throws(() => parseAlert({ symbol: 'BTCUSDT' }, 's3cr3t'), InvalidAlertError);
});

test('allows any request when no secret is configured', () => {
  const alert = parseAlert({ symbol: 'BTCUSDT' }, '');
  assert.equal(alert.symbol, 'BTCUSDT');
});

test('rejects an alert without a symbol', () => {
  assert.throws(() => parseAlert({ secret: 's3cr3t' }, 's3cr3t'), InvalidAlertError);
});

test('rejects a non-object body', () => {
  assert.throws(() => parseAlert(null, ''), InvalidAlertError);
  assert.throws(() => parseAlert('not json', ''), InvalidAlertError);
});
