# Gold Trading Bot — Technical Brief for Consultant Review

## 1. Overview

Fully automated XAUUSD (gold) trading bot running on MetaTrader 5 (the broker live account). Executes market orders every ~60 seconds on M5 candles. All decisions are deterministic from candle data — no manual intervention.

**Account:** the broker live, $55.86 balance, 0.01 lots fixed.

---

## 2. Entry Decision Pipeline (in order)

Every scan follows this exact sequence. If any step blocks, the signal is rejected and the next scan begins fresh.

### Step 1: Chop Filter (`_is_chop`)
Kills entries in ranging/compressing markets. Blocks if ANY of these are true:
- EMA gap (EMA20 vs EMA50) < 0.05% of price → EMAs are flat/converged = no trend
- ATR ratio (ATR14 / ATR14_baseline) between 0.80 and 1.20 → market is in compression or neutral, not expanding
- ADX < 20 → trend strength too weak to trade

**Current behavior:** ~71% of scans blocked by chop filter today. Market has been in compression (11pt range, EMA gap <0.05%, ATR ratio 0.87-0.93).

### Step 2: Pattern Detection & Scoring
Scans M5 candles for pattern clusters. Each pattern adds to a directional score:

**Bullish patterns:** morning_star, bull_pin_bar, bull_pullback_cont, bull_alignment, inside_bar_breakout, higher_highs, last_bar_impulse
**Bearish patterns:** evening_star, bear_pin_bar, bear_pullback_cont, bear_alignment, inside_bar_breakdown, lower_lows, last_bar_impulse

Additional scoring factors:
- RSI sweet spot (30-45 for longs, 55-70 for shorts)
- VWAP cross (price above/below VWAP)

Score range: -1.0 to +1.0. **MIN_SCORE = 0.50** required to proceed.

### Step 3: Trend Filter
Checks M30 trend alignment. A trade must align with the higher timeframe trend.
- EMA20 > EMA50 on M30 = bullish trend → only longs allowed
- EMA20 < EMA50 on M30 = bearish trend → only shorts allowed

Can be disabled via `TREND_FILTER=false` (currently ON).

### Step 4: Consultant (LLM)
Sends the setup to a Claude LLM (via 9router, model `kr/claude-sonnet-4`) with:
- Side, score, pattern names, price, ATR, stop loss, target
- M5 and M15 candle data (last 20 bars)
- EMA values, RSI, VWAP, ADX

The consultant responds with:
- `trade` or `no_trade` recommendation
- Confidence level (0.0-1.0)
- Brief reasoning

**Approval requires:** view == side AND confidence ≥ 0.55 (`MIN_CONFIDENCE`).
**Veto:** Any `no_trade` recommendation blocks regardless of confidence.
**Required:** `CONSULTANT_REQUIRED=true` — consultant down = no entries (fail-closed).

### Step 5: Position Limits
- Max open gold positions: 1
- If a position is already open → skip
- Daily loss limit check
- Cooldown between trades (currently 5 bars = 25 min)
- Streak pause (disabled)

### Step 6: Execution
If all gates pass:
- Calculate stop loss: `price ± STOP_ATR × ATR` (currently 1.5× ATR)
- Calculate take profit: `price ± TARGET_ATR × ATR` (currently 2.5× ATR)
- Size lots: fixed 0.01 (or risk-based with `POSITION_SIZING`)
- Submit market order to MT5
- Retry up to 3 times with fresh price on failure

---

## 3. Active Trade Management (ATM)

Once a position is open, the bot manages it every scan cycle:

### Priority 1: Breakeven
If unrealized P/L ≥ +0.7R (where R = entry-to-stop distance):
- Move stop loss to entry price + small buffer
- Prevents winning trades from turning into losers

### Priority 2: Trailing Stop
If unrealized P/L ≥ +1.0R:
- Trail stop at `TRAIL_ATR × ATR` behind current price (currently 1.0× ATR)
- Locks in profits as price moves favorably

### Priority 3: Time-based Exit
If holding > `MAX_HOLD` bars (currently 12 bars = 60 min):
- Close position regardless of P/L
- Prevents stale positions from becoming losers

### Priority 4: Momentum Exit
If RSI crosses back through 50 against the position direction:
- Close to protect gains

### Priority 5: Opposite Signal
If a strong signal in the opposite direction appears (score ≥ 0.7):
- Close current position and potentially reverse

### Priority 6: Stop/Target Hit
Standard SL/TP execution by MT5.

---

## 4. Risk Management

| Parameter | Value | Description |
|---|---|---|
| FIXED_LOTS | 0.01 | Fixed position size |
| STOP_ATR | 1.5 | Stop loss = 1.5 × ATR |
| TARGET_ATR | 2.5 | Take profit = 2.5 × ATR |
| Breakeven R | 0.7 | Move SL to entry at +0.7R |
| Trailing R | 1.0 | Start trailing at +1.0R |
| Max Hold | 12 bars | Force close after 60 min |
| Max Positions | 1 | One gold trade at a time |
| Cooldown | 5 bars | 25 min between trades |
| Daily Loss Limit | -2% | Halt for day if hit |
| Risk Per Trade | 1% | Account risk per trade |

**R-multiple calculation:** R = |entry - stop|. All management levels are expressed as multiples of R.

---

## 5. Equity Reconciler

Compares the engine's internal book equity against MT5's real account equity every cycle.
- Baseline offset is absorbed on startup (allows for pre-existing drift)
- Gap > 10% of book equity sustained for 3+ consecutive cycles → PAUSE all new entries
- Prevents the bot from trading when the book doesn't match reality (manual closes, broker-side fills, slippage)

---

## 6. Prop Firm Gate

Tracks a virtual prop-firm challenge:
- Starting balance configurable (currently $58.82 = live account)
- Max drawdown limit (typically 10%)
- Daily loss limit (typically 5%)
- If either is breached → status = DRAWDOWN_HIT, all entries blocked
- Currently DISABLED (`PROP_ENABLED=false`)

---

## 7. Autopilot System

Supervised loop that runs the engine for 16-hour sessions:
- Restarts engine if it crashes (max 3 crashes before abort)
- Probes MT5 broker connectivity every cycle
- Studies closed trades and applies conservative tuning to `.env`
- **Frozen keys** (41): strategy-defining params (patterns, gates, sizing, stops) — CANNOT be changed mid-run
- **Tunable keys** (11): operational params (enable/disable, consultant infra) — CAN be adjusted
- All changes logged to `config_audit.jsonl`
- Human-readable ledger at `autopilot/study.md`

---

## 8. Data Flow

```
MT5 Terminal (M5 candles)
    ↓
get_mt5_bars() → Raw OHLCV data
    ↓
extract_market_state() → State snapshot (location, vwap, expansion, htf)
    ↓
_is_chop() → Chop filter (EMA gap, ATR ratio, ADX)
    ↓
Pattern detector → Score (-1.0 to +1.0)
    ↓
Trend filter → M30 alignment check
    ↓
Consultant (LLM) → Trade/no-trade verdict
    ↓
Position limits → Cooldown, daily loss, max positions
    ↓
Execution → MT5 market order
    ↓
ATM loop → Breakeven, trailing, time exit
    ↓
Equity reconciler → Book vs broker check
```

---

## 9. Key Files

| File | Purpose |
|---|---|
| `money_engine/gold.py` | Main scanner, pattern detection, entry logic, ATM |
| `money_engine/consultant.py` | LLM consultant integration |
| `money_engine/execution.py` | MT5 broker interface, order execution |
| `money_engine/equity_reconciler.py` | Book vs broker divergence monitor |
| `money_engine/active_trade_manager.py` | Breakeven, trailing, exits |
| `money_engine/market_state.py` | State telemetry extractor |
| `money_engine/autopilot.py` | Supervised run loop, tuning |
| `money_engine/config.py` | All configurable parameters |
| `.env` | Runtime configuration values |

---

## 10. Current Issues to Discuss

### A. Chop Filter Sensitivity
71% of scans blocked. The filter uses three conditions (EMA gap, ATR ratio, ADX) that all must pass. In compression markets, almost nothing gets through. Is this too aggressive? Should we relax one condition?

### B. Consultant Over-Vetting
Before MIN_CONFIDENCE was raised to 0.55, the consultant vetoed 16 trades that scored ≥0.5 (including +1.0 scores). The LLM sees "bearish EMA stack" and vetoes everything, even clean bull setups. The consultant is the #1 reason entries don't open when the pattern engine finds setups.

### C. Breakeven vs Trailing Stop Interaction
Breakeven activates at +0.7R, trailing starts at +1.0R. If breakeven moves SL to entry, the trailing stop needs to recalculate R from the new SL. Need to verify this doesn't create issues.

### D. Retcode 10030 (Invalid Price)
5 trades rejected by MT5 with stale price errors. The engine retries 3 times with fresh ticks but all failed during a fast market. Is there a better way to handle price staleness?

### E. Market Regime Detection
Currently only "chop" vs "trending". No volatility regime classification (low vol, normal, high vol, news). ATR ratio gives some info but isn't granular enough.

### F. Pattern Reliability
Some patterns (morning_star, bull_pin_bar) are used as both primary signals and boosters. Are all patterns equally reliable on XAUUSD M5? Should some be weighted differently?

### G. Position Sizing
Currently fixed 0.01 lots. Risk-based sizing exists but is disabled. With $55 balance, what's appropriate?

### H. Session Awareness
Bot runs 24/5. No session filters (London, NY overlap, Asian). Gold behaves differently across sessions. Should we add time-of-day awareness?

---

## 11. What the Consultant Needs

To help optimize, we need:
1. **Which patterns are actually profitable on XAUUSD M5?** (We have ~2300 signals logged)
2. **Are the ATR multipliers (1.5 stop, 2.5 target) appropriate?** (R:R = 1:1.67)
3. **Should the chop filter be softer?** (71% block rate seems extreme)
4. **Is the consultant adding value or just blocking?** (16 vetoes today, 0 approvals of vetoes)
5. **Session filters — which hours are best for gold?**
6. **Should we trade M5 only or add M15/M30 confirmation?**
7. **What's a realistic daily target for 0.01 lots on $55?**


