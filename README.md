# TradingView Alert Reactor

A small webhook service that reacts to TradingView alerts automatically:
it receives the alert, enriches it with live market data from the
Crypto.com Exchange public API, and logs the combined result.

## How it works

1. You create an alert in TradingView with a webhook URL pointing at this
   service (`POST /webhook/tradingview`).
2. When the alert fires, TradingView POSTs a JSON payload to that URL.
3. The service validates a shared secret embedded in the payload (TradingView
   webhooks can't send custom headers), normalizes the alert's symbol into a
   Crypto.com instrument name (e.g. `BINANCE:BTCUSDT` -> `BTC_USDT`), and
   fetches the current ticker, order book snapshot, and recent candles for it.
4. The enriched alert is appended to a local log file and viewable via
   `GET /alerts`.

If the symbol can't be mapped to a Crypto.com instrument, or the market-data
fetch fails, the alert is still logged — enrichment degrades gracefully
instead of dropping the alert.

## Setup

```bash
npm install
cp .env.example .env
# edit .env and set TRADINGVIEW_WEBHOOK_SECRET to a long random value
npm start
```

The server listens on `PORT` (default `3000`).

For TradingView to reach it, the service needs a public HTTPS URL — deploy it
(Render, Fly.io, a VPS, etc.) or expose your local server with a tunnel
(e.g. `ngrok http 3000`) during testing.

## Configuring the TradingView alert

1. Open the alert's **Notifications** tab and enable **Webhook URL**.
2. Set the URL to `https://<your-host>/webhook/tradingview`.
3. Set the alert **Message** to a JSON body containing the same secret you
   configured in `.env`, for example:

   ```json
   {
     "secret": "change-me",
     "symbol": "{{ticker}}",
     "action": "buy",
     "price": "{{close}}",
     "message": "{{strategy.order.comment}}",
     "time": "{{time}}"
   }
   ```

   Only `secret` and `symbol` are required; `action`, `price`, `message`,
   and `time` are stored as-is if present. Adjust the placeholders to match
   whatever indicator/strategy variables are available on your alert.

## API

- `POST /webhook/tradingview` — receives an alert, returns
  `{ "status": "logged", "instrumentName": "BTC_USDT" }` on success, or
  `400` with an error message if the payload is invalid or the secret is
  wrong.
- `GET /alerts?limit=50` — returns the most recently logged alerts (newest
  first).
- `GET /health` — liveness check.

## Development

```bash
npm test
```

Runs the unit tests (symbol normalization and alert-payload validation)
with Node's built-in test runner.

## Notes

- Alerts are appended to `data/alerts.log` as newline-delimited JSON. This
  directory is gitignored; for production use, swap in a real datastore if
  you need durability or querying beyond "last N alerts".
- The webhook secret is the only authentication TradingView supports for
  webhooks. For extra protection, also restrict inbound traffic to
  [TradingView's published webhook IP ranges](https://www.tradingview.com/support/solutions/43000529348-about-webhooks/)
  at your load balancer/firewall if your hosting provider allows it.
