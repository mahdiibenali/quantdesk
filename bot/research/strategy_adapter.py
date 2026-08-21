"""Strategy adapter — connects the real Scalp strategy to the backtest engine.

Architecture: market data → strategy → Signal → execution simulator

One source of truth for strategy logic.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from ..strategies.scalp import Scalp, PROFILES
from ..strategies.base import Side
from ..strategies.signals import SignalAggregator
from ..strategies.patterns import detect_all as detect_patterns
from .data_pipeline import Bar, SymbolData
from .execution_model import BacktestState

logger = logging.getLogger(__name__)


def make_strategy_adapter(
    symbol: str,
    ema_fast: int = 5,
    ema_slow: int = 20,
    rsi_period: int = 14,
    rsi_oversold: float = 35.0,
    rsi_overbought: float = 65.0,
    atr_period: int = 14,
    stop_atr_mult: float = 1.5,
    target_atr_mult: float = 2.5,
    min_bars: int = 30,
    timeframe: int = 5,
    min_score: float = 0.1,
    use_trend_filter: bool = True,
    lots: float = 0.01,
    **kwargs,
):
    """Create a strategy function compatible with BacktestEngine.run().

    Returns a callable: (SymbolData, BacktestState) -> Optional[dict]
    """
    # Create a Scalp instance (reuses real strategy logic)
    strategy = Scalp(
        symbol=symbol,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        rsi_period=rsi_period,
        rsi_oversold=rsi_oversold,
        rsi_overbought=rsi_overbought,
        atr_period=atr_period,
        stop_atr_mult=stop_atr_mult,
        target_atr_mult=target_atr_mult,
        min_bars=min_bars,
        timeframe=timeframe,
    )

    profile = PROFILES.get(symbol, PROFILES["EURUSD"])
    pip = profile["pip"]
    min_stop_pips = profile["min_stop_pips"]
    min_target_pips = profile["min_target_pips"]

    def strategy_fn(data: SymbolData, state: BacktestState) -> Optional[dict]:
        """Run strategy on historical data slice.

        Returns dict with: side, stop_distance_pts, target_distance_pts,
                          score, confidence, reasons, lots, atr
        Or None if no signal.
        """
        if len(data.bars) < min_bars:
            return None

        closes = data.closes
        highs = data.highs
        lows = data.lows
        opens = data.opens

        # Compute indicators (same logic as Scalp.on_idle)
        closes_arr = np.array(closes, dtype=float)
        fast_ema = Scalp._ema(closes_arr, ema_fast)
        slow_ema = Scalp._ema(closes_arr, ema_slow)
        ema50 = Scalp._ema(closes_arr, 50) if len(closes) >= 50 else None
        rsi = Scalp._rsi(closes_arr, rsi_period)
        atr = Scalp._atr(
            np.array(highs, dtype=float),
            np.array(lows, dtype=float),
            closes_arr,
            atr_period,
        )

        if atr <= 0 or np.isnan(atr):
            return None

        cur_fast = fast_ema[-1]
        cur_slow = slow_ema[-1]
        cur_rsi = rsi[-1]
        cur_price = closes_arr[-1]

        prev_fast = fast_ema[-2] if len(fast_ema) > 1 else cur_fast
        prev_slow = slow_ema[-2] if len(slow_ema) > 1 else cur_slow

        if any(np.isnan(x) for x in [cur_fast, cur_slow, cur_rsi, prev_fast, prev_slow]):
            return None

        # Signal aggregation (same as Scalp.on_idle)
        agg = SignalAggregator(["ema_cross", "rsi", "momentum", "patterns"])

        # EMA crossover
        cross_up = prev_fast <= prev_slow and cur_fast > cur_slow
        cross_down = prev_fast >= prev_slow and cur_fast < cur_slow
        above = cur_fast > cur_slow

        if cross_up:
            agg.add_component("ema_cross", score=0.8, weight=1.5)
        elif cross_down:
            agg.add_component("ema_cross", score=-0.8, weight=1.5)
        elif above:
            agg.add_component("ema_cross", score=0.3, weight=1.0)
        else:
            agg.add_component("ema_cross", score=-0.3, weight=1.0)

        # RSI
        if cur_rsi < rsi_oversold:
            agg.add_component("rsi", score=0.7, weight=1.0)
        elif cur_rsi > rsi_overbought:
            agg.add_component("rsi", score=-0.7, weight=1.0)
        else:
            rsi_mid = (cur_rsi - 50) / 50
            agg.add_component("rsi", score=-rsi_mid * 0.3, weight=0.8)

        # Momentum
        momentum = (cur_price - cur_slow) / atr if atr > 0 else 0
        momentum = max(-1, min(1, momentum))
        agg.add_component("momentum", score=momentum * 0.5, weight=1.0)

        # Candlestick patterns
        if len(opens) >= 5:
            pat = detect_patterns(
                list(opens), list(highs), list(lows), list(closes)
            )
            if pat["combined"] != 0.0:
                agg.add_component("patterns", score=pat["combined"], weight=1.3)

        sig = agg.compute()

        if sig.score == 0 and sig.confidence == 0:
            return None

        # TREND FILTER: only trade in direction of EMA50
        if use_trend_filter and ema50 is not None and not np.isnan(ema50[-1]):
            if sig.score > 0 and cur_price < ema50[-1]:
                return None
            if sig.score < 0 and cur_price > ema50[-1]:
                return None

        # Compute stop and target
        stop_pts = max(atr * stop_atr_mult, min_stop_pips * pip)
        target_pts = max(atr * target_atr_mult, min_target_pips * pip)

        if sig.score > min_score:
            return {
                "side": "BUY",
                "stop_distance_pts": stop_pts,
                "target_distance_pts": target_pts,
                "score": sig.score,
                "confidence": sig.confidence,
                "reasons": sig.reasons,
                "lots": lots,
                "atr": atr,
            }
        elif sig.score < -min_score:
            return {
                "side": "SELL",
                "stop_distance_pts": stop_pts,
                "target_distance_pts": target_pts,
                "score": abs(sig.score),
                "confidence": sig.confidence,
                "reasons": sig.reasons,
                "lots": lots,
                "atr": atr,
            }

        return None

    return strategy_fn


def make_ablation_adapter(
    symbol: str,
    components: list[str],
    **kwargs,
):
    """Create a strategy adapter that uses only specified components.

    Components: "ema", "rsi", "momentum", "patterns", "trend_filter"
    """
    base_fn = make_strategy_adapter(symbol=symbol, **kwargs)

    def ablation_fn(data: SymbolData, state: BacktestState) -> Optional[dict]:
        """Run strategy with only specified components."""
        if len(data.bars) < kwargs.get("min_bars", 30):
            return None

        closes = data.closes
        highs = data.highs
        lows = data.lows
        opens = data.opens

        closes_arr = np.array(closes, dtype=float)
        ema_fast_period = kwargs.get("ema_fast", 5)
        ema_slow_period = kwargs.get("ema_slow", 20)
        rsi_period_val = kwargs.get("rsi_period", 14)
        atr_period_val = kwargs.get("atr_period", 14)
        stop_mult = kwargs.get("stop_atr_mult", 1.5)
        target_mult = kwargs.get("target_atr_mult", 2.5)
        rsi_os = kwargs.get("rsi_oversold", 35.0)
        rsi_ob = kwargs.get("rsi_overbought", 65.0)

        profile = PROFILES.get(symbol, PROFILES["EURUSD"])
        pip_val = profile["pip"]
        min_stop = profile["min_stop_pips"]
        min_target = profile["min_target_pips"]

        fast_ema = Scalp._ema(closes_arr, ema_fast_period)
        slow_ema = Scalp._ema(closes_arr, ema_slow_period)
        ema50 = Scalp._ema(closes_arr, 50) if len(closes) >= 50 and "trend_filter" in components else None
        rsi = Scalp._rsi(closes_arr, rsi_period_val)
        atr = Scalp._atr(
            np.array(highs, dtype=float),
            np.array(lows, dtype=float),
            closes_arr,
            atr_period_val,
        )

        if atr <= 0 or np.isnan(atr):
            return None

        cur_fast = fast_ema[-1]
        cur_slow = slow_ema[-1]
        cur_rsi = rsi[-1]
        cur_price = closes_arr[-1]
        prev_fast = fast_ema[-2] if len(fast_ema) > 1 else cur_fast
        prev_slow = slow_ema[-2] if len(slow_ema) > 1 else cur_slow

        if any(np.isnan(x) for x in [cur_fast, cur_slow, cur_rsi, prev_fast, prev_slow]):
            return None

        # Build score from selected components only
        total_score = 0.0
        total_weight = 0.0
        reasons = []

        if "ema" in components:
            cross_up = prev_fast <= prev_slow and cur_fast > cur_slow
            cross_down = prev_fast >= prev_slow and cur_fast < cur_slow
            above = cur_fast > cur_slow
            if cross_up:
                total_score += 0.8 * 1.5
                total_weight += 1.5
                reasons.append("ema_cross_up")
            elif cross_down:
                total_score += -0.8 * 1.5
                total_weight += 1.5
                reasons.append("ema_cross_down")
            elif above:
                total_score += 0.3 * 1.0
                total_weight += 1.0
                reasons.append("ema_above")
            else:
                total_score += -0.3 * 1.0
                total_weight += 1.0
                reasons.append("ema_below")

        if "rsi" in components:
            if cur_rsi < rsi_os:
                total_score += 0.7 * 1.0
                total_weight += 1.0
                reasons.append("rsi_oversold")
            elif cur_rsi > rsi_ob:
                total_score += -0.7 * 1.0
                total_weight += 1.0
                reasons.append("rsi_overbought")
            else:
                rsi_mid = (cur_rsi - 50) / 50
                total_score += -rsi_mid * 0.3 * 0.8
                total_weight += 0.8

        if "momentum" in components:
            mom = (cur_price - cur_slow) / atr if atr > 0 else 0
            mom = max(-1, min(1, mom))
            total_score += mom * 0.5 * 1.0
            total_weight += 1.0
            if abs(mom) > 0.3:
                reasons.append("momentum")

        if "patterns" in components and len(opens) >= 5:
            pat = detect_patterns(list(opens), list(highs), list(lows), list(closes))
            if pat["combined"] != 0.0:
                total_score += pat["combined"] * 1.3
                total_weight += 1.3
                reasons.append("pattern")

        if total_weight <= 0:
            return None

        final_score = total_score / total_weight

        # Trend filter
        if "trend_filter" in components and ema50 is not None and not np.isnan(ema50[-1]):
            if final_score > 0 and cur_price < ema50[-1]:
                return None
            if final_score < 0 and cur_price > ema50[-1]:
                return None

        stop_pts = max(atr * stop_mult, min_stop * pip_val)
        target_pts = max(atr * target_mult, min_target * pip_val)

        min_score = kwargs.get("min_score", 0.1)

        if final_score > min_score:
            return {
                "side": "BUY",
                "stop_distance_pts": stop_pts,
                "target_distance_pts": target_pts,
                "score": abs(final_score),
                "confidence": min(0.92, 0.42 + abs(final_score) * 0.5),
                "reasons": reasons,
                "lots": kwargs.get("lots", 0.01),
                "atr": atr,
            }
        elif final_score < -min_score:
            return {
                "side": "SELL",
                "stop_distance_pts": stop_pts,
                "target_distance_pts": target_pts,
                "score": abs(final_score),
                "confidence": min(0.92, 0.42 + abs(final_score) * 0.5),
                "reasons": reasons,
                "lots": kwargs.get("lots", 0.01),
                "atr": atr,
            }

        return None

    return ablation_fn
