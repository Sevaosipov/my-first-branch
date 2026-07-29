const BASE_URL = 'https://api.crypto.com/exchange/v1/public';

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Crypto.com API request failed: ${res.status} ${res.statusText}`);
  }
  const body = await res.json();
  if (body.code !== 0) {
    throw new Error(`Crypto.com API error: ${body.message ?? body.code}`);
  }
  return body.result;
}

export async function getTicker(instrumentName) {
  const result = await getJson(`${BASE_URL}/get-tickers?instrument_name=${encodeURIComponent(instrumentName)}`);
  return result?.data?.[0] ?? null;
}

export async function getOrderBook(instrumentName, depth = 10) {
  const result = await getJson(
    `${BASE_URL}/get-book?instrument_name=${encodeURIComponent(instrumentName)}&depth=${depth}`,
  );
  return result?.data?.[0] ?? null;
}

export async function getRecentCandles(instrumentName, timeframe = '1h', count = 5) {
  const result = await getJson(
    `${BASE_URL}/get-candlestick?instrument_name=${encodeURIComponent(instrumentName)}&timeframe=${timeframe}&count=${count}`,
  );
  return result?.data ?? [];
}

// Fetches ticker, order book, and recent candles in parallel. Each piece
// fails independently so one bad/unlisted instrument doesn't drop the others.
export async function enrichWithMarketData(instrumentName) {
  const [ticker, orderBook, candles] = await Promise.all([
    getTicker(instrumentName).catch((err) => ({ error: err.message })),
    getOrderBook(instrumentName).catch((err) => ({ error: err.message })),
    getRecentCandles(instrumentName).catch((err) => ({ error: err.message })),
  ]);
  return { instrumentName, ticker, orderBook, candles };
}
