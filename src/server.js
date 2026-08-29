import express from 'express';
import { parseAlert, InvalidAlertError } from './tradingview.js';
import { normalizeSymbol } from './symbolMap.js';
import { enrichWithMarketData } from './marketData.js';
import { logAlert, readAlerts } from './logger.js';
import { sizeAlert, sizingConfigFromEnv } from './positionSizing.js';

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 3000;
const WEBHOOK_SECRET = process.env.TRADINGVIEW_WEBHOOK_SECRET || '';
const SIZING_CONFIG = sizingConfigFromEnv();

app.post('/webhook/tradingview', async (req, res) => {
  let alert;
  try {
    alert = parseAlert(req.body, WEBHOOK_SECRET);
  } catch (err) {
    if (err instanceof InvalidAlertError) {
      return res.status(400).json({ error: err.message });
    }
    throw err;
  }

  const instrumentName = normalizeSymbol(alert.symbol);
  let marketData = null;
  if (instrumentName) {
    try {
      marketData = await enrichWithMarketData(instrumentName);
    } catch (err) {
      marketData = { error: err.message };
    }
  }

  // FX alerts that carry a stop get a position size for the configured
  // account attached; everything else passes through untouched.
  const sizing = sizeAlert(alert, SIZING_CONFIG);
  const entry = { ...alert, instrumentName, marketData, sizing };
  await logAlert(entry);

  console.log(`[tradingview] logged alert for ${alert.symbol}${instrumentName ? ` (${instrumentName})` : ''}`);
  res.status(200).json({ status: 'logged', instrumentName, sizing });
});

app.get('/alerts', async (req, res) => {
  const limit = Number(req.query.limit) || 50;
  const alerts = await readAlerts(undefined, limit);
  res.json({ alerts });
});

app.get('/health', (req, res) => {
  res.json({ status: 'ok' });
});

app.listen(PORT, () => {
  console.log(`TradingView alert reactor listening on port ${PORT}`);
  if (!WEBHOOK_SECRET) {
    console.warn(
      'WARNING: TRADINGVIEW_WEBHOOK_SECRET is not set — the webhook endpoint will accept unauthenticated requests.',
    );
  }
});
