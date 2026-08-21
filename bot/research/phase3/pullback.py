"""Family 2: Pullback — trends retrace to defined locations before continuing.

Hypothesis: In a trending market, price pulls back to value (moving averages,
structure levels) before resuming. Entering at the pullback location offers
better R:R than chasing the trend.

Logic:
- Detect established trend (EMA structure, higher highs/lows)
- Measure retracement depth (Fibonacci-like levels)
- Enter on rejection/continuation trigger at pullback location
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from ..data_pipeline import SymbolData
from ..execution_model import BacktestState


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1)
    result = np.empty_like(arr, dtype=float)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
    return result


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    trs = np.empty(len(closes), dtype=float)
    trs[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        trs[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = np.empty_like(trs)
    if len(trs) >= period:
        atr[period - 1] = np.mean(trs[:period])
        for i in range(period, len(trs)):
            atr[i] = (atr[i - 1] * (period - 1) + trs[i]) / period
    else:
        atr[:] = np.nan
    return atr


def _find_swing_points(closes: np.ndarray, lookback: int = 5) -> tuple[list, list]:
    """Find recent swing highs and lows."""
    highs = []
    lows = []
    for i in range(lookback, len(closes) - lookback):
        if all(closes[i] >= closes[i - j] for j in range(1, lookback + 1)) and \
           all(closes[i] >= closes[i + j] for j in range(1, lookback + 1)):
            highs.append((i, closes[i]))
        if all(closes[i] <= closes[i - j] for j in range(1, lookback + 1)) and \
           all(closes[i] <= closes[i + j] for j in range(1, lookback + 1)):
            lows.append((i, closes[i]))
    return highs, lows


def make_pullback(
    symbol: str,
    ema_fast: int = 8,
    ema_slow: int = 21,
    ema_value: int = 50,
    atr_period: int = 14,
    pullback_min_pct: float = 0.3,
    pullback_max_pct: float = 0.7,
    rejection_candle_ratio: float = 0.5,
    stop_atr_mult: float = 1.5,
    target_atr_mult: float = 2.5,
    rsi_period: int = 14,
    min_bars: int = 60,
    min_score: float = 0.15,
    lots: float = 0.01,
    **kwargs,
):
    """Create pullback strategy adapter.

    Entry logic:
    1. Established trend (EMA structure aligned, price making HH/HL or LH/LL)
    2. Pullback to value area (near EMA_value or recent swing)
    3. Rejection candle (pin bar, engulfing, or RSI divergence at pullback)
    """
    pip = 0.001 if "JPY" in symbol else 0.0001
    min_stop_pips = 15 if "JPY" in symbol else 10
    min_target_pips = 25 if "JPY" in symbol else 15

    def strategy_fn(data: SymbolData, state: BacktestState) -> Optional[dict]:
        if len(data.bars) < min_bars:
            return None

        closes = np.array(data.closes, dtype=float)
        highs = np.array(data.highs, dtype=float)
        lows = np.array(data.lows, dtype=float)
        opens = np.array(data.opens, dtype=float)

        fast = _ema(closes, ema_fast)
        slow = _ema(closes, ema_slow)
        value = _ema(closes, ema_value)
        atr = _atr(highs, lows, closes, atr_period)

        if any(np.isnan(x) for x in [fast[-1], slow[-1], value[-1], atr[-1]]):
            return None
        if atr[-1] <= 0:
            return None

        cur_price = closes[-1]
        cur_fast = fast[-1]
        cur_slow = slow[-1]
        cur_value = value[-1]

        # 1. Established trend
        uptrend = cur_fast > cur_slow and cur_price > cur_value
        downtrend = cur_fast < cur_slow and cur_price < cur_value
        if not (uptrend or downtrend):
            return None

        # 2. Pullback depth: how far has price retraced from recent extreme?
        lookback = min(50, len(closes) - 1)
        if uptrend:
            recent_high = np.max(closes[-lookback:])
            recent_low = np.min(closes[-lookback:])
            range_size = recent_high - recent_low
            if range_size <= 0:
                return None
            pullback_depth = (recent_high - cur_price) / range_size
        else:
            recent_high = np.max(closes[-lookback:])
            recent_low = np.min(closes[-lookback:])
            range_size = recent_high - recent_low
            if range_size <= 0:
                return None
            pullback_depth = (cur_price - recent_low) / range_size

        # Pullback must be in the sweet spot
        if pullback_depth < pullback_min_pct or pullback_depth > pullback_max_pct:
            return None

        # 3. Rejection candle at pullback location
        body = abs(closes[-1] - opens[-1])
        upper_wick = highs[-1] - max(opens[-1], closes[-1])
        lower_wick = min(opens[-1], closes[-1]) - lows[-1]
        bar_range = highs[-1] - lows[-1]

        if bar_range <= 0:
            return None

        score = 0.0
        reasons = []

        if uptrend:
            # Bullish rejection: lower wick dominant
            if lower_wick / bar_range > rejection_candle_ratio:
                score += 0.4
                reasons.append("bullish_rejection_candle")
            # Price near value area
            dist_to_value = abs(cur_price - cur_value) / atr[-1]
            if dist_to_value < 1.0:
                score += 0.3
                reasons.append("near_value_area")
            # RSI not overbought
            rsi = _rsi(closes, rsi_period)
            if not np.isnan(rsi[-1]) and rsi[-1] < 65:
                score += 0.15
                reasons.append("rsi_not_overbought")
            # Higher low forming
            if len(closes) > 10:
                recent_lows_idx = np.argmin(lows[-10:])
                if closes[-1] > lows[-10 + recent_lows_idx]:
                    score += 0.15
                    reasons.append("higher_low")
        else:
            # Bearish rejection: upper wick dominant
            if upper_wick / bar_range > rejection_candle_ratio:
                score += 0.4
                reasons.append("bearish_rejection_candle")
            dist_to_value = abs(cur_price - cur_value) / atr[-1]
            if dist_to_value < 1.0:
                score += 0.3
                reasons.append("near_value_area")
            rsi = _rsi(closes, rsi_period)
            if not np.isnan(rsi[-1]) and rsi[-1] > 35:
                score += 0.15
                reasons.append("rsi_not_oversold")
            if len(closes) > 10:
                recent_highs_idx = np.argmax(highs[-10:])
                if closes[-1] < highs[-10 + recent_highs_idx]:
                    score += 0.15
                    reasons.append("lower_high")

        if score < min_score:
            return None

        stop_pts = max(atr[-1] * stop_atr_mult, min_stop_pips * pip)
        target_pts = max(atr[-1] * target_atr_mult, min_target_pips * pip)

        side = "BUY" if uptrend else "SELL"
        return {
            "side": side,
            "stop_distance_pts": stop_pts,
            "target_distance_pts": target_pts,
            "score": score,
            "confidence": min(0.9, 0.4 + score * 0.4),
            "reasons": reasons,
            "lots": lots,
            "atr": atr[-1],
        }

    return strategy_fn


def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    rsi = np.full(len(closes), np.nan)
    if len(gains) < period:
        return rsi
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss > 0:
            rsi[i + 1] = 100 - 100 / (1 + avg_gain / avg_loss)
        else:
            rsi[i + 1] = 100.0
    return rsi
