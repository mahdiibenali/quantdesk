"""Family 3: Breakout — compression resolves into directional moves.

Hypothesis: Periods of low volatility (compression) are followed by
high volatility (expansion). Trading the breakout from compression
captures the initial move.

Logic:
- Detect compression via Bollinger Band width and ATR contraction
- Identify breakout (price closes outside range with displacement)
- Confirm follow-through (volume/momentum, not a false breakout)
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
    """Bollinger Bands: upper, middle, lower, bandwidth."""
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


def make_breakout(
    symbol: str,
    bb_period: int = 20,
    bb_std: float = 2.0,
    compression_lookback: int = 20,
    compression_threshold_pct: float = 0.5,
    atr_period: int = 14,
    atr_expansion_mult: float = 1.3,
    displacement_mult: float = 1.5,
    stop_atr_mult: float = 2.0,
    target_atr_mult: float = 3.0,
    ema_fast: int = 10,
    min_bars: int = 60,
    min_score: float = 0.15,
    lots: float = 0.01,
    **kwargs,
):
    """Create breakout strategy adapter.

    Entry logic:
    1. Compression detected (BB bandwidth below threshold, ATR contracting)
    2. Breakout (price closes outside BB, or range breakout)
    3. Displacement confirmation (large bar, momentum)
    4. Follow-through (price staying outside range)
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

        upper, middle, lower, bw = _bollinger(closes, bb_period, bb_std)
        atr = _atr(highs, lows, closes, atr_period)
        fast_ema = _ema(closes, ema_fast)

        if any(np.isnan(x) for x in [bw[-1], atr[-1], fast_ema[-1]]):
            return None
        if atr[-1] <= 0:
            return None

        cur_price = closes[-1]
        prev_price = closes[-2] if len(closes) > 1 else cur_price

        # 1. Compression: BB bandwidth below threshold
        bw_lookback = bw[-compression_lookback:]
        bw_lookback = bw_lookback[~np.isnan(bw_lookback)]
        if len(bw_lookback) < 5:
            return None

        bw_current = bw[-1]
        bw_mean = np.mean(bw_lookback)
        bw_min = np.min(bw_lookback)
        is_compressed = bw_current < bw_mean * compression_threshold_pct or bw_current < bw_min * 1.1

        if not is_compressed:
            return None

        # 2. ATR contraction
        atr_series = atr[-compression_lookback:]
        atr_series = atr_series[~np.isnan(atr_series)]
        if len(atr_series) < 5:
            return None
        atr_mean = np.mean(atr_series)
        atr_contracting = atr[-1] < atr_mean * 0.8

        if not atr_contracting:
            return None

        # 3. Breakout: price outside BB or significant range break
        bar_range = highs[-1] - lows[-1]
        displacement = abs(cur_price - prev_price)

        breakout_up = cur_price > upper[-1] if not np.isnan(upper[-1]) else False
        breakout_down = cur_price < lower[-1] if not np.isnan(lower[-1]) else False

        # Also check for range breakout: close above/below recent range
        lookback = min(20, len(closes) - 1)
        range_high = np.max(highs[-lookback:])
        range_low = np.min(lows[-lookback:])
        range_breakout_up = cur_price > range_high and displacement > atr[-1] * 0.5
        range_breakout_down = cur_price < range_low and displacement > atr[-1] * 0.5

        is_breakout = breakout_up or breakout_down or range_breakout_up or range_breakout_down
        if not is_breakout:
            return None

        # 4. Displacement confirmation
        has_displacement = bar_range > atr[-1] * 0.8 or displacement > atr[-1] * 0.5

        score = 0.0
        reasons = []

        if breakout_up or range_breakout_up:
            score += 0.4
            reasons.append("breakout_up")
            if has_displacement:
                score += 0.3
                reasons.append("displacement")
            if cur_price > fast_ema[-1]:
                score += 0.15
                reasons.append("above_ema")
            if bw_current < bw_mean * 0.7:
                score += 0.15
                reasons.append("deep_compression")
        else:
            score += 0.4
            reasons.append("breakout_down")
            if has_displacement:
                score += 0.3
                reasons.append("displacement")
            if cur_price < fast_ema[-1]:
                score += 0.15
                reasons.append("below_ema")
            if bw_current < bw_mean * 0.7:
                score += 0.15
                reasons.append("deep_compression")

        if score < min_score:
            return None

        stop_pts = max(atr[-1] * stop_atr_mult, min_stop_pips * pip)
        target_pts = max(atr[-1] * target_atr_mult, min_target_pips * pip)

        side = "BUY" if (breakout_up or range_breakout_up) else "SELL"
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
