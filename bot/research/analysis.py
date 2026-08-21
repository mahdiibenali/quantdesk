"""Analysis modules — regime, session, pattern, and walk-forward analysis.

Computes baseline statistics broken down by various dimensions.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..strategies.scalp import PROFILES
from .execution_model import BacktestTrade

logger = logging.getLogger(__name__)


@dataclass
class SegmentStats:
    """Statistics for a segment of trades (by regime, session, etc.)."""
    label: str
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    total_pnl: float = 0.0
    total_pnl_after_cost: float = 0.0
    avg_r: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    avg_bars_held: float = 0.0
    avg_mfe: float = 0.0
    avg_mae: float = 0.0
    expectancy: float = 0.0  # avg PnL per trade


def compute_baseline_stats(trades: list[BacktestTrade]) -> dict:
    """Compute complete baseline statistics from trade list."""
    if not trades:
        return {"error": "no trades"}

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]

    gross_profit = sum(t.pnl for t in wins) if wins else 0
    gross_loss = abs(sum(t.pnl for t in losses)) if losses else 0

    total_pnl = sum(t.pnl for t in trades)
    total_pnl_after_cost = sum(t.pnl_after_cost for t in trades)
    total_cost = sum(t.cost.total_round_trip * t.lots * t.cost.contract_size for t in trades)

    # Expectancy
    avg_pnl = total_pnl / len(trades)
    avg_pnl_after_cost = total_pnl_after_cost / len(trades)

    # R-multiples (cap extremes to avoid division by near-zero risk)
    r_values = [max(-10.0, min(10.0, t.r_multiple)) for t in trades]
    avg_r = sum(r_values) / len(r_values) if r_values else 0

    # Drawdown from equity curve
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t.pnl_after_cost
        if equity > peak:
            peak = equity
        dd = peak - equity
        max_dd = max(max_dd, dd)

    # Holding time
    bars_held = [t.bars_held for t in trades]

    # MFE/MAE
    mfes = [t.mfe for t in trades]
    maes = [t.maе for t in trades]

    # Streaks
    streak = 0
    max_streak = 0
    for t in trades:
        if t.pnl > 0:
            streak = max(0, streak + 1)
        else:
            streak = min(0, streak - 1)
        max_streak = max(max_streak, abs(streak))

    return {
        "total_trades": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": len(wins) / len(trades),
        "avg_win": sum(t.pnl for t in wins) / len(wins) if wins else 0,
        "avg_loss": -sum(t.pnl for t in losses) / len(losses) if losses else 0,
        "total_pnl": total_pnl,
        "total_pnl_after_cost": total_pnl_after_cost,
        "total_cost": total_cost,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else 0
        ),
        "expectancy": avg_pnl,
        "expectancy_after_cost": avg_pnl_after_cost,
        "avg_r": avg_r,
        "max_drawdown": max_dd,
        "avg_bars_held": sum(bars_held) / len(bars_held),
        "median_bars_held": sorted(bars_held)[len(bars_held) // 2],
        "avg_mfe": sum(mfes) / len(mfes),
        "avg_mae": sum(maes) / len(maes),
        "max_winning_streak": max_streak,
        "largest_win": max((t.pnl for t in trades), default=0),
        "largest_loss": min((t.pnl for t in trades), default=0),
    }


def analyze_by_session(trades: list[BacktestTrade]) -> dict[str, SegmentStats]:
    """Analyze performance by trading session."""
    sessions = {
        "asian": [],      # 00:00 - 07:00 UTC
        "london": [],     # 07:00 - 13:00 UTC
        "overlap": [],    # 13:00 - 17:00 UTC (London/NY)
        "new_york": [],   # 17:00 - 22:00 UTC
        "other": [],      # 22:00 - 00:00 UTC
    }

    for t in trades:
        hour = t.entry_time.hour
        if 0 <= hour < 7:
            sessions["asian"].append(t)
        elif 7 <= hour < 13:
            sessions["london"].append(t)
        elif 13 <= hour < 17:
            sessions["overlap"].append(t)
        elif 17 <= hour < 22:
            sessions["new_york"].append(t)
        else:
            sessions["other"].append(t)

    results = {}
    for name, seg_trades in sessions.items():
        if seg_trades:
            results[name] = _segment_stats(name, seg_trades)
    return results


def analyze_by_direction(trades: list[BacktestTrade]) -> dict[str, SegmentStats]:
    """Analyze performance by trade direction."""
    buys = [t for t in trades if t.side == "BUY"]
    sells = [t for t in trades if t.side == "SELL"]

    results = {}
    if buys:
        results["BUY"] = _segment_stats("BUY", buys)
    if sells:
        results["SELL"] = _segment_stats("SELL", sells)
    return results


def analyze_by_month(trades: list[BacktestTrade]) -> dict[str, SegmentStats]:
    """Analyze performance by month."""
    months = {}
    for t in trades:
        key = t.entry_time.strftime("%Y-%m")
        months.setdefault(key, []).append(t)

    return {k: _segment_stats(k, v) for k, v in sorted(months.items())}


def analyze_by_day_of_week(trades: list[BacktestTrade]) -> dict[str, SegmentStats]:
    """Analyze performance by day of week."""
    days = {}
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for t in trades:
        key = day_names[t.entry_time.weekday()]
        days.setdefault(key, []).append(t)

    return {k: _segment_stats(k, v) for k, v in days.items()}


def analyze_by_exit_reason(trades: list[BacktestTrade]) -> dict[str, SegmentStats]:
    """Analyze performance by exit reason."""
    reasons = {}
    for t in trades:
        reasons.setdefault(t.exit_reason, []).append(t)

    return {k: _segment_stats(k, v) for k, v in reasons.items()}


def classify_regime_simple(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    lookback: int = 20,
) -> str:
    """Simple regime classification based on observable market states.

    Returns: "trend", "range", "expansion", "compression", "chop"
    """
    if len(closes) < lookback + 5:
        return "unknown"

    # ATR contraction/expansion
    trs = []
    for i in range(max(1, len(closes) - lookback), len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    if len(trs) < 5:
        return "unknown"

    atr_now = sum(trs[-3:]) / 3
    atr_prev = sum(trs[:-3]) / max(1, len(trs) - 3)
    atr_sma = sum(trs) / len(trs)

    # Trend strength: price displacement from mean
    recent = closes[-lookback:]
    mean_price = np.mean(recent)
    std_price = np.std(recent)
    displacement = abs(closes[-1] - mean_price) / std_price if std_price > 0 else 0

    # Range efficiency
    range_high = max(highs[-lookback:])
    range_low = min(lows[-lookback:])
    total_range = range_high - range_low
    net_move = abs(closes[-1] - closes[-lookback])
    efficiency = net_move / total_range if total_range > 0 else 0

    # Classification
    if efficiency > 0.6 and displacement > 1.0:
        return "trend"
    elif atr_now > atr_prev * 1.3:
        return "expansion"
    elif atr_now < atr_sma * 0.7:
        return "compression"
    elif efficiency < 0.3 and displacement < 0.5:
        return "range"
    elif efficiency < 0.4:
        return "chop"
    else:
        return "range"


def analyze_by_regime(
    trades: list[BacktestTrade],
    all_data: dict,  # symbol -> SymbolData
) -> dict[str, SegmentStats]:
    """Analyze performance by market regime at entry time."""
    # For each trade, classify the regime at entry
    regime_trades = {}
    for t in trades:
        # Get data for this symbol
        data = all_data.get(t.symbol)
        if data is None:
            continue

        # Find bar index at entry
        entry_idx = t.entry_bar_idx
        if entry_idx < 25:
            regime = "unknown"
        else:
            closes = data.closes[:entry_idx + 1]
            highs = data.highs[:entry_idx + 1]
            lows = data.lows[:entry_idx + 1]
            regime = classify_regime_simple(closes, highs, lows)

        regime_trades.setdefault(regime, []).append(t)

    return {k: _segment_stats(k, v) for k, v in regime_trades.items()}


def analyze_patterns(
    trades: list[BacktestTrade],
) -> dict[str, dict]:
    """Analyze each pattern's performance independently."""
    # This requires pattern metadata per trade
    # For now, return basic stats grouped by signal reasons
    pattern_stats = {}
    for t in trades:
        for reason in t.signal_reasons:
            if "pattern" in reason.lower() or "engulfing" in reason.lower() or \
               "pin" in reason.lower() or "hammer" in reason.lower() or \
               "doji" in reason.lower() or "harami" in reason.lower():
                pattern_stats.setdefault(reason, []).append(t)

    results = {}
    for pattern, pat_trades in pattern_stats.items():
        wins = [t for t in pat_trades if t.pnl > 0]
        results[pattern] = {
            "sample_size": len(pat_trades),
            "win_rate": len(wins) / len(pat_trades) if pat_trades else 0,
            "avg_pnl": sum(t.pnl for t in pat_trades) / len(pat_trades) if pat_trades else 0,
            "avg_r": sum(t.r_multiple for t in pat_trades) / len(pat_trades) if pat_trades else 0,
            "profit_factor": (
                sum(t.pnl for t in wins) / abs(sum(t.pnl for t in pat_trades if t.pnl <= 0))
                if any(t.pnl <= 0 for t in pat_trades) else 0
            ),
        }

    return results


def _segment_stats(label: str, trades: list[BacktestTrade]) -> SegmentStats:
    """Compute stats for a segment of trades."""
    if not trades:
        return SegmentStats(label=label)

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]

    gross_profit = sum(t.pnl for t in wins) if wins else 0
    gross_loss = abs(sum(t.pnl for t in losses)) if losses else 0

    return SegmentStats(
        label=label,
        trade_count=len(trades),
        win_count=len(wins),
        loss_count=len(losses),
        win_rate=len(wins) / len(trades),
        avg_win=sum(t.pnl for t in wins) / len(wins) if wins else 0,
        avg_loss=gross_loss / len(losses) if losses else 0,
        total_pnl=sum(t.pnl for t in trades),
        total_pnl_after_cost=sum(t.pnl_after_cost for t in trades),
        avg_r=sum(t.r_multiple for t in trades) / len(trades),
        profit_factor=gross_profit / gross_loss if gross_loss > 0 else 0,
        avg_bars_held=sum(t.bars_held for t in trades) / len(trades),
        avg_mfe=sum(t.mfe for t in trades) / len(trades),
        avg_mae=sum(t.maе for t in trades) / len(trades),
        expectancy=sum(t.pnl for t in trades) / len(trades),
    )
