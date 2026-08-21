"""Baseline report generator — produces the Phase 2 baseline report.

Marked as UNPROVEN until validation is performed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .execution_model import BacktestTrade
from .analysis import (
    compute_baseline_stats,
    analyze_by_session,
    analyze_by_direction,
    analyze_by_month,
    analyze_by_day_of_week,
    analyze_by_exit_reason,
    analyze_by_regime,
)
from .telemetry import Telemetry

logger = logging.getLogger(__name__)


def generate_baseline_report(
    trades: list[BacktestTrade],
    symbol: str,
    cost_scenario: str = "REALISTIC",
    telemetry: Optional[Telemetry] = None,
    all_data: Optional[dict] = None,
) -> str:
    """Generate the complete Phase 2 baseline report.

    Returns markdown-formatted report.
    """
    stats = compute_baseline_stats(trades)

    lines = []
    lines.append("=" * 70)
    lines.append("  TRADING KING — PHASE 2 BASELINE REPORT")
    lines.append("=" * 70)
    lines.append(f"  Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"  Symbol: {symbol}")
    lines.append(f"  Cost Scenario: {cost_scenario}")
    lines.append(f"  Verdict: UNPROVEN")
    lines.append("=" * 70)
    lines.append("")

    # Global stats
    lines.append("## GLOBAL STATISTICS")
    lines.append("")
    lines.append(f"  Total Trades:          {stats['total_trades']}")
    lines.append(f"  Wins:                  {stats['win_count']}")
    lines.append(f"  Losses:                {stats['loss_count']}")
    lines.append(f"  Win Rate:              {stats['win_rate']:.1%}")
    lines.append(f"  Avg Win:               ${stats['avg_win']:.2f}")
    lines.append(f"  Avg Loss:              ${stats['avg_loss']:.2f}")
    lines.append(f"  Profit Factor:         {stats['profit_factor']:.2f}")
    lines.append(f"  Expectancy (gross):    ${stats['expectancy']:.2f}/trade")
    lines.append(f"  Expectancy (net):      ${stats['expectancy_after_cost']:.2f}/trade")
    lines.append(f"  Avg R:                 {stats['avg_r']:.3f}")
    lines.append(f"  Total PnL (gross):     ${stats['total_pnl']:.2f}")
    lines.append(f"  Total PnL (net):       ${stats['total_pnl_after_cost']:.2f}")
    lines.append(f"  Total Cost:            ${stats['total_cost']:.2f}")
    lines.append(f"  Max Drawdown:          ${stats['max_drawdown']:.2f}")
    lines.append(f"  Avg Bars Held:         {stats['avg_bars_held']:.1f}")
    lines.append(f"  Median Bars Held:      {stats['median_bars_held']}")
    lines.append(f"  Avg MFE:               ${stats['avg_mfe']:.2f}")
    lines.append(f"  Avg MAE:               ${stats['avg_mae']:.2f}")
    lines.append(f"  Largest Win:           ${stats['largest_win']:.2f}")
    lines.append(f"  Largest Loss:          ${stats['largest_loss']:.2f}")
    lines.append("")

    # Cost analysis
    lines.append("## COST ANALYSIS")
    lines.append("")
    if stats['total_trades'] > 0:
        avg_cost_per_trade = stats['total_cost'] / stats['total_trades']
        cost_as_pct_of_win = avg_cost_per_trade / stats['avg_win'] * 100 if stats['avg_win'] > 0 else 0
        lines.append(f"  Avg Cost/Trade:        ${avg_cost_per_trade:.2f}")
        lines.append(f"  Cost as % of Avg Win:  {cost_as_pct_of_win:.1f}%")
        lines.append(f"  Gross->Net Decay:      {(1 - stats['total_pnl_after_cost']/stats['total_pnl']*100) if stats['total_pnl'] != 0 else 0:.1f}%")
    lines.append("")

    # By session
    lines.append("## BY SESSION")
    lines.append("")
    session_stats = analyze_by_session(trades)
    for name, seg in session_stats.items():
        lines.append(f"  {name:12s}: {seg.trade_count:3d} trades, WR={seg.win_rate:.0%}, "
                      f"PF={seg.profit_factor:.2f}, E=${seg.expectancy:.2f}, "
                      f"net=${seg.total_pnl_after_cost:.2f}")
    lines.append("")

    # By direction
    lines.append("## BY DIRECTION")
    lines.append("")
    dir_stats = analyze_by_direction(trades)
    for name, seg in dir_stats.items():
        lines.append(f"  {name:6s}: {seg.trade_count:3d} trades, WR={seg.win_rate:.0%}, "
                      f"PF={seg.profit_factor:.2f}, E=${seg.expectancy:.2f}")
    lines.append("")

    # By month
    lines.append("## BY MONTH")
    lines.append("")
    month_stats = analyze_by_month(trades)
    for name, seg in month_stats.items():
        lines.append(f"  {name}: {seg.trade_count:3d} trades, WR={seg.win_rate:.0%}, "
                      f"net=${seg.total_pnl_after_cost:.2f}")
    lines.append("")

    # By exit reason
    lines.append("## BY EXIT REASON")
    lines.append("")
    reason_stats = analyze_by_exit_reason(trades)
    for name, seg in reason_stats.items():
        lines.append(f"  {name:12s}: {seg.trade_count:3d} trades, WR={seg.win_rate:.0%}, "
                      f"E=${seg.expectancy:.2f}")
    lines.append("")

    # By regime (if data available)
    if all_data:
        lines.append("## BY REGIME")
        lines.append("")
        regime_stats = analyze_by_regime(trades, all_data)
        for name, seg in regime_stats.items():
            lines.append(f"  {name:12s}: {seg.trade_count:3d} trades, WR={seg.win_rate:.0%}, "
                          f"PF={seg.profit_factor:.2f}, E=${seg.expectancy:.2f}")
        lines.append("")

    # Telemetry
    if telemetry:
        lines.append("## REJECTION TELEMETRY")
        lines.append("")
        rej_stats = telemetry.get_stats()
        lines.append(f"  Total Candidates:      {rej_stats.total_candidates}")
        lines.append(f"  Filled:                {rej_stats.total_filled}")
        lines.append(f"  Rejected:              {rej_stats.total_rejected}")
        lines.append(f"  Fill Rate:             {rej_stats.total_filled/rej_stats.total_candidates:.1%}" if rej_stats.total_candidates > 0 else "")
        lines.append(f"  Filled Win Rate:       {rej_stats.filled_win_rate:.1%}")
        lines.append("")
        if rej_stats.rejections_by_reason:
            lines.append("  Rejection Reasons:")
            for reason, count in sorted(rej_stats.rejections_by_reason.items(), key=lambda x: -x[1]):
                lines.append(f"    {reason:30s}: {count}")
        lines.append("")

    # Verdict
    lines.append("=" * 70)
    lines.append("  VERDICT: UNPROVEN")
    lines.append("")
    lines.append("  This baseline report is a HYPOTHESIS, not proof of edge.")
    lines.append("  Positive results must be validated OOS before any claim")
    lines.append("  of profitability.")
    lines.append("=" * 70)

    report = "\n".join(lines)

    # Fix the typo
    report = report.replace("cost_as_pct_of_pct", "cost_as_pct_of_win")

    return report


def generate_comparison_report(
    baseline_stats: dict,
    stressed_stats: dict,
    symbol: str,
) -> str:
    """Compare BASE vs STRESSED cost results."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"  COST STRESS COMPARISON — {symbol}")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"  {'Metric':30s} {'BASE':>12s} {'STRESSED':>12s} {'Delta':>12s}")
    lines.append("  " + "-" * 66)

    metrics = [
        ("Total Trades", "total_trades", "d"),
        ("Win Rate", "win_rate", ".1%"),
        ("Expectancy", "expectancy", "$.2f"),
        ("Profit Factor", "profit_factor", ".2f"),
        ("Total PnL (net)", "total_pnl_after_cost", "$.2f"),
        ("Max Drawdown", "max_drawdown", "$.2f"),
    ]

    for label, key, fmt in metrics:
        b = baseline_stats.get(key, 0)
        s = stressed_stats.get(key, 0)
        delta = s - b
        if fmt == ".1%":
            lines.append(f"  {label:30s} {b:12.1%} {s:12.1%} {delta:12.1%}")
        elif fmt.startswith("$"):
            lines.append(f"  {label:30s} {b:12.2f} {s:12.2f} {delta:12.2f}")
        else:
            lines.append(f"  {label:30s} {b:12{fmt}} {s:12{fmt}} {delta:12{fmt}}")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)
