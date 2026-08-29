# Trend Pullback Continuation (TPC)

A daily-bar forex swing strategy. Trades are held from a few days to a few
weeks, entries and exits are decided once per day after the New York close, and
position sizing is built around a 500 EUR account rather than bolted on
afterwards.

## Read this first

**Nobody can hand you a strategy that is guaranteed profitable, and I have not
done so.** What is here is a mechanical rule set with a documented rationale, a
backtester that is deliberately hard on itself, and honest measurements of what
the strategy needs in order to make money. Whether it *does* make money on live
prices is something you have to establish on real data — [RESULTS.md](RESULTS.md)
records exactly what has and has not been tested, and it is short on claims for
a reason.

Two numbers set expectations before anything else:

- **The strategy pays about 0.09R per trade in costs**, almost all of it
  overnight financing. Any edge smaller than that loses money. That is the bar.
- **At 1% risk per trade with roughly 25 trades a year**, a genuinely good
  outcome on 500 EUR is *tens of euros a year*, with drawdowns of 10–20% along
  the way. Anyone quoting you 10% a month is quoting you a story.

A 500 EUR account is a training account. Its job is to let you run a hundred
trades and find out whether you can follow rules under pressure, while the
tuition is affordable. Treat compounding it as a bonus, not the goal.

## The constraint that decides everything: lot granularity

Risking 1% of 500 EUR is 5 EUR. To lose exactly 5 EUR on a 140-pip EURUSD stop
you need about **390 units** of EURUSD. Whether your broker can sell you 390
units is the single biggest determinant of whether this strategy is tradeable
at 500 EUR.

| Stop distance | Units for a 5 EUR risk | Nano broker (0.001 lot) | Micro broker (0.01 lot) |
| --- | --- | --- | --- |
| 60 pips | 908 | 900 units, risks 4.95 EUR | 1,000 units, risks 5.50 EUR (1.1%) |
| 100 pips | 545 | 500 units, risks 4.59 EUR | 1,000 units, risks **9.17 EUR (1.8%)** |
| 140 pips | 389 | 300 units, risks 3.85 EUR | 1,000 units, risks **12.84 EUR (2.6%)** |
| 200 pips | 273 | 200 units, risks 3.67 EUR | 1,000 units, risks **18.35 EUR (3.7%)** |

Daily-bar stops on the majors mostly land in the 100–200 pip band. So on a
0.01-lot broker, a 500 EUR account cannot risk 1% — its smallest possible trade
risks 2–4%. Six losers in a row is an ordinary run for a system that wins 40% of
the time; at 1% that is −6%, at 3% it is −17%.

**Therefore: use a broker whose minimum position is 0.001 lots (100 units) or
smaller.** Check the contract specification for "minimum volume" and "volume
step" before funding anything. Where that is impossible, the honest options are
to fund ~1,300 EUR instead, or to accept 2.5% risk per trade and read the ruin
column of the risk table below. There is no third option where 500 EUR risks 1%
on a micro-lot broker.

`src/positionSizing.js` and `strategy/backtest/swingfx/sizing.py` both refuse to
size a trade whose smallest legal ticket would breach the risk budget, rather
than silently rounding you into a bigger bet.

## Universe and timeframe

Five majors: **EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD**. They have the tightest
spreads and the smallest financing markups, which is what matters when you hold
for weeks. Crosses like GBPJPY are excluded: wider spreads, wider stops, worse
swap, and on 500 EUR the position sizes get too small to be legal.

Everything runs on **daily bars**, evaluated once after the 17:00 New York
close. This is a deliberate design constraint, not a limitation — it means the
strategy takes about ten minutes a day and cannot be traded impulsively.

## The rules

Signals are computed on today's close. Orders are placed for **tomorrow's
open**. Every rule below is mechanical; none of them requires an opinion.

### Entry — long (shorts are the exact mirror)

All five conditions must be true on the same daily close:

1. **Trend** — `EMA(50) > EMA(200)` and `close > EMA(200)`
2. **Momentum** — 60-day return > 0
3. **Volatility gate** — `ATR(14) / close` is between 0.30% and 2.00%
4. **Pullback** — `RSI(14)` has closed below 42 at some point in the last 5 bars
5. **Trigger** — today's close is above the highest high of the previous 2 bars

Conditions 1 and 2 say the trend is real, on two different lookbacks. Condition
3 skips markets that are too dead to follow through and markets whose stop
distance would be too wide to size. Conditions 4 and 5 are the actual edge
thesis: buy a pullback inside an established uptrend, once it has started to
resume, rather than chasing a breakout.

The pullback filter is not decoration. In testing it cut trade count by more
than half and roughly doubled the gross edge per trade — the largest structural
effect measured anywhere in this project.

### Position sizing

```
risk budget  = equity x 1%
stop distance = 2.0 x ATR(14)      (using the ATR of the SIGNAL bar)
units         = risk budget / (stop distance converted to EUR)
units         = round DOWN to the broker's lot step
if units < the broker's minimum ticket -> SKIP THE TRADE
```

### Exit

Three rules, checked in this order, once per day:

1. **Initial stop** — `entry − 2.0 × ATR` for longs. This sits in the market as
   a real stop order from the moment you are filled, so a weekend gap cannot
   run past it unattended.
2. **Trailing stop** — once the trade has been 1R in your favour at any point,
   trail with a chandelier stop: `highest high since entry − 3.0 × ATR(14)`,
   recomputed daily. It only ever tightens.
3. **Time stop** — if a trade is still below +0.5R after 15 bars, close it at
   the next close. Dead trades occupy one of three slots and pay financing every
   night to do it.

There is no fixed profit target, and **no scaling out at 1R** — that was in the
first draft of this strategy and the backtest killed it. Taking half off at 1R
and moving to breakeven dropped the best trade in the sample from 9.5R to 2.9R
and cut average winners from 1.25R to 0.86R. Trend systems earn everything in
the right tail; rules that feel prudent by capping winners remove the only part
that pays. If your position is too small to scale out anyway — which it usually
is at 500 EUR — this is moot, which is a hint that it was never the right design
for this account size.

### Portfolio rules

| Rule | Value | Why |
| --- | --- | --- |
| Max open positions | 3 | 500 EUR cannot diversify further; more slots just means more correlated risk |
| Max total open risk | 3% | worst case if every open trade gaps through its stop |
| Max exposure per currency | 2 | long EURUSD + short USDJPY + long AUDUSD is one short-USD bet in a trenchcoat, not three trades |
| Weekly loss limit | −5% | stop opening new trades until the next Monday |
| Drawdown throttle | −15% from peak | halve risk per trade until a new equity high |

The currency-exposure cap matters more than it looks. The majors are dominated
by the USD leg, so an unconstrained system routinely stacks three positions that
are all the same trade.

## Costs: the finding that should change how you choose a broker

Measured across the test runs, per trade:

| Cost | Size | Share |
| --- | --- | --- |
| Overnight financing (swap) | ~0.090R | **94%** |
| Spread + slippage + commission | ~0.005R | 6% |

At a 15-day average hold, the spread everyone shops for is a rounding error and
the financing almost nobody reads is the entire bill. Consequences:

- **Compare brokers on their swap table, not their spread.** Swap markups differ
  by a factor of three between brokers on the same pair.
- **Carry direction is worth more than any indicator setting.** Holding the
  positive-carry side turns roughly −0.09R into a credit — a swing bigger than
  the strategy's whole gross edge.
- The backtester takes your broker's real numbers: put them in a JSON file and
  pass `--swap-json`. `--max-financing-r 0.25` will then refuse signals that
  would bleed more than a quarter of the risk budget in swap over a full hold.

## Risk per trade, and the ruin arithmetic

`run_backtest.py` prints a bootstrap of your own trade results at different
risk levels. Read the last column before choosing:

- **0.5%** — barely moves the account; appropriate while you are learning
- **1.0%** — the default here; drawdowns in the teens are normal
- **2.0%** — drawdowns in the twenties, and you feel every one of them
- **3%+** — a losing streak that a real edge would survive can still halve the
  account

Higher risk buys higher median returns and a fatter left tail simultaneously.
The bootstrap shows both. Choose with the numbers in front of you.

## Before you risk actual money

1. **Get real data and backtest it.** See
   [backtest/README.md](backtest/README.md). Fit on 2005–2016, then look at
   2017 onward *exactly once*. Peeking repeatedly turns out-of-sample into
   in-sample.
2. **Backtest it on TradingView too.** [pine/trend_pullback_continuation.pine](pine/trend_pullback_continuation.pine)
   implements identical rules on TradingView's own price history. Two
   independent implementations agreeing is a meaningful check; two
   disagreeing means one of them has a bug worth finding.
3. **Forward-test on demo for 30 trades.** Not 30 days — 30 trades. You are
   testing whether you can follow the rules, which is a different question from
   whether the rules work.
4. **Then go live at 0.5% risk** and stay there until you have 50 live trades.

If steps 1–3 do not show an edge on your data and your broker's costs, the
correct conclusion is not to trade it. That outcome is a successful use of this
repository.

## Running it day to day

Once a day, after the New York close (22:00 or 23:00 CET depending on DST):

1. Check open positions: update trailing stops with the broker, close anything
   that hit its time stop.
2. Scan the five pairs for new signals.
3. For each signal, compute the size — or let the webhook do it (below) — and
   place the order for the next open with its protective stop attached.
4. Write down the trade and the reason. The log is what makes a bad month
   diagnosable instead of demoralising.

### Automating the alert

This repo is a TradingView webhook receiver, so the loop can be wired up:

1. Add the Pine strategy to a daily chart of each pair.
2. Set its **Webhook secret** input to your `TRADINGVIEW_WEBHOOK_SECRET`.
3. Create an alert → condition: the strategy → **Any alert() function call**,
   leaving the message box empty. Point the webhook URL at
   `https://<your-host>/webhook/tradingview`.
4. Configure the account in `.env`:

   ```
   ACCOUNT_EQUITY_EUR=500
   RISK_PER_TRADE=0.01
   LOT_STEP_UNITS=100
   MIN_UNITS=100
   ```

Alerts then arrive with a `sizing` block attached — units to trade, the euro
risk, and the stop distance in pips — or with `skippedReason` explaining why
that trade is not takeable on your account. Sizes are computed from the rates in
`QUOTE_RATES_JSON`; refresh them every month or so, though lot rounding moves
the answer more than a stale rate will.

**The webhook tells you what to trade. It does not place orders.** Nothing here
touches a broker API, and adding that is a much bigger decision than adding an
indicator.

## Honest failure modes

- **Trend following has long flat periods.** 12–18 months without a new equity
  high is normal, not evidence of breakage. Decide now what would actually make
  you stop, and write it down while you are calm.
- **Correlated stop-outs.** When the dollar reverses, several positions lose
  together. That is what the currency-exposure cap is for, and it only reduces
  the problem.
- **This is a small edge.** Costs eat most of it. Broker choice and discipline
  matter more than any parameter in this document.
- **Parameters are round numbers, not optimised values.** That is deliberate —
  see RESULTS.md — but it also means nothing here has been proven best.

## Files

| Path | What it is |
| --- | --- |
| `strategy/README.md` | this document — the rules |
| `strategy/RESULTS.md` | what was tested, what was found, what was not |
| `strategy/pine/trend_pullback_continuation.pine` | TradingView implementation + webhook alerts |
| `strategy/backtest/` | Python backtester, validation harness, tests |
| `src/positionSizing.js` | account-aware sizing for incoming alerts |
