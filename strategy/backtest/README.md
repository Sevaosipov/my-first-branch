# TPC backtester

A daily-bar FX backtester built for one strategy, with the modelling choices
that usually get skipped: broker lot granularity, overnight financing, EUR
conversion of quote-currency P&L, and pessimistic resolution of ambiguous bars.

```bash
pip install -r requirements.txt

python -m pytest tests/ -q                       # 35 correctness tests
python validate.py                               # null + power tests
python run_backtest.py --synthetic trend         # runs without any data
```

## Getting price data

None is bundled. The backtester reads a directory of `<SYMBOL>.csv` files with
`Date,Open,High,Low,Close` columns (any capitalisation, extra columns ignored):

```
data/
  EURUSD.csv
  GBPUSD.csv
  USDJPY.csv
  AUDUSD.csv
  USDCAD.csv
```

Free daily OHLC sources that produce this shape directly:

```bash
# Stooq -- daily history back to the 1990s, no key needed
for p in eurusd gbpusd usdjpy audusd usdcad; do
  curl -s "https://stooq.com/q/d/l/?s=$p&i=d" -o "data/$(echo $p | tr a-z A-Z).csv"
done
```

```python
# Yahoo Finance via yfinance
import yfinance as yf
for pair in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]:
    df = yf.download(f"{pair}=X", start="2004-01-01", auto_adjust=False)
    df.reset_index()[["Date", "Open", "High", "Low", "Close"]].to_csv(f"data/{pair}.csv", index=False)
```

Dukascopy and your own broker's MT4/MT5 history export both work too; the
loader only cares about the columns. Include **EURUSD** whatever else you
trade — it is what converts USD-denominated P&L into EUR. Without it the
backtest falls back to fixed approximate rates and says so in the report.

## Running a real backtest

```bash
# in sample: develop here
python run_backtest.py --data ./data --start 2005-01-01 --end 2016-12-31

# out of sample: look once
python run_backtest.py --data ./data --start 2017-01-01 --trades-csv oos_trades.csv
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--broker micro` | simulate a 0.01-lot broker (watch the skip count explode on 500 EUR) |
| `--risk 0.005` | risk per trade as a fraction of equity |
| `--equity 500` | starting capital |
| `--swap-json swaps.json` | your broker's real financing rates — see below |
| `--max-financing-r 0.25` | refuse signals that would bleed swap for weeks |
| `--long-only` | disable shorts |
| `--json out.json` / `--trades-csv t.csv` | machine-readable output |

### Your broker's swap rates

Financing is ~94% of this strategy's cost, so this is the highest-value input
you can supply. Take the numbers from your broker's contract specification and
write them as **pips per night**, negative meaning a credit you receive:

```json
{
  "EURUSD": [0.8, -0.2],
  "GBPUSD": [0.9, -0.3],
  "USDJPY": [-0.4, 1.6],
  "AUDUSD": [1.1, -0.5],
  "USDCAD": [0.6, -0.1]
}
```

`[long, short]`. Then `--swap-json swaps.json --max-financing-r 0.25`.

## Layout

| Module | Responsibility |
| --- | --- |
| `swingfx/indicators.py` | EMA/RMA/RSI/ATR, seeded to match TradingView's Pine |
| `swingfx/strategy.py` | entry and exit rules, all parameters in one dataclass |
| `swingfx/engine.py` | the event loop: fills, stops, trailing, portfolio caps |
| `swingfx/sizing.py` | lot-step and margin aware sizing; refuses untakeable trades |
| `swingfx/broker.py` | spread, slippage, commission, per-side swap |
| `swingfx/instruments.py` | pair specs and quote-currency to EUR conversion |
| `swingfx/metrics.py` | performance stats, bootstrap, risk dial |
| `swingfx/synthetic.py` | random-walk and regime-trending price generators |

## What the engine assumes

Documented in full at the top of `engine.py`. The choices that matter:

- Signals on bar *t*'s close, fills at bar *t+1*'s open, stop distance from bar
  *t*'s ATR.
- Entries are processed before exits, so a slot freed today is reusable
  tomorrow, not today.
- A bar containing both stop and target is booked as a stop-out. Daily bars
  cannot say which came first, and guessing favourably is how backtests lie.
- Gaps through the stop fill at the open, not the stop level.
- Swap is charged every night, tripled on Wednesdays, in both directions unless
  a swap table says otherwise.

If you change any of these, re-run `validate.py` before believing a result.
