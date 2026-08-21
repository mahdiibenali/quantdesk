"""Pre-registered promotion criteria checker.

Written BEFORE any strategy results exist.
Cannot be loosened after results are visible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from ..analysis import compute_baseline_stats
from ..execution_model import BacktestTrade

logger = logging.getLogger(__name__)


@dataclass
class PromotionResult:
    """Result of promotion criteria check for one family."""
    family: str
    symbol: str
    # Raw metrics
    total_trades_oos: int = 0
    win_rate_oos: float = 0.0
    profit_factor_oos: float = 0.0
    expectancy_oos: float = 0.0
    expectancy_after_cost_oos: float = 0.0
    avg_win_oos: float = 0.0
    avg_loss_oos: float = 0.0
    max_drawdown_oos: float = 0.0
    max_drawdown_pct_oos: float = 0.0
    trades_per_month_oos: float = 0.0
    # Walk-forward
    wf_folds_profitable: int = 0
    wf_folds_total: int = 0
    # Cost stress
    cost_stress_1_5x_positive: bool = False
    # Robustness
    robustness_positive_count: int = 0
    robustness_total: int = 0
    # Outlier removal
    expectancy_after_top1pct: float = 0.0
    expectancy_after_top5pct: float = 0.0
    expectancy_after_top10pct: float = 0.0
    # Monte Carlo
    mc_95th_dd: float = 0.0
    # Verdict
    passed_criteria: int = 0
    failed_criteria: int = 0
    verdict: str = "NOT_EVALUATED"  # REJECTED / UNPROVEN / PROMISING / STRONG
    failures: list[str] = field(default_factory=list)


def check_promotion(
    family: str,
    symbol: str,
    oos_trades: list[BacktestTrade],
    wf_results: list[dict],
    cost_stress_results: dict,
    robustness_results: list[dict],
    mc_95th_dd: float,
    data_days: float = 60.0,
    initial_equity: float = 10000.0,
) -> PromotionResult:
    """Check pre-registered promotion criteria.

    ALL criteria must pass for PROMISING verdict.
    """
    result = PromotionResult(family=family, symbol=symbol)

    if not oos_trades:
        result.verdict = "REJECTED"
        result.failures = ["no OOS trades"]
        return result

    # --- OOS metrics ---
    stats = compute_baseline_stats(oos_trades)
    result.total_trades_oos = stats["total_trades"]
    result.win_rate_oos = stats["win_rate"]
    result.profit_factor_oos = stats["profit_factor"]
    result.expectancy_oos = stats["expectancy"]
    result.expectancy_after_cost_oos = stats["expectancy_after_cost"]
    result.avg_win_oos = stats["avg_win"]
    result.avg_loss_oos = stats["avg_loss"]
    result.max_drawdown_oos = stats["max_drawdown"]
    result.max_drawdown_pct_oos = stats["max_drawdown"] / initial_equity

    # Trades per month
    if data_days > 0:
        result.trades_per_month_oos = len(oos_trades) / (data_days / 30.0)

    # --- Walk-forward ---
    wf_profitable = 0
    for wf in wf_results:
        if wf.get("expectancy_after_cost", 0) > 0:
            wf_profitable += 1
    result.wf_folds_profitable = wf_profitable
    result.wf_folds_total = len(wf_results)

    # --- Cost stress (1.5x) ---
    stress_1_5 = cost_stress_results.get("1.5x", {})
    result.cost_stress_1_5x_positive = stress_1_5.get("pnl_net", 0) > 0

    # --- Robustness ---
    rob_positive = sum(1 for r in robustness_results if r.get("expectancy_after_cost", 0) > 0)
    result.robustness_positive_count = rob_positive
    result.robustness_total = len(robustness_results)

    # --- Outlier removal ---
    pnls = sorted([t.pnl_after_cost for t in oos_trades], reverse=True)
    n = len(pnls)

    for pct, attr in [(0.01, "expectancy_after_top1pct"),
                       (0.05, "expectancy_after_top5pct"),
                       (0.10, "expectancy_after_top10pct")]:
        cutoff = max(1, int(n * pct))
        trimmed = pnls[cutoff:]
        setattr(result, attr, np.mean(trimmed) if trimmed else 0)

    # --- Monte Carlo ---
    result.mc_95th_dd = mc_95th_dd

    # === CHECK CRITERIA ===
    failures = []

    # 1. OOS expectancy > 0
    if result.expectancy_after_cost_oos <= 0:
        failures.append(f"OOS expectancy <= 0: ${result.expectancy_after_cost_oos:.4f}")

    # 2. OOS PF > 1.0
    if result.profit_factor_oos <= 1.0:
        failures.append(f"OOS PF <= 1.0: {result.profit_factor_oos:.2f}")

    # 3. WF folds >= 3/4 profitable
    if result.wf_folds_profitable < 3:
        failures.append(f"WF folds {result.wf_folds_profitable}/{result.wf_folds_total} < 3/4")

    # 4. Cost stress 1.5x still positive
    if not result.cost_stress_1_5x_positive:
        failures.append("Cost stress 1.5x not positive")

    # 5. Expectancy after removing top 10% > 0
    if result.expectancy_after_top10pct <= 0:
        failures.append(f"After removing top 10%, E <= 0: ${result.expectancy_after_top10pct:.4f}")

    # 6. Max drawdown < 50% of equity
    if result.max_drawdown_pct_oos >= 0.50:
        failures.append(f"Max DD >= 50%: {result.max_drawdown_pct_oos:.1%}")

    # 7. Monte Carlo 95th DD < 30% of equity
    if result.mc_95th_dd / initial_equity >= 0.30:
        failures.append(f"MC 95th DD >= 30%: {result.mc_95th_dd / initial_equity:.1%}")

    # 8. Trades per month >= 5
    if result.trades_per_month_oos < 5:
        failures.append(f"Trades/month < 5: {result.trades_per_month_oos:.1f}")

    # 9. Parameter robustness >= 3/5 positive
    if result.robustness_positive_count < 3:
        failures.append(f"Robustness {result.robustness_positive_count}/{result.robustness_total} < 3/5")

    result.failures = failures
    result.passed_criteria = 9 - len(failures)
    result.failed_criteria = len(failures)

    # Verdict
    if len(failures) == 0:
        if result.profit_factor_oos > 1.3 and result.expectancy_after_cost_oos > 0.10:
            result.verdict = "STRONG"
        else:
            result.verdict = "PROMISING"
    elif len(failures) == 1:
        result.verdict = "UNPROVEN"
    else:
        result.verdict = "REJECTED"

    return result


def format_promotion_result(r: PromotionResult) -> str:
    """Format promotion result for display."""
    lines = []
    lines.append(f"  Family: {r.family} | Symbol: {r.symbol}")
    lines.append(f"  Verdict: {r.verdict} ({r.passed_criteria}/9 criteria passed)")
    lines.append("")
    lines.append(f"  OOS trades: {r.total_trades_oos}")
    lines.append(f"  OOS win rate: {r.win_rate_oos:.1%}")
    lines.append(f"  OOS PF: {r.profit_factor_oos:.2f}")
    lines.append(f"  OOS expectancy (net): ${r.expectancy_after_cost_oos:.4f}")
    lines.append(f"  OOS avg win: ${r.avg_win_oos:.4f}")
    lines.append(f"  OOS avg loss: ${r.avg_loss_oos:.4f}")
    lines.append(f"  OOS max DD: ${r.max_drawdown_oos:.2f} ({r.max_drawdown_pct_oos:.1%})")
    lines.append(f"  Trades/month: {r.trades_per_month_oos:.1f}")
    lines.append(f"  WF folds: {r.wf_folds_profitable}/{r.wf_folds_total}")
    lines.append(f"  Cost stress 1.5x: {'PASS' if r.cost_stress_1_5x_positive else 'FAIL'}")
    lines.append(f"  Robustness: {r.robustness_positive_count}/{r.robustness_total}")
    lines.append(f"  MC 95th DD: ${r.mc_95th_dd:.2f}")
    lines.append(f"  E after removing top 1%: ${r.expectancy_after_top1pct:.4f}")
    lines.append(f"  E after removing top 5%: ${r.expectancy_after_top5pct:.4f}")
    lines.append(f"  E after removing top 10%: ${r.expectancy_after_top10pct:.4f}")

    if r.failures:
        lines.append("")
        lines.append("  FAILURES:")
        for f in r.failures:
            lines.append(f"    - {f}")

    return "\n".join(lines)
