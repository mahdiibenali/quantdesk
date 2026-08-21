"""Family 4: Mean Reversion — prices revert to equilibrium after extremes.

Hypothesis: In range-bound markets, extreme deviations from value tend
to revert. Fading the extreme captures the reversion.

Logic:
- Detect range-bound market (low ADX, price oscillating around mean)
- Detect extreme deviation (distance from moving average, Bollinger band)
- Enter on exhaustion signal (RSI extreme, candle pattern)
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


def _adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Average Directional Index."""
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


def _bollinger(closes: np.ndarray, period: int = 20, num_std: float = 2.0):
    n = len(closes)
    upper = np.full(n, np.nan)
    middle = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        ma = np.mean(window)
        std = np.std(window, ddof=1)
        middle[i] = ma
        upper[i] = ma + num_std * std
        lower[i] = ma - num_std * std
    return upper, middle, lower


def make_mean_reversion(
    symbol: str,
    ema_period: int = 20,
    adx_period: int = 14,
    adx_max: float = 25.0,
    bb_period: int = 20,
    bb_std: float = 2.0,
    extreme_atr_mult: float = 1.5,
    rsi_period: int = 14,
    rsi_overbought: float = 70.0,
    rsi_oversold: float = 30.0,
    stop_atr_mult: float = 1.5,
    target_atr_mult: float = 2.0,
    min_bars: int = 60,
    min_score: float = 0.15,
    lots: float = 0.01,
    **kwargs,
):
    """Create mean reversion strategy adapter.

    Entry logic:
    1. Range-bound market (ADX low, price oscillating)
    2. Extreme deviation (price far from mean, outside BB)
    3. Exhaustion signal (RSI extreme, rejection candle)
    4. Fade the extreme
    """
    pip = 0.001 if "JPY" in symbol else 0.0001
    min_stop_pips = 15 if "JPY" in symbol else 10
    min_target_pips = 20 if "JPY" in symbol else 12

    def strategy_fn(data: SymbolData, state: BacktestState) -> Optional[dict]:
        if len(data.bars) < min_bars:
            return None

        closes = np.array(data.closes, dtype=float)
        highs = np.array(data.highs, dtype=float)
        lows = np.array(data.lows, dtype=float)
        opens = np.array(data.opens, dtype=float)

        ema = _ema(closes, ema_period)
        atr = _atr(highs, lows, closes, adx_period)
        adx = _adx(highs, lows, closes, adx_period)
        bb_upper, bb_middle, bb_lower = _bollinger(closes, bb_period, bb_std)
        rsi = _rsi(closes, rsi_period)

        if any(np.isnan(x) for x in [ema[-1], atr[-1], adx[-1], bb_upper[-1], bb_lower[-1], rsi[-1]]):
            return None
        if atr[-1] <= 0:
            return None

        cur_price = closes[-1]
        cur_adx = adx[-1]
        cur_rsi = rsi[-1]
        cur_ema = ema[-1]

        # 1. Range-bound market
        if cur_adx > adx_max:
            return None

        # 2. Extreme deviation from mean
        deviation = (cur_price - cur_ema) / atr[-1]
        bb_position = (cur_price - bb_lower[-1]) / (bb_upper[-1] - bb_lower[-1]) if (bb_upper[-1] - bb_lower[-1]) > 0 else 0.5

        # Check for exhaustion candle
        body = abs(closes[-1] - opens[-1])
        upper_wick = highs[-1] - max(opens[-1], closes[-1])
        lower_wick = min(opens[-1], closes[-1]) - lows[-1]
        bar_range = highs[-1] - lows[-1]

        score = 0.0
        reasons = []

        # Oversold + extreme low
        if deviation < -extreme_atr_mult and bb_position < 0.1:
            score += 0.35
            reasons.append("extreme_low")
            if cur_rsi < rsi_oversold:
                score += 0.25
                reasons.append("rsi_oversold")
            if bar_range > 0 and lower_wick / bar_range > 0.5:
                score += 0.2
                reasons.append("bullish_rejection")
            if cur_adx < 20:
                score += 0.1
                reasons.append("weak_trend")

        # Overbought + extreme high
        elif deviation > extreme_atr_mult and bb_position > 0.9:
            score += 0.35
            reasons.append("extreme_high")
            if cur_rsi > rsi_overbought:
                score += 0.25
                reasons.append("rsi_overbought")
            if bar_range > 0 and upper_wick / bar_range > 0.5:
                score += 0.2
                reasons.append("bearish_rejection")
            if cur_adx < 20:
                score += 0.1
                reasons.append("weak_trend")
        else:
            return None

        if score < min_score:
            return None

        stop_pts = max(atr[-1] * stop_atr_mult, min_stop_pips * pip)
        target_pts = max(atr[-1] * target_atr_mult, min_target_pips * pip)

        # Fade the extreme: buy lows, sell highs
        side = "BUY" if deviation < 0 else "SELL"
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
