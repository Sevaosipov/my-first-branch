const QUOTE_CURRENCIES = ['USDT', 'USDC', 'USD', 'BTC', 'ETH', 'EUR', 'GBP', 'DAI'];

// Normalizes a TradingView ticker (e.g. "BINANCE:BTCUSDT", "BTC/USDT", "btcusdt")
// into a Crypto.com Exchange instrument name (e.g. "BTC_USDT"). Returns null
// when the symbol can't be confidently mapped, so callers can skip enrichment
// instead of guessing.
export function normalizeSymbol(rawSymbol) {
  if (!rawSymbol || typeof rawSymbol !== 'string') return null;

  let symbol = rawSymbol.trim().toUpperCase();
  const colonIndex = symbol.indexOf(':');
  if (colonIndex !== -1) symbol = symbol.slice(colonIndex + 1);
  symbol = symbol.replace(/\.P$|PERP$/, '');

  if (symbol.includes('_')) {
    const [base, quote] = symbol.split('_');
    return base && quote ? `${base}_${quote}` : null;
  }

  if (symbol.includes('/')) {
    const [base, quote] = symbol.split('/');
    return base && quote ? `${base}_${quote}` : null;
  }

  for (const quote of QUOTE_CURRENCIES) {
    if (symbol.endsWith(quote) && symbol.length > quote.length) {
      const base = symbol.slice(0, symbol.length - quote.length);
      return `${base}_${quote}`;
    }
  }

  return null;
}
