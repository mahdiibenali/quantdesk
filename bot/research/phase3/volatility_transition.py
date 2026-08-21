"""Family 5: Range/Volatility Transition — compression precedes expansion.

Hypothesis: Markets cycle between compression and expansion. Detecting
the onset of expansion from a compressed state captures the initial
volatility expansion move.

Logic:
- Detect compression (tight range, low ATR, low BB bandwidth)
- Detect expansion onset (ATR rising, BB widening, range breakout)
- Trade the transition: enter early in the expansion
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


def _bollinger(closes: np.ndarray, period: int = 20, num_std: float = 2.0):
    n = len(closes)
    upper = np.full(n, np.nan)
    middle = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    bandwidth = np.full(n, np.nan)
    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        ma = np.mean(window)
        std = np.std(window, ddof=1)
        middle[i] = ma
        upper[i] = ma + num_std * std
        lower[i] = ma - num_std * std
        bandwidth[i] = (upper[i] - lower[i]) / ma if ma > 0 else 0
    return upper, middle, lower, bandwidth


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


def make_volatility_transition(
    symbol: str,
    bb_period: int = 20,
    bb_std: float = 2.0,
    atr_period: int = 14,
    compression_lookback: int = 20,
    bw_compression_pct: float = 0.7,
    atr_expansion_mult: float = 1.2,
    expansion_confirmation_bars: int = 2,
    stop_atr_mult: float = 2.0,
    target_atr_mult: float = 3.0,
    ema_fast: int = 10,
    ema_slow: int = 30,
    min_bars: int = 60,
    min_score: float = 0.15,
    lots: float = 0.01,
    **kwargs,
):
    """Create volatility transition strategy adapter.

    Entry logic:
    1. Compression state (BB bandwidth low, ATR low)
    2. Expansion onset (BB widening, ATR rising)
    3. Direction from EMA structure
    4. Enter on first confirmed expansion bar
    """
    pip = 0.001 if "JPY" in symbol else 0.0001
    min_stop_pips = 20 if "JPY" in symbol else 12
    min_target_pips = 30 if "JPY" in symbol else 20

    def strategy_fn(data: SymbolData, state: BacktestState) -> Optional[dict]:
        if len(data.bars) < min_bars:
            return None

        closes = np.array(data.closes, dtype=float)
        highs = np.array(data.highs, dtype=float)
        lows = np.array(data.lows, dtype=float)

        bb_upper, bb_middle, bb_lower, bw = _bollinger(closes, bb_period, bb_std)
        atr = _atr(highs, lows, closes, atr_period)
        fast = _ema(closes, ema_fast)
        slow = _ema(closes, ema_slow)
        rsi = _rsi(closes, 14)

        if any(np.isnan(x) for x in [bw[-1], atr[-1], fast[-1], slow[-1]]):
            return None
        if atr[-1] <= 0:
            return None

        cur_price = closes[-1]

        # 1. Check if we were recently in compression
        bw_series = bw[-compression_lookback:]
        bw_series = bw_series[~np.isnan(bw_series)]
        if len(bw_series) < 5:
            return None

        bw_mean = np.mean(bw_series)
        bw_min = np.min(bw_series)

        # Recent compression: bandwidth was low in recent past
        was_compressed = False
        for i in range(-compression_lookback, -expansion_confirmation_bars - 1):
            if i + len(bw) >= 0 and not np.isnan(bw[i]):
                if bw[i] < bw_mean * bw_compression_pct:
                    was_compressed = True
                    break

        if not was_compressed:
            return None

        # 2. Expansion onset: bandwidth rising, ATR rising
        bw_now = bw[-1]
        bw_prev = bw[-2] if len(bw) > 1 and not np.isnan(bw[-2]) else bw_now
        atr_now = atr[-1]
        atr_prev = atr[-2] if len(atr) > 1 and not np.isnan(atr[-2]) else atr_now

        expanding_bw = bw_now > bw_prev
        expanding_atr = atr_now > atr_prev * atr_expansion_mult

        if not (expanding_bw and expanding_atr):
            return None

        # 3. Direction from EMA structure
        cur_fast = fast[-1]
        cur_slow = slow[-1]
        if np.isnan(cur_fast) or np.isnan(cur_slow):
            return None

        # 4. Displacement confirmation
        bar_range = highs[-1] - lows[-1]
        displacement = abs(cur_price - (closes[-2] if len(closes) > 1 else cur_price))
        has_displacement = bar_range > atr[-1] * 0.8 or displacement > atr[-1] * 0.5

        score = 0.0
        reasons = []

        if cur_fast > cur_slow:
            score += 0.25
            reasons.append("bullish_structure")
            if cur_price > closes[-2]:
                score += 0.2
                reasons.append("bullish_displacement")
        elif cur_fast < cur_slow:
            score += 0.25
            reasons.append("bearish_structure")
            if cur_price < closes[-2]:
                score += 0.2
                reasons.append("bearish_displacement")

        if has_displacement:
            score += 0.2
            reasons.append("displacement")

        if expanding_atr:
            score += 0.15
            reasons.append("atr_expansion")

        if expanding_bw:
            score += 0.1
            reasons.append("bb_widening")

        # RSI confirmation
        if not np.isnan(rsi[-1]):
            if cur_fast > cur_slow and 40 < rsi[-1] < 70:
                score += 0.1
                reasons.append("rsi_bullish_range")
            elif cur_fast < cur_slow and 30 < rsi[-1] < 60:
                score += 0.1
                reasons.append("rsi_bearish_range")

        if score < min_score:
            return None

        stop_pts = max(atr[-1] * stop_atr_mult, min_stop_pips * pip)
        target_pts = max(atr[-1] * target_atr_mult, min_target_pips * pip)

        side = "BUY" if cur_fast > cur_slow else "SELL"
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
