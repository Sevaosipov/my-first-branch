// Turns a TPC alert into a position size for a small account.
//
// The strategy is documented in strategy/README.md. The one number that
// decides whether a 500 EUR account can trade it at all is the broker's lot
// step: risking 1% of 500 EUR on a typical 140-pip stop calls for roughly 400
// units of EURUSD, which a 0.01-lot (1,000-unit) broker cannot sell you. When
// the smallest legal ticket would blow the risk budget, this returns a skip
// rather than a size, because taking that trade is a 2.5% risk pretending to
// be a 1% one.
//
// Mirrors strategy/backtest/swingfx/sizing.py; keep the two in step.

const PIP_SIZES = {
  EURUSD: 0.0001,
  GBPUSD: 0.0001,
  AUDUSD: 0.0001,
  NZDUSD: 0.0001,
  USDCAD: 0.0001,
  USDCHF: 0.0001,
  USDJPY: 0.01,
  EURJPY: 0.01,
  GBPJPY: 0.01,
  EURGBP: 0.0001,
};

export const DEFAULT_SIZING_CONFIG = {
  equityEur: 500,
  riskFraction: 0.01,
  lotStepUnits: 100,
  minUnits: 100,
  maxLeverage: 30,
  marginUtilisationCap: 0.35,
  // A minimum ticket up to this multiple of the budget is accepted; beyond it
  // the trade is skipped.
  maxRiskOvershoot: 1.25,
  // Units of each currency per EUR. These move slowly enough that refreshing
  // them monthly changes a position by less than lot rounding does.
  quoteRates: { EUR: 1, USD: 1.09, JPY: 158, GBP: 0.85, CHF: 0.94, CAD: 1.48, AUD: 1.63, NZD: 1.79 },
};

// Splits a TradingView ticker ("OANDA:EURUSD", "EUR/USD", "eurusd") into its
// two currencies. Returns null for anything that is not a known FX pair.
export function parseFxSymbol(rawSymbol) {
  if (!rawSymbol || typeof rawSymbol !== 'string') return null;

  let symbol = rawSymbol.trim().toUpperCase();
  const colonIndex = symbol.indexOf(':');
  if (colonIndex !== -1) symbol = symbol.slice(colonIndex + 1);
  symbol = symbol.replace(/[/_-]/g, '');

  if (!Object.prototype.hasOwnProperty.call(PIP_SIZES, symbol)) return null;
  return { symbol, base: symbol.slice(0, 3), quote: symbol.slice(3, 6), pip: PIP_SIZES[symbol] };
}

/**
 * Units to trade, constrained by the risk budget, the broker's lot step, and
 * margin. Returns { units: 0, skippedReason } when the trade is not takeable.
 *
 * @param {object} params
 * @param {string} params.symbol        e.g. "EURUSD" or "OANDA:EURUSD"
 * @param {number} params.entryPrice    current price
 * @param {number} params.stopPrice     protective stop price
 * @param {object} [config]             overrides for DEFAULT_SIZING_CONFIG
 */
export function computePositionSize({ symbol, entryPrice, stopPrice }, config = {}) {
  const cfg = { ...DEFAULT_SIZING_CONFIG, ...config };
  cfg.quoteRates = { ...DEFAULT_SIZING_CONFIG.quoteRates, ...(config.quoteRates || {}) };

  const instrument = parseFxSymbol(symbol);
  if (!instrument) return { units: 0, skippedReason: `not a known FX pair: ${symbol}` };

  if (!Number.isFinite(entryPrice) || !Number.isFinite(stopPrice)) {
    return { units: 0, skippedReason: 'entryPrice and stopPrice must both be numbers' };
  }

  const stopDistance = Math.abs(entryPrice - stopPrice);
  if (stopDistance <= 0) return { units: 0, skippedReason: 'stop distance is zero' };
  if (!(cfg.equityEur > 0)) return { units: 0, skippedReason: 'equity must be positive' };

  const quotePerEur = cfg.quoteRates[instrument.quote];
  const basePerEur = cfg.quoteRates[instrument.base];
  if (!quotePerEur || !basePerEur) {
    return { units: 0, skippedReason: `no EUR rate for ${instrument.quote}/${instrument.base}` };
  }

  const budgetEur = cfg.equityEur * cfg.riskFraction;
  const lossPerUnitEur = stopDistance / quotePerEur;
  const rawUnits = budgetEur / lossPerUnitEur;

  const maxNotionalEur = cfg.equityEur * cfg.maxLeverage * cfg.marginUtilisationCap;
  const maxUnitsByMargin = maxNotionalEur * basePerEur;

  const step = cfg.lotStepUnits;
  let units = Math.floor(Math.min(rawUnits, maxUnitsByMargin) / step) * step;

  if (units < cfg.minUnits) {
    // You cannot trade 100 units at a broker that deals in 1,000s.
    const smallest = Math.ceil(cfg.minUnits / step) * step;
    const smallestRisk = smallest * lossPerUnitEur;
    if (smallestRisk <= budgetEur * cfg.maxRiskOvershoot && smallest <= maxUnitsByMargin) {
      units = smallest;
    } else {
      return {
        units: 0,
        skippedReason:
          `minimum ticket (${smallest.toLocaleString('en-US')} units) risks ` +
          `${smallestRisk.toFixed(2)} EUR against a ${budgetEur.toFixed(2)} EUR budget ` +
          `(${((smallestRisk / cfg.equityEur) * 100).toFixed(1)}% of the account)`,
      };
    }
  }

  const riskEur = units * lossPerUnitEur;
  return {
    symbol: instrument.symbol,
    units,
    lots: units / 100000,
    riskEur: Number(riskEur.toFixed(2)),
    riskPctOfEquity: Number(((riskEur / cfg.equityEur) * 100).toFixed(2)),
    stopDistancePips: Number((stopDistance / instrument.pip).toFixed(1)),
    notionalEur: Number((units / basePerEur).toFixed(2)),
    skippedReason: null,
  };
}

/**
 * Sizes an incoming alert when it carries enough information to be sized.
 * Returns null for alerts this does not apply to, so callers can just attach
 * whatever comes back.
 */
export function sizeAlert(alert, config = {}) {
  if (!alert || !parseFxSymbol(alert.symbol)) return null;
  if (!Number.isFinite(alert.price) || !Number.isFinite(alert.stop)) return null;

  const direction = typeof alert.action === 'string' && alert.action.toLowerCase() === 'sell' ? -1 : 1;
  const sized = computePositionSize(
    { symbol: alert.symbol, entryPrice: alert.price, stopPrice: alert.stop },
    config,
  );
  return { ...sized, direction };
}

// Reads sizing configuration from the environment, falling back to the
// defaults above.
export function sizingConfigFromEnv(env = process.env) {
  const config = {};
  const numeric = {
    equityEur: 'ACCOUNT_EQUITY_EUR',
    riskFraction: 'RISK_PER_TRADE',
    lotStepUnits: 'LOT_STEP_UNITS',
    minUnits: 'MIN_UNITS',
    maxLeverage: 'MAX_LEVERAGE',
  };
  for (const [key, name] of Object.entries(numeric)) {
    const value = Number(env[name]);
    if (env[name] !== undefined && Number.isFinite(value)) config[key] = value;
  }
  if (env.QUOTE_RATES_JSON) {
    try {
      config.quoteRates = JSON.parse(env.QUOTE_RATES_JSON);
    } catch {
      console.warn('QUOTE_RATES_JSON is not valid JSON; using default rates');
    }
  }
  return config;
}
