# Results: what was actually tested

This file exists so the claims in [README.md](README.md) can be checked against
what was measured. The short version:

> **No historical FX backtest was run.** The environment this was built in
> blocks outbound access to every market-data provider, so there is no price
> history here to test against. What *was* validated is the backtester itself —
> that it does not cheat, and that its cost model is right — plus several
> structural findings about the strategy's design. None of that is evidence
> that the strategy is profitable on real prices. Running that test is step 1
> of "Before you risk actual money" in the README, and it is yours to do.

## What could not be tested, and why

Network egress in this environment is restricted to package registries.
`stooq.com`, Yahoo Finance, Frankfurter, ECB, AlphaVantage, TwelveData, Tiingo,
Polygon and Dukascopy were each attempted and refused at the proxy. There is no
bundled price history in this repository and none was fabricated.

So every number below comes from **generated prices**, and the generator's
assumptions are mine, not the market's. Synthetic data can prove a backtester
wrong. It cannot prove a strategy right.

## The null test: does the backtester cheat?

The most valuable test available without real data. Run the strategy on
driftless random walks — no trend exists, so there is nothing to find. Six
seeds × 2,000 bars × 5 pairs.

| Configuration | Trades | Expectancy | t |
| --- | --- | --- | --- |
| Random walk, **costs removed** | 714 | **+0.006R** | +0.11 |
| Random walk, costs modelled | 715 | −0.087R | −1.70 |

With costs switched off, expectancy on random data is statistically
indistinguishable from zero — which is the only correct answer, and strong
evidence there is no lookahead in the signal, sizing, or fill logic. Switch
costs on and the strategy loses almost exactly what it pays. A backtester that
made money here would be broken regardless of how good its equity curve looked
elsewhere.

Reproduce with `python validate.py`.

## The power test: does the machinery work?

Same engine on paths whose drift switches between up/flat/down regimes, so
momentum genuinely exists.

| Configuration | Trades | Expectancy | t |
| --- | --- | --- | --- |
| Trending paths, costs modelled | 706 | +0.001R | +0.02 |

Gross edge attributable to trend: **+0.088R per trade** (trending minus random).
Modelled cost: **0.095R per trade**. So on this generator, costs consume the
entire trend edge and the net result is a coin flip.

Read that carefully, in both directions. It does **not** mean the strategy
cannot work on real FX: these synthetic trends are weak — regimes flip at
random every ~60 days and a third are flat — and real currency trends have been
longer and stronger than that. But it does establish the bar precisely: **the
gross edge has to clear ~0.09R per trade before a single euro reaches the
account.** That is the number to hold real backtest results against.

## Cost decomposition: the most useful thing measured here

Averaged over six seeds, ~700 trades, 15-day mean holding period:

| Component | Per trade | Share |
| --- | --- | --- |
| Overnight financing (swap) | 0.0897R | **94.5%** |
| Spread + slippage + commission | 0.0053R | 5.5% |

This is a consequence of the holding period, and it inverts the usual retail
advice. At 15 nights per trade, financing dwarfs everything transactional. The
strategy's cheapest available improvement is not a better indicator — it is a
broker with a smaller swap markup, or trading the side that *earns* carry.

`BrokerSpec.swap_table` and `--max-financing-r` exist because of this
measurement.

## Exit structure: the first design was wrong

The original exit rule took half the position off at +1R and moved the stop to
breakeven. It looked prudent. Compared on trending paths:

| Exit design | Avg winner | Best trade | Expectancy |
| --- | --- | --- | --- |
| Scale out at 1R + breakeven | 0.86R | 2.9R | −0.026R |
| Trail only, no partial | 1.25R | **9.5R** | +0.036R |

Capping winners at 1R removed the right tail, which is the only place a trend
system makes money. The shipped rules have no partial exit. This is the one
design change in this project the evidence clearly supports.

It also happens to be moot on a 500 EUR account, where a position is often a
single minimum ticket that cannot be split at all — the engine reports these as
`position too small to scale out`.

## Entry rules: one clear effect, and a lot of noise

Eight entry variants, six seeds each, exit rules held fixed:

| Variant | Trades | Gross edge/trade | t (net, trending) |
| --- | --- | --- | --- |
| Shipped: RSI dip + 2-bar trigger | 706 | 0.088R | +0.02 |
| No RSI dip requirement | 1557 | 0.038R | −0.13 |
| Deeper dip (RSI < 35) | 198 | 0.056R | −0.36 |
| 5-bar breakout trigger | 474 | 0.047R | +0.40 |
| 10-bar breakout trigger | 198 | 0.148R | +1.04 |
| No momentum filter | 1012 | 0.073R | −0.31 |
| Dip window 10 bars | 818 | 0.048R | −0.39 |

And stop width:

| Initial stop | Gross edge/trade | t (net, trending) |
| --- | --- | --- |
| 1.5 × ATR | 0.066R | −0.41 |
| 2.0 × ATR (shipped) | 0.088R | +0.02 |
| 2.5 × ATR | 0.110R | +0.40 |
| 3.0 × ATR | 0.070R | −0.51 |

**Every t-statistic here is below 1.1, so none of these differences is
statistically distinguishable from noise.** The 10-bar breakout variant tops the
table, and picking it for that reason would be textbook selection bias across
eight candidates on data I generated myself.

So the shipped parameters were **not** tuned on these results. They are round,
conventional values chosen before the sweeps ran, and they stayed put. The one
robust signal in the table is that requiring an RSI dip halves the trade count
while roughly doubling gross edge per trade, which is consistent across variants
and matches the design thesis.

The volatility gate showed no effect at all here, because the generator produces
constant volatility by construction. It is retained on reasoning — real FX has
volatility regimes — and is genuinely untested.

## Correctness tests

`python -m pytest tests/` — 35 tests. Beyond indicator arithmetic:

- **Truncation invariance** — trades settled before date *D* are byte-identical
  whether or not the engine is given the bars after *D*.
- **Signal-bar ATR** — stop distance uses the ATR known at signal time, not the
  entry bar's own range.
- **Accounting identity** — final equity equals starting capital plus net trade
  P&L plus open-position value.
- **Pessimistic fills** — a bar containing both stop and target is booked as a
  loss; a gap through the stop fills at the open and can lose more than 1R.
- **Financing** — Wednesday triple swap, weekends charged three nights, credits
  on positive-carry sides.

`npm test` — 25 tests, including the sizing refusals described in the README.

One real bug was found this way: the sizing fallback could return a position
smaller than the broker's lot step (100 units at a broker dealing in 1,000s).
Fixed in `sizing.py` and `positionSizing.js`.

## Reproducing all of it

```bash
cd strategy/backtest
pip install -r requirements.txt

python -m pytest tests/ -q       # correctness
python validate.py               # null + power tests
python run_backtest.py --synthetic trend --days 2000 --seed 2
```

And, with your own data — the test that actually matters:

```bash
python run_backtest.py --data ./data --start 2005-01-01 --end 2016-12-31
python run_backtest.py --data ./data --start 2017-01-01   # once, and only once
```
