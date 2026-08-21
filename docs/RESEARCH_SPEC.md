# TRADING KING — COMPLETE SYSTEM REBUILD

## Objective: Transform the current retail-style scalping bot into a research-driven, multi-specialist algorithmic trading desk

You are acting as a senior quantitative researcher, systematic trader, trading-systems architect, execution engineer, risk manager, and adversarial code reviewer.

Your job is NOT to make the existing bot "more aggressive," add random indicators, or optimize parameters until historical performance looks good.

Your job is to transform the project into a system that behaves conceptually like a disciplined professional trading desk:

* multiple independent trading specialists
* a regime detector
* a senior decision layer
* a portfolio/risk manager
* an execution-quality gate
* strict statistical validation
* realistic transaction-cost modeling
* walk-forward out-of-sample evaluation
* Monte Carlo analysis
* correlation/exposure control
* robust live telemetry
* explicit NO-TRADE states
* zero dependence on wishful thinking
* no assumption that profitability is guaranteed

The system must be designed around one principle:

> A trading system does not need to trade often. It needs to trade only when the statistical conditions supporting an edge are present, execute that edge efficiently, control downside, and survive the inevitable variance.

Do not claim that the resulting system will "never lose," "never fail," or be profitable every month. Those are impossible guarantees. The objective is positive long-run expectancy with controlled risk and enough robustness to survive losing periods.

---

# 1. FIRST: AUDIT THE EXISTING PROJECT BEFORE CHANGING IT

Do not immediately edit the code.

Perform a complete repository audit.

Inspect:

* bot/
* risk/
* execution/
* strategies/
* backtest/
* configuration
* data loading
* logging
* state management
* test suite
* live launcher
* demo launcher
* all symbol definitions
* all spread assumptions
* position sizing
* stop/target logic
* pattern detectors
* signal aggregation
* existing research infrastructure
* existing telemetry
* historical-data support
* any existing OOS or walk-forward infrastructure

Create an explicit inventory of:

1. What already works.
2. What is incomplete.
3. What is dangerous.
4. What is duplicated.
5. What is untested.
6. What is statistically unsupported.
7. What is live-specific.
8. What is demo-specific.
9. What is synthetic.
10. What is accidentally leaking information.
11. What cannot currently be backtested faithfully.

Do NOT hide flaws.

The existing system description already identifies several major issues:

* no validated historical backtest for the current strategy
* live losses
* large live spreads
* one strategy applied across very different instruments
* insufficient correlation control
* small-account capital constraints
* retail-style indicator aggregation

Treat these as starting hypotheses and verify them in code.

---

# 2. FREEZE LIVE TRADING DURING RESEARCH

Do not keep burning live capital while the architecture is being redesigned.

Introduce an explicit system state:

RESEARCH
VALIDATION
PAPER
LIVE

Default state during development:

RESEARCH

LIVE trading must require:

* validated configuration
* passing tests
* passing data-quality checks
* passing OOS criteria
* explicit operator enablement
* broker/execution validation
* risk checks

Never silently fall back into LIVE.

If possible, build a hard configuration guard such as:

TRADING_MODE = "RESEARCH"

and require an explicit production configuration for live trading.

Do not delete existing live functionality. Gate it safely.

---

# 3. STOP THINKING OF THE SYSTEM AS ONE STRATEGY

The current architecture behaves mainly like:

EMA5/EMA20
+
RSI
+
momentum
+
candlestick patterns
+
EMA50 trend filter
==================

entry

That is not a professional team.

Replace the conceptual architecture with specialized agents.

Each agent must have an explicit responsibility.

Agents must not all consume the exact same indicators and vote on the same information.

The objective is information diversity.

---

# 4. CREATE THE SPECIALIST TEAM

Implement the following conceptual specialists.

## SPECIALIST A — REGIME TRADER

Purpose:

Determine WHAT KIND OF MARKET CURRENTLY EXISTS.

Possible states:

TREND_UP
TREND_DOWN
RANGE
BREAKOUT
EXPANSION
COMPRESSION
HIGH_VOLATILITY
LOW_VOLATILITY
CHOP
DEAD_TAPE
UNKNOWN

The regime engine may use:

* ATR
* ATR percentile
* rolling range
* EMA slope
* EMA separation
* price structure
* ADX or equivalent if justified
* realized volatility
* volatility expansion/compression
* VWAP displacement
* range efficiency
* directional persistence
* session behavior

Do not add indicators merely because they are popular.

Every feature must have a reason.

The Regime Trader must be capable of returning:

NO_TRADE

This is critical.

A professional system must know when NOT to deploy a strategy.

---

## SPECIALIST B — MARKET STRUCTURE TRADER

Purpose:

Determine WHERE PRICE IS in the market structure.

Analyze:

* swing highs
* swing lows
* higher highs
* higher lows
* lower highs
* lower lows
* break of structure
* change of character
* local support/resistance
* prior session high/low
* day high/low
* meaningful range boundaries
* liquidity zones where objectively measurable
* distance from structure
* location within current range

Output:

BULLISH_STRUCTURE
BEARISH_STRUCTURE
NEUTRAL_STRUCTURE
CONFLICTED_STRUCTURE

and a confidence score.

Do not allow structure to be reduced to another EMA crossover.

---

# 6. SPECIALIST C — MOMENTUM / DISPLACEMENT TRADER

Purpose:

Determine whether price is actually moving with enough force to justify a directional trade.

Evaluate:

* short-term return
* ATR-normalized displacement
* momentum persistence
* acceleration
* candle body/range relationships
* consecutive directional movement
* volatility expansion
* breakout follow-through
* post-breakout acceptance/rejection

The key question:

> Is the market actually moving, or is the system merely interpreting random noise as momentum?

Output directional conviction plus NO_TRADE when momentum is weak.

---

# 7. SPECIALIST D — MEAN-REVERSION TRADER

Purpose:

Detect when price is sufficiently stretched that reversion has a measurable statistical basis.

Potential features:

* VWAP distance
* ATR-normalized displacement
* percentile distance from local mean
* RSI extremes
* failed breakout
* exhaustion
* re-entry into prior range
* volatility spike followed by contraction

IMPORTANT:

Do not combine trend-following and mean-reversion indiscriminately.

Mean reversion must only activate in regimes where historical data demonstrates that mean reversion has positive expectancy.

Example:

TREND_UP + huge displacement does NOT automatically mean SELL.

The regime decides whether reversion is appropriate.

---

# 8. SPECIALIST E — PATTERN / PRICE-ACTION TRADER

Move the existing pattern logic here.

Existing patterns include concepts such as:

* engulfing
* pin bar
* hammer
* shooting star
* harami
* inside bar
* breakout
* pullback continuation
* etc.

Do NOT assume a pattern is profitable.

Every pattern must eventually receive empirical statistics:

pattern
symbol
timeframe
regime
session
direction
entry type
win rate
average win
average loss
expectancy
profit factor
MFE
MAE
sample size
spread
slippage

A pattern with no validated edge is INFORMATION, not an entry signal.

---

# 9. SPECIALIST F — VWAP / LOCATION TRADER

Create a dedicated location specialist.

Evaluate:

* price relative to VWAP
* VWAP slope
* VWAP distance in ATR units
* interaction with VWAP
* reclaim/loss of VWAP
* extension away from VWAP
* session-specific behavior

The purpose is not "VWAP says BUY."

The purpose is:

> Is the current trade happening at a location where the expected payoff historically justifies taking risk?

---

# 10. SPECIALIST G — SESSION / MARKET MICROSTRUCTURE TRADER

Segment behavior by time.

At minimum investigate:

* Asian session
* London session
* London/New York overlap
* New York session
* session transitions
* rollover/dead periods

Do NOT assume all hours have equal expectancy.

The research engine must determine this.

A strategy may be profitable during one session and structurally unprofitable during another.

---

# 11. SPECIALIST H — NEWS / EVENT SAFETY GATE

If reliable event data is available, create an event filter.

The system should be able to detect:

* major scheduled economic events
* abnormal volatility
* spread explosion
* execution deterioration

If reliable news data is not available, implement a conservative market-based proxy rather than pretending the bot knows news.

During dangerous conditions:

NO_TRADE

---

# 12. SPECIALIST I — EXECUTION SPECIALIST

This specialist does not predict direction.

Its job is:

> Can we afford to execute this trade?

Evaluate:

* current spread
* expected spread
* spread relative to ATR
* recent spread percentile
* tick freshness
* latency
* recent slippage
* execution failure rate
* broker conditions
* stop-distance constraints
* expected transaction cost
* expected cost as percentage of theoretical edge

A trade with a good signal but terrible execution conditions must be rejected.

Example:

THEORETICAL EDGE = +0.20R
EXPECTED EXECUTION COST = -0.25R

FINAL EXPECTED EDGE < 0

→ NO TRADE

This rule is mandatory.

---

# 13. BUILD A REAL SENIOR DECISION MAKER

Do not simply average all agents.

Create a senior decision layer.

Inputs:

* regime
* structure
* momentum
* reversion
* patterns
* VWAP/location
* session
* execution
* volatility
* historical context
* current portfolio exposure

The senior layer decides:

BUY
SELL
NO_TRADE

The default output should be NO_TRADE unless sufficient evidence exists.

Implement explicit conflict handling.

Example:

Regime = TREND_UP
Structure = BULLISH
Momentum = STRONG
Pattern = BULLISH
VWAP = BULLISH
Execution = GOOD

→ HIGH-CONVICTION BUY candidate

Another example:

Regime = CHOP
Structure = CONFLICTED
Momentum = WEAK
Pattern = BULLISH

→ NO_TRADE

Another:

Regime = TREND_UP
Structure = BULLISH
Momentum = STRONG
Pattern = BEARISH
Execution = BAD

→ NO_TRADE

Do not force a vote.

Conflict should usually reduce conviction, not average into an arbitrary trade.

---

# 14. BUILD A PORTFOLIO MANAGER

The system must stop treating each symbol independently.

Portfolio manager responsibilities:

* total exposure
* directional exposure
* currency exposure
* correlated positions
* sector/factor exposure where applicable
* gross exposure
* net exposure
* maximum simultaneous positions
* maximum correlated positions
* risk concentration
* daily risk budget

Example:

EURUSD BUY
GBPUSD BUY
AUDUSD BUY

must be recognized as potentially concentrated USD-short exposure.

Do not pretend three correlated trades equal diversification.

Implement correlation-aware risk budgeting.

Use rolling correlations from historical returns.

Correlation must influence position size and/or eligibility.

---

# 15. BUILD A REAL RISK ENGINE

Risk must be based on actual expected loss, not merely lot size.

For every trade determine:

entry
stop
distance
spread
expected slippage
commission
contract size
point value
currency conversion
worst-case planned loss

Then calculate:

risk_R

and

risk_as_percent_of_equity

Do not rely on hardcoded contract specifications.

Use symbol metadata.

Keep:

* maximum risk per trade
* maximum daily risk
* maximum drawdown
* consecutive loss protection
* exposure limits
* correlation limits
* margin limits
* abnormal-market protection

But do not create arbitrary safety rules that unintentionally destroy positive expectancy.

Every risk constraint must be separately testable.

---

# 16. REBUILD POSITION SIZING AROUND VOLATILITY AND PORTFOLIO RISK

Do NOT simply use:

equity × 1%

then divide by stop.

Instead investigate:

* volatility targeting
* inverse-volatility adjustment
* correlation-adjusted risk
* regime-dependent risk
* portfolio heat
* max drawdown state
* expected shortfall

However:

Do not implement sophisticated sizing until the underlying entry edge is validated.

Risk engineering cannot rescue a negative-expectancy strategy.

---

# 17. GIVE EVERY TRADE AN EXPECTED-VALUE OBJECT

Every candidate trade should carry something like:

expected_win_rate
expected_win_R
expected_loss_R
expected_cost_R
expected_value_R
confidence
sample_size
regime
session
symbol
strategy_family
signal_reasons

Calculate:

EV = P(win) × AvgWin - P(loss) × AvgLoss - transaction_costs

Only allow execution when the estimated edge exceeds a conservative minimum threshold.

Do not use a threshold that was tuned solely to maximize historical profit.

The threshold itself must be validated OOS.

---

# 18. BUILD THE HISTORICAL BACKTEST ENGINE BEFORE OPTIMIZATION

This is mandatory.

The current system must be backtested on real historical data.

Do NOT use synthetic OHLCV to claim profitability.

Use real historical M5 data for every supported symbol.

For every historical bar model:

* bid
* ask
* spread
* entry side
* exit side
* slippage
* stop execution
* target execution
* latency assumptions where relevant
* trading hours
* symbol-specific point value
* contract size
* commission
* swap where relevant

Never use midpoint close as if it were an executable price.

A BUY enters on ask.

A SELL enters on bid.

A BUY exits on bid.

A SELL exits on ask.

This must be enforced in backtesting.

---

# 19. MODEL REALISTIC SPREADS

Do NOT assume demo spreads.

Create per-symbol spread models.

At minimum support:

* fixed spread
* empirical historical spread
* percentile spread model
* stress spread
* session-dependent spread

Test:

BASE
REALISTIC
STRESSED

The strategy must survive realistic transaction costs.

If profitability disappears once realistic costs are applied:

MARK STRATEGY AS NONVIABLE

Do not "optimize around" this by cheating with unrealistic fills.

---

# 20. BUILD A PROPER RESEARCH PIPELINE

The research system must support:

TRAIN / RESEARCH PERIOD
VALIDATION PERIOD
OUT-OF-SAMPLE PERIOD

Prefer walk-forward validation.

Example:

Fold 1:
train → validation → OOS

Fold 2:
train → validation → OOS

Fold 3:
train → validation → OOS

Fold 4:
train → validation → OOS

Then pool OOS results.

Do not judge the strategy only by aggregate in-sample equity curves.

---

# 21. PREVENT DATA LEAKAGE

Audit for:

* future bars
* future spread
* future volatility
* lookahead indicators
* accidental use of current close before candle completion
* parameter tuning on OOS
* overlapping datasets
* duplicate samples
* survivorship bias
* selection bias

Explicitly test that no feature at timestamp T uses information from T+1 onward.

For M5 trading, define clearly whether a signal is generated:

* at candle close
* inside candle
* on next tick

Do not mix these models.

---

# 22. RUN REAL MONTE CARLO ANALYSIS

After obtaining OOS trade results:

run:

* trade-order reshuffling
* bootstrap
* win/loss sequence simulation
* drawdown distribution
* losing-streak distribution
* expected monthly return distribution
* probability of ruin
* worst historical-equivalent drawdown
* 5th percentile / 1st percentile outcomes

The objective is NOT:

"What is the maximum possible profit?"

The objective is:

"What can realistically happen to us even if we have the same edge?"

---

# 23. DEFINE A REAL STRATEGY ACCEPTANCE PROTOCOL

A strategy cannot become live simply because:

* win rate > 50%
* profit > 0
* equity curve looks nice

Create explicit promotion criteria.

Potential criteria:

1. Positive OOS expectancy.
2. Positive expectancy after realistic spread/slippage.
3. Stability across multiple walk-forward folds.
4. No single month responsible for most profits.
5. No single symbol responsible for nearly all profits.
6. No single pattern responsible for nearly all profits unless explicitly intended.
7. Acceptable maximum drawdown.
8. Acceptable Monte Carlo risk of ruin.
9. Minimum sample size.
10. Performance not dependent on one hyperparameter.
11. No obvious leakage.
12. Execution cost remains below expected edge.
13. Results survive stressed costs.
14. Strategy remains viable after removing the best few trades.

A strategy that fails these requirements remains RESEARCH ONLY.

---

# 24. TEST ROBUSTNESS, NOT JUST PROFIT

Perform parameter perturbation.

If EMA:

5 / 20

is profitable, test nearby ranges such as:

4–7
18–24

If performance only works at exactly 5 and 20:

flag possible overfitting.

Apply the same principle to:

* ATR multipliers
* RSI thresholds
* pattern thresholds
* stop distance
* target distance
* regime thresholds
* entry thresholds

A robust strategy should occupy a region of parameter space, not a single magic number.

---

# 25. REMOVE LOW-VALUE INDICATOR REDUNDANCY

Detect whether multiple signals are mathematically expressing the same thing.

For example:

EMA crossover
EMA position
momentum vs EMA

may be highly correlated information.

Do not pretend three correlated indicators are three independent traders.

Measure feature correlation and conditional contribution.

The research layer should ask:

> Does this feature provide incremental information after the others already know it?

If not, remove or reduce its influence.

---

# 26. CREATE STRATEGY FAMILIES

Instead of one universal scalper, implement strategy families.

Example:

TREND_FOLLOWING
BREAKOUT
PULLBACK
MEAN_REVERSION
REVERSAL
RANGE
MOMENTUM_CONTINUATION

Each strategy family should activate only under appropriate regimes.

Example:

TREND_FOLLOWING:
allowed in TREND_UP/TREND_DOWN

MEAN_REVERSION:
allowed in RANGE / exhaustion regimes

BREAKOUT:
allowed during compression → expansion transitions

CHOP:
everything disabled

This is far more important than adding more indicators.

---

# 27. LET THE REGIME ENGINE CONTROL WHICH TEAM MEMBERS ARE ACTIVE

The system should behave like an actual desk.

Example:

### Trending market

Active:

* structure trader
* momentum trader
* trend strategy
* pullback strategy

Inactive:

* aggressive mean reversion

### Range market

Active:

* mean reversion
* location/VWAP

Inactive:

* trend breakout unless breakout evidence appears

### Compression → expansion

Active:

* breakout specialist
* momentum specialist

### Dead tape

Active:
NONE

This is the central architecture.

---

# 28. ADD TRADE JOURNALING AT RESEARCH DEPTH

For every candidate, even rejected candidates, log:

timestamp
symbol
session
regime
market structure
strategy family
agent opinions
composite score
expected value
spread
spread percentile
ATR
VWAP displacement
volatility state
correlation state
portfolio heat
decision
rejection reason

For executed trades:

entry
exit
side
planned SL
actual SL
planned TP
actual exit
MFE
MAE
R
slippage
spread at entry
spread at exit
latency
duration
agent opinions
regime transition
exit reason

This makes the system scientifically inspectable.

---

# 29. ANALYZE REJECTIONS, NOT JUST TRADES

This is extremely important.

A professional system learns not only:

"What trades won?"

but:

"What trades did we correctly refuse?"

Track:

* rejected because regime
* rejected because spread
* rejected because correlation
* rejected because weak structure
* rejected because insufficient EV
* rejected because volatility
* rejected because portfolio risk

Then retrospectively determine:

Did rejected trades perform better or worse than executed trades?

This tells us whether filters are genuinely useful or simply reducing trade count.

---

# 30. BUILD A "WHY DID WE TRADE?" REPORT

Every executed trade must be explainable.

Example:

TRADE #1842

BUY EURUSD

Regime:
TREND_UP

Structure:
BULLISH

Momentum:
STRONG

VWAP:
PRICE ABOVE VWAP

Pattern:
PULLBACK_CONTINUATION

Session:
LONDON/NY OVERLAP

Spread:
LOW

Execution:
GOOD

Correlation:
ACCEPTABLE

Expected Value:
+0.17R

Risk:
0.35%

Decision:
BUY

If the system cannot explain its own trade in structured terms, it should not be considered production-grade.

---

# 31. BUILD A "WHY DID WE NOT TRADE?" REPORT

Example:

Candidate:
BUY GBPUSD

Rejected.

Reasons:

* regime = CHOP
* structure = conflicted
* expected edge = +0.04R
* expected spread cost = -0.11R
* correlated USD exposure already high

Final:

NO_TRADE

This will become one of the most valuable research tools in the entire project.

---

# 32. DO NOT OPTIMIZE FOR TRADE FREQUENCY

Never use:

"more trades = better system"

The desired behavior may be:

1000 candidate signals
↓
700 rejected by regime
↓
150 rejected by location
↓
80 rejected by execution
↓
40 rejected by portfolio risk
↓
30 high-quality opportunities
↓
20 executed

That is completely acceptable.

A professional desk filters aggressively.

---

# 33. DO NOT CHASE MONTHLY PROFITABILITY

Do not create a system that tries to force:

January profit
February profit
March profit
etc.

Some months should be negative.

The correct objective is:

positive long-run expectancy
+
bounded downside
+
robustness
+
survival

Create reporting around:

daily
weekly
monthly
quarterly
rolling 100 trades
rolling 250 trades
rolling 500 trades

But do not classify a single losing month as strategy failure.

---

# 34. DEFINE STATISTICAL VERDICTS

Use explicit statistical states:

UNPROVEN
NEGATIVE
INCONCLUSIVE
PROMISING
ROBUST
PRODUCTION_ELIGIBLE

Do not call a strategy "profitable" merely because cumulative PnL > 0.

Use:

mean R/trade
confidence interval
bootstrap interval
profit factor
expectancy
max drawdown
ulcer-like metrics if appropriate
risk of ruin
sample size

The system should clearly distinguish:

"looks good"

from

"has sufficient evidence."

---

# 35. BUILD A FORWARD LIVE VALIDATION PROTOCOL

Before production:

Freeze:

* strategy logic
* parameters
* risk
* filters
* execution rules

Then run forward paper trading or tiny-risk validation.

Do not modify the system every time it loses a trade.

Record all results.

Only evaluate after a predefined sample.

This prevents emotional/cherry-picked iteration.

---

# 36. CAPITAL REALITY CHECK

The current ~$31 live balance must not be treated as sufficient capital for proving a professional scalping operation.

Do not increase risk to compensate for the tiny account.

Do not use leverage simply to make monthly returns look impressive.

The system should report:

MINIMUM PRACTICAL CAPITAL

based on:

* broker minimum lot
* stop distance
* maximum risk %
* margin
* expected drawdown
* spread
* correlation exposure

If the desired risk cannot be implemented safely at the account size:

RETURN CAPITAL_INSUFFICIENT

NOT:

"increase risk."

---

# 37. SYMBOL SELECTION MUST BE EMPIRICAL

Do not automatically trade every available instrument.

For each symbol calculate:

* spread/ATR
* average spread
* spread percentile
* volatility
* liquidity proxy
* historical expectancy
* execution quality
* correlation profile
* session behavior

Each symbol gets a state:

TRADEABLE
CONDITIONALLY_TRADEABLE
UNTRADEABLE

A 167-pip-spread instrument should not be traded simply because the code can technically place orders.

---

# 38. CREATE PER-SYMBOL AND PER-REGIME CONFIGURATION

Avoid:

"one strategy for everything."

Parameters can differ by:

symbol
session
regime
strategy family

BUT:

every parameter difference must eventually be justified using out-of-sample evidence.

Do not create dozens of parameters merely because they are available.

Complexity must earn its place.

---

# 39. DO NOT MAKE MACHINE LEARNING THE DEFAULT SOLUTION

Do not introduce ML simply to make the system sound sophisticated.

First prove:

1. data quality
2. executable pricing
3. baseline edge
4. regime segmentation
5. robust rule-based strategies
6. OOS validation

Only then investigate ML if it provides incremental out-of-sample information.

ML must beat a strong simple baseline out-of-sample.

Otherwise it does not belong in production.

---

# 40. TEST WHETHER THE "TEAM" ACTUALLY ADDS VALUE

Run ablation studies:

Baseline
vs
Baseline + Regime
vs
Baseline + Structure
vs
Baseline + Momentum
vs
Baseline + VWAP
vs
Baseline + Execution
vs
Full Team

Measure each addition on:

OOS expectancy
drawdown
trade count
profit factor
cost-adjusted return
robustness

This prevents fake complexity.

If an agent does not improve the system:

REMOVE IT.

---

# 41. TEST THE SYSTEM WITHOUT ITS BEST TRADES

Run:

full results

then remove:

best 1 trade
best 5 trades
best 10 trades
best 1%

If the entire performance collapses, flag concentration.

A professional system should not rely on one extraordinary trade.

---

# 42. STRESS TRANSACTION COSTS

Run:

normal spread
+25%
+50%
+100%

and:

normal slippage
+25%
+50%
+100%

The strategy must be evaluated against degraded execution.

If the edge disappears under mild stress:

MARK FRAGILE

Do not declare success.

---

# 43. BUILD AN EQUITY-CURVE HEALTH MONITOR

Monitor:

rolling expectancy
rolling win rate
rolling payoff ratio
rolling drawdown
rolling spread cost
rolling slippage
rolling execution latency
regime-specific expectancy

If the live behavior statistically diverges from validated expectations:

REDUCE RISK
or
STOP TRADING

Do not automatically retune.

First investigate.

---

# 44. ADD A KILL SWITCH FOR MODEL/EDGE DECAY

If:

rolling expectancy falls significantly below validated baseline
AND
sample size is sufficient

then:

DEGRADED

Possible action:

* reduce risk
* switch to paper trading
* disable affected strategy family

Do not continuously tune against live noise.

---

# 45. TEST THE DIFFERENCE BETWEEN SIGNAL QUALITY AND EXECUTION QUALITY

Store separately:

THEORETICAL TRADE RESULT

and

ACTUAL EXECUTED RESULT

This tells us whether failure comes from:

strategy

or

broker/execution.

Example:

Signal expectancy:
+0.18R

Actual after spread/slippage:
-0.07R

Conclusion:

strategy may have an edge,
but execution destroys it.

This is a completely different engineering problem.

---

# 46. DO NOT CONFUSE RISK MANAGEMENT WITH EDGE

Explicitly preserve this distinction throughout the code and reports.

Risk management answers:

"How much can we lose?"

Strategy answers:

"Why should price move in our favor?"

Execution answers:

"Can we capture the edge?"

Portfolio management answers:

"Are we taking redundant risk?"

These are different layers.

Do not let one masquerade as another.

---

# 47. PRODUCTION ARCHITECTURE

Target architecture:

DATA
↓
DATA QUALITY
↓
FEATURE ENGINE
↓
REGIME ENGINE
↓
SPECIALIST AGENTS
↓
SENIOR DECISION ENGINE
↓
EXPECTED-VALUE ENGINE
↓
PORTFOLIO MANAGER
↓
RISK ENGINE
↓
EXECUTION GATE
↓
ORDER ENGINE
↓
POSITION MANAGER
↓
TELEMETRY / JOURNAL
↓
RESEARCH LOOP

Every layer should be independently testable.

---

# 48. TEST SUITE

Create tests for:

* every agent
* every feature
* signal conflicts
* NO_TRADE behavior
* spread gating
* correlation gating
* risk limits
* lot sizing
* stop/target calculations
* bid/ask execution
* slippage
* contract sizes
* MFE/MAE
* portfolio heat
* regime transitions
* emergency flattening
* state recovery
* disconnects
* stale data
* missing bars
* abnormal spreads
* partial failures

Do not sacrifice existing passing tests.

Run the full suite after every major architectural phase.

---

# 49. BACKTEST ACCEPTANCE REPORT

Create an automated report containing:

## GLOBAL

Trades
Expectancy
Win rate
Average win
Average loss
Profit factor
Max drawdown
Return
Risk-adjusted return
Average holding time
Transaction cost
Slippage
Monte Carlo drawdown
Probability of ruin

## BY SYMBOL

same metrics

## BY REGIME

same metrics

## BY SESSION

same metrics

## BY STRATEGY FAMILY

same metrics

## BY SPECIALIST

contribution and incremental value

## BY WALK-FORWARD FOLD

same metrics

## BY COST SCENARIO

normal / stressed

This is mandatory.

---

# 50. FINAL QUALITY GATE

Do not tell the user:

"Done, profitable."

Instead return one of:

NOT READY
RESEARCH ONLY
PROMISING
VALIDATION REQUIRED
PRODUCTION ELIGIBLE

and explain exactly why.

A production eligibility report must contain evidence.

No marketing language.

No "AI trader."

No "institutional grade" claims without evidence.

No "never loses."

No guaranteed monthly profitability.

---

# 51. HIGHEST PRIORITY ORDER

Do the work in this order:

PHASE 1
Audit

PHASE 2
Safe live gating

PHASE 3
Real historical data pipeline

PHASE 4
Executable bid/ask backtest

PHASE 5
Baseline strategy validation

PHASE 6
Regime engine

PHASE 7
Specialist architecture

PHASE 8
Senior decision engine

PHASE 9
Portfolio manager

PHASE 10
Execution-cost model

PHASE 11
Walk-forward OOS

PHASE 12
Monte Carlo

PHASE 13
Ablation / robustness tests

PHASE 14
Forward paper validation

PHASE 15
Tiny-risk production validation

PHASE 16
Only then consider scaling

Do not skip the foundational research because the user wants the system to trade immediately.

---

# 52. IMPORTANT: DO NOT OVERENGINEER WITHOUT EVIDENCE

The system should not become a giant pile of heuristics.

Every component must answer:

1. What hypothesis does this encode?
2. How is it measured?
3. How is it tested?
4. Does it add incremental OOS value?
5. Does it survive realistic costs?
6. Is it robust to parameter perturbation?

If the answer is "we thought professional traders might use it," that is insufficient.

---

# 53. THE FINAL BEHAVIOR WE WANT

The finished system should conceptually behave like this:

MARKET DATA ARRIVES

↓

"WHAT REGIME IS THIS?"

↓

"WHERE ARE WE?"

↓

"IS THERE A STRUCTURAL OPPORTUNITY?"

↓

"IS PRICE ACTUALLY MOVING?"

↓

"IS THIS A VALID STRATEGY FOR THIS REGIME?"

↓

"DO MULTIPLE INDEPENDENT SPECIALISTS SUPPORT IT?"

↓

"WHAT IS THE HISTORICALLY EXPECTED VALUE OF THIS SETUP?"

↓

"DO SPREAD + SLIPPAGE + EXECUTION COSTS LEAVE POSITIVE EXPECTANCY?"

↓

"DOES THE PORTFOLIO ALREADY HAVE THE SAME RISK?"

↓

"IS THE REQUIRED RISK ACCEPTABLE?"

↓

YES → EXECUTE

NO → DO NOTHING

Then once in the position:

MANAGE RISK

↓

MEASURE MFE / MAE

↓

EXECUTE EXIT LOGIC

↓

JOURNAL EVERYTHING

↓

COMPARE LIVE RESULT TO VALIDATED EXPECTATION

↓

DETECT EDGE DECAY

This is the desired system.

---

# 54. THE CENTRAL PRINCIPLE

The objective is NOT:

"make this bot trade like a team of traders who never lose."

The objective is:

"build a system where multiple specialized decision processes independently evaluate the market, a senior layer only allows statistically justified opportunities, the portfolio manager controls aggregate exposure, the execution layer protects expected edge, and every claimed advantage is proven through realistic out-of-sample evidence."

That is what "professional" should mean in this project.

---

# 55. WHAT YOU MUST NOT DO

DO NOT:

* optimize directly on the final OOS set
* use future information
* use midpoint fills for executable trades
* assume demo spreads
* add indicators without hypothesis
* assume higher win rate means better system
* maximize trade count
* optimize monthly profitability
* promise no losing months
* increase risk to compensate for small capital
* silently change live logic
* continuously retune after losses
* hide losing results
* delete failed experiments
* call an unvalidated strategy profitable
* introduce ML simply for complexity
* keep untradeable symbols because they are available
* treat correlated positions as independent risk
* confuse risk control with trading edge

---

# 56. REQUIRED FINAL DELIVERABLE

After implementing the architecture, produce:

1. Repository audit
2. Architecture diagram
3. Component inventory
4. Research assumptions
5. Backtest engine
6. Specialist agents
7. Senior decision engine
8. Portfolio manager
9. Risk engine improvements
10. Execution-cost model
11. Walk-forward framework
12. Monte Carlo framework
13. Ablation tests
14. Robustness tests
15. Full automated test suite
16. Research report
17. Production-readiness report

The final report must explicitly answer:

* Does the strategy have positive expectancy?
* Does it remain positive after realistic spread/slippage?
* Which strategy families actually work?
* Which symbols actually work?
* Which regimes actually work?
* Which sessions actually work?
* Which agents add genuine incremental value?
* What is the OOS expectancy?
* What is the realistic drawdown?
* What is the probability of a severe losing streak?
* What happens under stressed execution costs?
* What happens if the best trades are removed?
* What is the minimum practical capital?
* Is the system ready for live trading?

If the answer to any of these is unknown, report it as UNKNOWN.

Never substitute confidence for evidence.

The final system should earn the right to trade.

# END OBJECTIVE
