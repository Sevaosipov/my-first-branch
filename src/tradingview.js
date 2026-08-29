export class InvalidAlertError extends Error {}

// Parses and validates the JSON body TradingView posts to the webhook.
// `expectedSecret` is compared against a `secret` field in the body since
// TradingView alert webhooks cannot send custom headers.
export function parseAlert(body, expectedSecret) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    throw new InvalidAlertError('Alert body must be a JSON object');
  }

  const { secret, symbol, action, price, stop, message, time } = body;

  if (expectedSecret && secret !== expectedSecret) {
    throw new InvalidAlertError('Invalid or missing webhook secret');
  }

  if (!symbol || typeof symbol !== 'string') {
    throw new InvalidAlertError('Alert must include a "symbol" field');
  }

  return {
    symbol,
    action: typeof action === 'string' ? action : null,
    price: price !== undefined && price !== null && price !== '' ? Number(price) : null,
    // Protective stop, when the alert carries one. Strategies that send it can
    // have their position size computed on arrival (see positionSizing.js).
    stop: stop !== undefined && stop !== null && stop !== '' ? Number(stop) : null,
    message: typeof message === 'string' ? message : null,
    time: typeof time === 'string' ? time : null,
    receivedAt: new Date().toISOString(),
  };
}
