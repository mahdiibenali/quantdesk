"""XAUUSD Gold Scalping Strategy — rebuilt from the original winning config.

Pattern-gated scoring: indicators ONLY boost confirmed patterns, never fire alone.
This is the core edge that produced PF 1.51, +0.31R/trade on M5.
"""

import math
import numpy as np
from datetime import datetime, timezone, timedelta
from bot.strategies.base import Strategy, Signal, Side, OpenPosition, ArmedState
from bot.strategies.signals import SignalAggregator


# ------------------------------------------------------------------
# Pattern detection — ported from original quant.py detect_patterns()
# ------------------------------------------------------------------

def _prior_trend(df):
    if len(df) < 4:
        return 0
    b1, b2 = df.iloc[-3], df.iloc[-2]
    if b2["close"] > b1["close"] and df.iloc[-1]["close"] > b2["close"]:
        return 1
    if b2["close"] < b1["close"] and df.iloc[-1]["close"] < b2["close"]:
        return -1
    return 0


def detect_patterns(opens, highs, lows, closes, volumes=None, lookback=20):
    """25-pattern detection — ported from original quant.py."""
    n = len(closes)
    if n < lookback + 3:
        return []

    pats = []
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    po, ph, pl, pc = opens[-2], highs[-2], lows[-2], closes[-2]

    body = c - o
    p_body = pc - po
    rng = h - l if h > l else abs(c) * 0.0005
    p_rng = ph - pl if ph > pl else rng
    body_abs = abs(body)
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)

    # 1) Engulfing
    if body > 0 and p_body < 0 and o <= pc and c >= po:
        pats.append({"name": "bull_engulfing", "side": 1, "weight": 0.50})
    elif body < 0 and p_body > 0 and o >= pc and c <= po:
        pats.append({"name": "bear_engulfing", "side": -1, "weight": 0.50})

    # 2) Pin bars
    if body_abs > 0:
        if body > 0 and lower_wick >= 2.0 * body_abs and upper_wick <= lower_wick:
            pats.append({"name": "bull_pin_bar", "side": 1, "weight": 0.45})
        elif body < 0 and upper_wick >= 2.0 * body_abs and lower_wick <= upper_wick:
            pats.append({"name": "bear_pin_bar", "side": -1, "weight": 0.45})

    # 2b) Classic single-candle
    small_body = body_abs <= 0.4 * rng
    if small_body and lower_wick >= 2.0 * body_abs and upper_wick <= 0.35 * lower_wick:
        pats.append({"name": "bull_hammer", "side": 1, "weight": 0.42})
    if small_body and upper_wick >= 2.0 * body_abs and lower_wick <= 0.35 * upper_wick:
        pats.append({"name": "bear_shooting_star", "side": -1, "weight": 0.42})

    # Doji family
    if body_abs <= 0.10 * rng:
        if lower_wick >= 0.6 * rng and upper_wick <= 0.3 * lower_wick:
            pats.append({"name": "dragonfly_doji", "side": 1, "weight": 0.35})
        elif upper_wick >= 0.6 * rng and lower_wick <= 0.3 * upper_wick:
            pats.append({"name": "gravestone_doji", "side": -1, "weight": 0.35})

    # 2c) Two-candle classic
    if body > 0 and p_body < 0 and o < pc and po > c > 0.5 * (po + pc):
        pats.append({"name": "piercing_line", "side": 1, "weight": 0.45})
    elif body < 0 and p_body > 0 and o > pc and po < c < 0.5 * (po + pc):
        pats.append({"name": "dark_cloud_cover", "side": -1, "weight": 0.45})

    if p_body > 0 and body < 0 and po < c < o < pc and body_abs <= 0.5 * p_body:
        pats.append({"name": "bear_harami", "side": -1, "weight": 0.30})
    elif p_body < 0 and body > 0 and pc < o < c < po and body_abs <= 0.5 * -p_body:
        pats.append({"name": "bull_harami", "side": 1, "weight": 0.30})

    if body < 0 and p_body > 0 and h >= ph and h - ph <= 0.15 * rng:
        pats.append({"name": "tweezer_top", "side": -1, "weight": 0.38})
    elif body > 0 and p_body < 0 and l <= pl and pl - l <= 0.15 * rng:
        pats.append({"name": "tweezer_bottom", "side": 1, "weight": 0.38})

    # 2d) Three-candle
    if n >= 3:
        o3, c3 = opens[-3], closes[-3]
        body3 = c3 - o3
        star_tiny = abs(p_body) <= 0.6 * max(body_abs, abs(body3))
        if body3 < 0 and body > 0 and star_tiny and o3 > c > 0.5 * (o3 + c3):
            pats.append({"name": "morning_star", "side": 1, "weight": 0.55})
        elif body3 > 0 and body < 0 and star_tiny and o3 < c < 0.5 * (o3 + c3):
            pats.append({"name": "evening_star", "side": -1, "weight": 0.55})

    # 2e) Marubozu
    if body > 0 and body_abs >= 0.7 * rng and (upper_wick + lower_wick) <= 0.15 * rng and body >= 1.5 * (p_rng + 1e-9):
        pats.append({"name": "bull_marubozu", "side": 1, "weight": 0.40})
    elif body < 0 and body_abs >= 0.7 * rng and (upper_wick + lower_wick) <= 0.15 * rng and -body >= 1.5 * (p_rng + 1e-9):
        pats.append({"name": "bear_marubozu", "side": -1, "weight": 0.40})

    # 3) Inside bar breakout
    if n >= 3:
        ib_h, ib_l = highs[-3], lows[-3]
        if ph < ib_h and pl > ib_l:
            if c > ph:
                pats.append({"name": "inside_bar_breakout", "side": 1, "weight": 0.50})
            elif c < pl:
                pats.append({"name": "inside_bar_breakdown", "side": -1, "weight": 0.50})

    # 4) Range expansion
    if volumes and len(volumes) >= lookback:
        avg_vol = sum(volumes[-lookback:-1]) / max(1, lookback - 1)
        vol = volumes[-1]
        surge = avg_vol > 0 and vol > 1.5 * avg_vol
    else:
        surge = False
    near_high = c >= h - 0.3 * rng
    near_low = c <= l + 0.3 * rng
    if body > 0 and body >= 1.6 * max(p_body, 1e-9) and (surge or body >= 2.5 * (p_rng + 1e-9)) and near_high:
        pats.append({"name": "bull_range_expansion", "side": 1, "weight": 0.40})
    elif body < 0 and -body >= 1.6 * max(-p_body, 1e-9) and (surge or -body >= 2.5 * (p_rng + 1e-9)) and near_low:
        pats.append({"name": "bear_range_expansion", "side": -1, "weight": 0.40})

    # 5) Range breakout/breakdown
    if n >= lookback + 1:
        rng_high = max(highs[-lookback-1:-1])
        rng_low = min(lows[-lookback-1:-1])
        if c > rng_high:
            pats.append({"name": "range_breakout", "side": 1, "weight": 0.32})
        elif c < rng_low:
            pats.append({"name": "range_breakdown", "side": -1, "weight": 0.32})

    # 6) Pullback continuation (needs EMA9/21 — computed externally)
    # Skipped here, computed in evaluate_scalp with EMA data

    # 7) 3-bar trend structure
    if n >= 4:
        if highs[-1] > highs[-2] > highs[-3] and lows[-1] > lows[-2] > lows[-3]:
            pats.append({"name": "higher_highs", "side": 1, "weight": 0.25})
        elif highs[-1] < highs[-2] < highs[-3] and lows[-1] < lows[-2] < lows[-3]:
            pats.append({"name": "lower_lows", "side": -1, "weight": 0.25})

    return pats


# ------------------------------------------------------------------
# Pattern-gated scoring — the core edge
# ------------------------------------------------------------------

def evaluate_scalp(opens, highs, lows, closes, volumes=None,
                   min_score=0.50, lookback=20):
    """Pattern-gated scoring: patterns first, indicators only boost.
    
    Returns (side, score, reasons) or (None, 0, []) if no trade.
    """
    n = len(closes)
    if n < lookback + 10:
        return None, 0, []

    c = closes[-1]

    # Dead tape filter
    if n >= lookback + 1:
        rng_high = max(highs[-lookback-1:-1])
        rng_low = min(lows[-lookback-1:-1])
        range_pct = (rng_high - rng_low) / c * 100.0 if c > 0 else 0.0
        if range_pct < 0.05:
            return None, 0, ["dead_tape"]

    # Regime filter: ADX compression skip (simplified — use ATR contraction)
    if n >= 20:
        trs = []
        for i in range(max(1, n-20), n):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        atr_now = sum(trs[-3:]) / 3
        atr_sma = sum(trs) / len(trs)
        if atr_now < atr_sma * 0.8:
            return None, 0, ["regime_skip_compression"]

    # Pattern detection
    raw = detect_patterns(opens, highs, lows, closes, volumes, lookback)
    if not raw:
        return None, 0, ["no_pattern"]

    bull = sum(p["weight"] for p in raw if p["side"] > 0)
    bear = sum(p["weight"] for p in raw if p["side"] < 0)
    if bull <= 0 and bear <= 0:
        return None, 0, ["no_pattern"]

    side = 1 if bull >= bear else -1
    score = bull if side == 1 else bear
    reasons = [p["name"] for p in raw if p["side"] == side]

    # Conflicting patterns check
    raw_opp = sum(p["weight"] for p in raw if p["side"] == -side)
    if raw_opp >= 0.5 * score:
        return None, 0, ["conflicting_patterns"]

    # --- Indicator boosts (only AFTER pattern confirmed) ---

    # EMA alignment
    closes_arr = np.array(closes, dtype=float)
    ema9 = _ema(closes_arr, 9)[-1]
    ema21 = _ema(closes_arr, 21)[-1]
    ema50 = _ema(closes_arr, 50)[-1] if n >= 50 else None

    if side == 1 and c > ema9 > ema21:
        score += 0.10
        reasons.append("bull_alignment")
    elif side == -1 and c < ema9 < ema21:
        score += 0.10
        reasons.append("bear_alignment")

    if ema50 is not None:
        if side == 1 and c > ema50 and ema9 > ema50:
            score += 0.10
            reasons.append("above_ema50")
        elif side == -1 and c < ema50 and ema9 < ema50:
            score += 0.10
            reasons.append("below_ema50")

    # RSI
    rsi = _rsi(closes_arr, 14)
    rsi_v = rsi[-1] if not np.isnan(rsi[-1]) else 50.0
    if n >= 4:
        rsi_prev = rsi[-3] if not np.isnan(rsi[-3]) else 50.0
        if side == 1 and rsi_v > rsi_prev and rsi_v < 55 and rsi_prev < 45:
            score += 0.20
            reasons.append("rsi_turning_up")
        elif side == -1 and rsi_v < rsi_prev and rsi_v > 45 and rsi_prev > 55:
            score += 0.20
            reasons.append("rsi_turning_down")
        elif (side == 1 and rsi_v > 80) or (side == -1 and rsi_v < 20):
            score *= 0.6
            reasons.append("rsi_extreme_scaled")
        elif 45 <= rsi_v <= 65:
            score += 0.05
            reasons.append("rsi_sweet")

    # Last bar impulse
    if n >= 3:
        prev_close = closes[-2]
        if (side == 1 and c > prev_close) or (side == -1 and c < prev_close):
            score += 0.05
            reasons.append("last_bar_impulse")

    # ATR expansion
    if n >= 20:
        if atr_now > atr_sma * 1.1:
            score += 0.10
            reasons.append("atr_expansion")
        elif atr_now < atr_sma * 0.8:
            score *= 0.6
            reasons.append("atr_contraction")

    final_score = round(max(-1.0, min(1.0, side * score)), 3)
    if abs(final_score) < min_score:
        return None, 0, reasons

    return (Side.BUY if final_score > 0 else Side.SELL), final_score, reasons


# ------------------------------------------------------------------
# Helper indicators
# ------------------------------------------------------------------

def _ema(data, period):
    alpha = 2 / (period + 1)
    ema = np.empty_like(data)
    ema[0] = data[0]
    for i in range(1, len(data)):
        ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
    return ema


def _rsi(data, period):
    deltas = np.diff(data)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.empty(len(data))
    avg_loss = np.empty(len(data))
    avg_gain[:] = np.nan
    avg_loss[:] = np.nan
    avg_gain[period] = np.mean(gains[:period])
    avg_loss[period] = np.mean(losses[:period])
    for i in range(period + 1, len(data)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    rsi = 100 - 100 / (1 + rs)
    rsi[:period] = np.nan
    return rsi


def _atr(highs, lows, closes, period=14):
    n = len(closes)
    if n < period + 1:
        return 0.0
    trs = []
    for i in range(1, n):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    if len(trs) < period:
        return 0.0
    return float(np.mean(trs[-period:]))


# ------------------------------------------------------------------
# Main strategy
# ------------------------------------------------------------------

class XAUUSDScalp(Strategy):
    """Gold scalper rebuilt from the original winning config.

    Pattern-gated scoring with regime/chop/session/trend filters.
    """

    name = "xauusd_scalp"

    def __init__(
        self,
        symbol: str = "XAUUSD",
        ema_fast: int = 9,
        ema_slow: int = 21,
        rsi_period: int = 14,
        atr_period: int = 14,
        stop_atr_mult: float = 1.2,
        target_atr_mult: float = 1.8,
        min_stop_atr: float = 0.8,
        min_score: float = 0.50,
        min_bars: int = 30,
        timeframe: int = 5,
        # Session gate
        session_start_hour: int = 7,
        session_end_hour: int = 22,
        # Risk
        risk_per_trade_pct: float = 0.004,
        fixed_lots: float = 0.01,
        max_open: int = 5,
        max_hold_minutes: int = 240,
        # Cooldown
        cooldown_minutes: int = 60,
        # Filters
        use_chop_filter: bool = True,
        use_trend_filter: bool = True,
    ):
        super().__init__(name=f"scalp_{symbol.lower()}")
        self.symbol = symbol
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.target_atr_mult = target_atr_mult
        self.min_stop_atr = min_stop_atr
        self.min_score = min_score
        self.min_bars = min_bars
        self.timeframe = timeframe
        self.session_start_hour = session_start_hour
        self.session_end_hour = session_end_hour
        self.risk_per_trade_pct = risk_per_trade_pct
        self.fixed_lots = fixed_lots
        self.max_open = max_open
        self.max_hold_minutes = max_hold_minutes
        self.cooldown_minutes = cooldown_minutes
        self.use_chop_filter = use_chop_filter
        self.use_trend_filter = use_trend_filter

        # State
        self._cooldown_until = None
        self._position_opened_at = None
        self._peak_pnl_r = 0.0
        self._armed = ArmedState()
        self._pnl_history = {}  # ticket -> [(time, pnl)]

    def on_idle(self, data: dict, equity: float) -> Signal | None:
        """Phase 1: Scan for entry with full gate cascade."""
        now = datetime.now(timezone.utc)

        # Session gate
        hour = now.hour
        if not (self.session_start_hour <= hour < self.session_end_hour):
            return None

        # Cooldown check
        if self._cooldown_until and now < self._cooldown_until:
            return None

        closes = data.get("closes")
        highs = data.get("highs")
        lows = data.get("lows")
        opens = data.get("opens")
        volumes = data.get("volumes")

        if closes is None or len(closes) < self.min_bars:
            return None

        # Pattern-gated scoring
        side, score, reasons = evaluate_scalp(
            opens, highs, lows, closes, volumes,
            min_score=self.min_score,
        )

        if side is None:
            return None

        # Chop filter
        if self.use_chop_filter:
            closes_arr = np.array(closes, dtype=float)
            ema_fast = _ema(closes_arr, self.ema_fast)
            ema_slow = _ema(closes_arr, self.ema_slow)
            # Choppy: EMAs tangled (crossing frequently)
            if len(ema_fast) > 5:
                crossings = 0
                for i in range(-5, 0):
                    if ema_fast[i] > ema_slow[i] and ema_fast[i-1] <= ema_slow[i-1]:
                        crossings += 1
                    elif ema_fast[i] < ema_slow[i] and ema_fast[i-1] >= ema_slow[i-1]:
                        crossings += 1
                if crossings >= 3:
                    return None

        # Trend filter
        if self.use_trend_filter:
            closes_arr = np.array(closes, dtype=float)
            ema50 = _ema(closes_arr, 50)[-1] if len(closes) >= 50 else None
            if ema50 is not None:
                c = closes[-1]
                if side == Side.BUY and c < ema50:
                    return None
                if side == Side.SELL and c > ema50:
                    return None

        # Compute ATR for stop/target
        highs_arr = np.array(highs, dtype=float)
        lows_arr = np.array(lows, dtype=float)
        closes_arr = np.array(closes, dtype=float)
        atr = _atr(highs_arr, lows_arr, closes_arr, self.atr_period)
        if atr <= 0:
            return None

        price = closes[-1]
        stop_mult = max(self.stop_atr_mult, self.min_stop_atr)
        stop_pts = stop_mult * atr
        target_pts = self.target_atr_mult * atr

        return Signal(
            side=side,
            confidence=min(0.92, 0.42 + abs(score) * 0.5),
            stop_distance_pts=stop_pts,
            target_distance_pts=target_pts,
            score=abs(score),
            reasons=reasons[:5],
            strategy_name=self.name,
        )

    def on_open(self, position: OpenPosition, data: dict, equity: float) -> Signal | None:
        """Phase 2: Follow every trade. Move SL to lock profit."""
        closes = data.get("closes")
        if closes is None or len(closes) < self.min_bars:
            return None

        c = closes[-1]
        pip = 0.01  # XAUUSD pip
        min_sl_distance = 0.50  # minimum 0.50 points from entry for XAUUSD

        if position.side == Side.BUY:
            risk = position.entry_price - position.stop_price
            if risk <= 0:
                return None
            profit = c - position.entry_price

            if profit > 0:
                new_sl = position.entry_price + profit * 0.5
                if new_sl >= position.entry_price + min_sl_distance and new_sl > position.stop_price:
                    return Signal(
                        side=Side.HOLD, confidence=1.0,
                        stop_distance_pts=0, target_distance_pts=0,
                        modify_sl=new_sl, modify_tp=position.target_price,
                        reasons=["lock_profit"],
                        strategy_name=self.name,
                    )

        elif position.side == Side.SELL:
            risk = position.stop_price - position.entry_price
            if risk <= 0:
                return None
            profit = position.entry_price - c

            if profit > 0:
                new_sl = position.entry_price - profit * 0.5
                if new_sl <= position.entry_price - min_sl_distance and new_sl < position.stop_price:
                    return Signal(
                        side=Side.HOLD, confidence=1.0,
                        stop_distance_pts=0, target_distance_pts=0,
                        modify_sl=new_sl, modify_tp=position.target_price,
                        reasons=["lock_profit"],
                        strategy_name=self.name,
                    )

        return None

    def on_busy(self, data: dict, equity: float) -> Signal | None:
        return None

    def arm(self, signal: Signal) -> None:
        self._armed = ArmedState(
            is_armed=True,
            armed_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            signal=signal,
        )

    def disarm(self) -> None:
        self._armed = ArmedState()

    def check_arm(self) -> Signal | None:
        if not self._armed.is_armed:
            return None
        if self._armed.expires_at and datetime.now(timezone.utc) > self._armed.expires_at:
            self.disarm()
            return None
        return self._armed.signal
