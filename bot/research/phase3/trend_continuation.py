"""Family 1: Trend Continuation — sustained directional moves continue.

Hypothesis: Markets in strong trends tend to continue. Entry after
consolidation/pullback within a trend captures the next leg.

Logic:
- Detect sustained trend via EMA slope and ADX
- Wait for consolidation (tight range, low ATR)
- Enter on momentum resumption in trend direction
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from ..data_pipeline import SymbolData
from ..execution_model import BacktestState


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average."""
    alpha = 2.0 / (period + 1)
    result = np.empty_like(arr, dtype=float)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
    return result


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range."""
    trs = np.empty(len(closes), dtype=float)
    trs[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs[i] = tr
    # Smoothed ATR
    atr = np.empty_like(trs)
    atr[:period] = np.nan
    if len(trs) >= period:
        atr[period - 1] = np.mean(trs[:period])
        for i in range(period, len(trs)):
            atr[i] = (atr[i - 1] * (period - 1) + trs[i]) / period
    return atr


def _adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Average Directional Index — measures trend strength."""
    n = len(closes)
    if n < period + 1:
        return np.full(n, np.nan)

    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)

    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > down and up > 0) else 0
        minus_dm[i] = down if (down > up and down > 0) else 0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))

    # Smoothed
    smoothed_tr = np.zeros(n)
    smoothed_plus = np.zeros(n)
    smoothed_minus = np.zeros(n)
    smoothed_tr[period] = np.sum(tr[1:period + 1])
    smoothed_plus[period] = np.sum(plus_dm[1:period + 1])
    smoothed_minus[period] = np.sum(minus_dm[1:period + 1])

    for i in range(period + 1, n):
        smoothed_tr[i] = smoothed_tr[i - 1] - smoothed_tr[i - 1] / period + tr[i]
        smoothed_plus[i] = smoothed_plus[i - 1] - smoothed_plus[i - 1] / period + plus_dm[i]
        smoothed_minus[i] = smoothed_minus[i - 1] - smoothed_minus[i - 1] / period + minus_dm[i]

    plus_di = np.where(smoothed_tr > 0, 100 * smoothed_plus / smoothed_tr, 0)
    minus_di = np.where(smoothed_tr > 0, 100 * smoothed_minus / smoothed_tr, 0)
    di_sum = plus_di + minus_di
    dx = np.where(di_sum > 0, 100 * np.abs(plus_di - minus_di) / di_sum, 0)

    adx = np.full(n, np.nan)
    start = 2 * period
    if start < n:
        adx[start] = np.mean(dx[period:start + 1])
        for i in range(start + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return adx


def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index."""
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
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100 - 100 / (1 + rs)
        else:
            rsi[i + 1] = 100.0

    return rsi


def make_trend_continuation(
    symbol: str,
    ema_fast: int = 8,
    ema_slow: int = 21,
    ema_trend: int = 50,
    adx_period: int = 14,
    adx_threshold: float = 20.0,
    atr_period: int = 14,
    consolidation_atr_ratio: float = 0.7,
    stop_atr_mult: float = 1.5,
    target_atr_mult: float = 2.5,
    rsi_period: int = 14,
    min_bars: int = 60,
    min_score: float = 0.15,
    lots: float = 0.01,
    **kwargs,
):
    """Create trend continuation strategy adapter.

    Entry logic:
    1. Trend in place (ADX > threshold, price above/below EMA_trend)
    2. Consolidation detected (ATR shrinking, price near EMAs)
    3. Momentum resumption (RSI moving in trend direction, EMA cross)
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

        fast = _ema(closes, ema_fast)
        slow = _ema(closes, ema_slow)
        trend = _ema(closes, ema_trend)
        atr = _atr(highs, lows, closes, atr_period)
        adx = _adx(highs, lows, closes, adx_period)
        rsi = _rsi(closes, rsi_period)

        if any(np.isnan(x) for x in [fast[-1], slow[-1], trend[-1], atr[-1], adx[-1], rsi[-1]]):
            return None
        if atr[-1] <= 0:
            return None

        cur_price = closes[-1]
        cur_fast = fast[-1]
        cur_slow = slow[-1]
        cur_trend = trend[-1]
        cur_adx = adx[-1]
        cur_rsi = rsi[-1]
        prev_fast = fast[-2]
        prev_slow = slow[-2]

        # 1. Trend in place
        in_uptrend = cur_price > cur_trend and cur_adx > adx_threshold
        in_downtrend = cur_price < cur_trend and cur_adx > adx_threshold
        if not (in_uptrend or in_downtrend):
            return None

        # 2. Consolidation: ATR below recent average
        atr_recent = np.mean(atr[-20:]) if len(atr) >= 20 else atr[-1]
        atr_prev = np.mean(atr[-40:-20]) if len(atr) >= 40 else atr_recent
        is_consolidating = atr[-1] < atr_recent * consolidation_atr_ratio

        # 3. Momentum resumption: EMA cross or RSI moving in trend direction
        cross_up = prev_fast <= prev_slow and cur_fast > cur_slow
        cross_down = prev_fast >= prev_slow and cur_fast < cur_slow

        score = 0.0
        reasons = []

        if in_uptrend:
            if is_consolidating:
                score += 0.3
                reasons.append("consolidation_in_uptrend")
            if cross_up:
                score += 0.5
                reasons.append("ema_cross_up")
            elif cur_fast > cur_slow:
                score += 0.2
                reasons.append("ema_aligned_up")
            if 40 < cur_rsi < 60:
                score += 0.15
                reasons.append("rsi_neutral_in_uptrend")
            elif cur_rsi > 60:
                score += 0.1
                reasons.append("rsi_bullish")
            if cur_adx > 25:
                score += 0.15
                reasons.append("strong_trend")
        else:  # downtrend
            if is_consolidating:
                score += 0.3
                reasons.append("consolidation_in_downtrend")
            if cross_down:
                score += 0.5
                reasons.append("ema_cross_down")
            elif cur_fast < cur_slow:
                score += 0.2
                reasons.append("ema_aligned_down")
            if 40 < cur_rsi < 60:
                score += 0.15
                reasons.append("rsi_neutral_in_downtrend")
            elif cur_rsi < 40:
                score += 0.1
                reasons.append("rsi_bearish")
            if cur_adx > 25:
                score += 0.15
                reasons.append("strong_trend")

        if score < min_score:
            return None

        stop_pts = max(atr[-1] * stop_atr_mult, min_stop_pips * pip)
        target_pts = max(atr[-1] * target_atr_mult, min_target_pips * pip)

        side = "BUY" if in_uptrend else "SELL"
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
