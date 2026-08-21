"""Backtest runner — main orchestrator for Phase 2 research.

Runs the complete pipeline:
1. Fetch historical data
2. Run strategy through backtest engine
3. Compute baseline stats
4. Run walk-forward validation
5. Run ablation tests
6. Generate report

Usage:
    python -m bot.research.backtest_runner --symbol EURUSD --bars 5000
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bot.research.data_pipeline import (
    load_or_fetch, apply_spread_model, SymbolData, Bar,
    fetch_mt5_bars, fetch_symbol_metadata, _detect_pip,
)
from bot.research.cost_model import CostModel, CostScenario, get_cost_scenarios
from bot.research.execution_model import BacktestEngine, BacktestTrade
from bot.research.strategy_adapter import make_strategy_adapter, make_ablation_adapter
from bot.research.analysis import (
    compute_baseline_stats, analyze_by_session, analyze_by_direction,
    analyze_by_month, analyze_by_exit_reason, analyze_by_regime,
    classify_regime_simple,
)
from bot.research.telemetry import Telemetry
from bot.research.report import generate_baseline_report, generate_comparison_report
from bot.research.walk_forward import WalkForwardEngine
from bot.strategies.scalp import PROFILES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_baseline(
    symbol: str = "EURUSD",
    n_bars: int = 5000,
    mt5_path: str = None,
    output_dir: str = "research_output",
    strategy_params: dict = None,
) -> dict:
    """Run complete baseline analysis for a symbol.

    Returns dict with all results.
    """
    output = Path(output_dir)
    output.mkdir(exist_ok=True)

    logger.info("=" * 60)
    logger.info("  PHASE 2 BASELINE ANALYSIS — %s", symbol)
    logger.info("=" * 60)

    # 1. Fetch data
    logger.info("Step 1: Fetching historical data...")
    data = load_or_fetch(symbol, timeframe=5, n_bars=n_bars, mt5_path=mt5_path)
    if data is None or len(data) < 100:
        logger.error("Insufficient data for %s (got %d bars)", symbol, len(data) if data else 0)
        return {"error": "insufficient_data"}

    logger.info("  Loaded %d bars from %s to %s",
                len(data), data.bars[0].timestamp, data.bars[-1].timestamp)

    # 2. Create cost models
    logger.info("Step 2: Creating cost models...")
    profile = PROFILES.get(symbol, PROFILES["EURUSD"])
    cost_scenarios = get_cost_scenarios(
        symbol=symbol,
        pip=profile["pip"],
        point=data.point,
        contract_size=data.contract_size,
        base_spread_points=data.base_spread_points,
    )

    # 3. Create strategy adapter
    logger.info("Step 3: Creating strategy adapter...")
    params = strategy_params or {}
    strategy_fn = make_strategy_adapter(symbol=symbol, **params)

    # 4. Run baseline at REALISTIC cost
    logger.info("Step 4: Running baseline backtest (REALISTIC costs)...")
    engine = BacktestEngine(
        initial_equity=10000.0,
        max_positions=1,
        max_hold_bars=48,
        cost_scenario=CostScenario.REALISTIC,
    )
    trades_realistic = engine.run(data, strategy_fn, cost_scenarios[CostScenario.REALISTIC])
    stats_realistic = compute_baseline_stats(trades_realistic)
    logger.info("  Trades: %d, Win Rate: %.1f%%, PF: %.2f, Net PnL: $%.2f",
                stats_realistic.get("total_trades", 0),
                stats_realistic.get("win_rate", 0) * 100,
                stats_realistic.get("profit_factor", 0),
                stats_realistic.get("total_pnl_after_cost", 0))

    # 5. Run at BASE cost
    logger.info("Step 5: Running baseline backtest (BASE costs)...")
    engine_base = BacktestEngine(
        initial_equity=10000.0,
        max_positions=1,
        max_hold_bars=48,
        cost_scenario=CostScenario.BASE,
    )
    trades_base = engine_base.run(data, strategy_fn, cost_scenarios[CostScenario.BASE])
    stats_base = compute_baseline_stats(trades_base)

    # 6. Run at STRESSED cost
    logger.info("Step 6: Running baseline backtest (STRESSED costs)...")
    engine_stressed = BacktestEngine(
        initial_equity=10000.0,
        max_positions=1,
        max_hold_bars=48,
        cost_scenario=CostScenario.STRESSED,
    )
    trades_stressed = engine_stressed.run(data, strategy_fn, cost_scenarios[CostScenario.STRESSED])
    stats_stressed = compute_baseline_stats(trades_stressed)

    # 7. Generate report
    logger.info("Step 7: Generating reports...")
    report = generate_baseline_report(
        trades_realistic, symbol, "REALISTIC",
    )
    report_path = output / f"baseline_{symbol}.md"
    report_path.write_text(report)
    logger.info("  Report saved to %s", report_path)

    # Cost comparison
    comparison = generate_comparison_report(stats_base, stats_stressed, symbol)
    comparison_path = output / f"cost_comparison_{symbol}.md"
    comparison_path.write_text(comparison)

    # 8. Walk-forward
    logger.info("Step 8: Running walk-forward validation...")
    wf_engine = WalkForwardEngine(n_folds=5)
    wf_result = wf_engine.run(data, strategy_fn, cost_scenarios[CostScenario.REALISTIC])

    wf_report = _format_walk_forward(wf_result)
    wf_path = output / f"walk_forward_{symbol}.md"
    wf_path.write_text(wf_report)

    # 9. Session analysis
    logger.info("Step 9: Running session analysis...")
    session_stats = analyze_by_session(trades_realistic)

    # 10. Direction analysis
    dir_stats = analyze_by_direction(trades_realistic)

    # 11. Exit reason analysis
    reason_stats = analyze_by_exit_reason(trades_realistic)

    # 12. Save trades
    trades_data = []
    for t in trades_realistic:
        trades_data.append({
            "symbol": t.symbol,
            "side": t.side,
            "lots": t.lots,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "entry_time": t.entry_time.isoformat(),
            "exit_time": t.exit_time.isoformat(),
            "pnl": t.pnl,
            "pnl_after_cost": t.pnl_after_cost,
            "r_multiple": t.r_multiple,
            "bars_held": t.bars_held,
            "mfe": t.mfe,
            "mae": t.maе,
            "exit_reason": t.exit_reason,
            "cost_total": t.cost.total_round_trip,
        })
    trades_path = output / f"trades_{symbol}.json"
    trades_path.write_text(json.dumps(trades_data, indent=2))

    # Summary
    result = {
        "symbol": symbol,
        "bars": len(data),
        "date_range": f"{data.bars[0].timestamp} to {data.bars[-1].timestamp}",
        "baseline_realistic": stats_realistic,
        "baseline_base": stats_base,
        "baseline_stressed": stats_stressed,
        "walk_forward": {
            "n_folds": wf_result.n_folds,
            "pooled_oos_trades": wf_result.pooled_oos_trades,
            "pooled_oos_win_rate": wf_result.pooled_oos_win_rate,
            "pooled_oos_expectancy": wf_result.pooled_oos_expectancy,
            "pooled_oos_profit_factor": wf_result.pooled_oos_profit_factor,
            "folds_profitable": wf_result.folds_profitable,
            "robustness_score": wf_result.robustness_score,
        },
        "sessions": {k: {"trades": v.trade_count, "wr": v.win_rate, "e": v.expectancy}
                     for k, v in session_stats.items()},
        "directions": {k: {"trades": v.trade_count, "wr": v.win_rate, "e": v.expectancy}
                       for k, v in dir_stats.items()},
    }

    summary_path = output / f"summary_{symbol}.json"
    summary_path.write_text(json.dumps(result, indent=2))

    logger.info("=" * 60)
    logger.info("  BASELINE ANALYSIS COMPLETE")
    logger.info("  Verdict: UNPROVEN")
    logger.info("  See %s for details", report_path)
    logger.info("=" * 60)

    return result


def run_ablation(
    symbol: str = "EURUSD",
    n_bars: int = 5000,
    mt5_path: str = None,
    output_dir: str = "research_output",
) -> dict:
    """Run ablation tests — measure each component's contribution."""
    output = Path(output_dir)
    output.mkdir(exist_ok=True)

    logger.info("=" * 60)
    logger.info("  ABLATION TESTS — %s", symbol)
    logger.info("=" * 60)

    # Fetch data
    data = load_or_fetch(symbol, timeframe=5, n_bars=n_bars, mt5_path=mt5_path)
    if data is None or len(data) < 100:
        return {"error": "insufficient_data"}

    profile = PROFILES.get(symbol, PROFILES["EURUSD"])
    cost_model = CostModel(
        symbol=symbol,
        pip=profile["pip"],
        point=data.point,
        contract_size=data.contract_size,
        base_spread_points=data.base_spread_points,
    )

    # Component sets to test
    component_sets = {
        "full": ["ema", "rsi", "momentum", "patterns", "trend_filter"],
        "ema_rsi_mom_pat": ["ema", "rsi", "momentum", "patterns"],
        "ema_rsi_mom": ["ema", "rsi", "momentum"],
        "ema_rsi": ["ema", "rsi"],
        "ema_only": ["ema"],
        "patterns_only": ["patterns"],
        "full_no_trend": ["ema", "rsi", "momentum", "patterns"],
    }

    results = {}
    for name, components in component_sets.items():
        logger.info("  Testing: %s (%s)", name, "+".join(components))
        adapter = make_ablation_adapter(symbol=symbol, components=components)
        engine = BacktestEngine(
            initial_equity=10000.0,
            max_positions=1,
            max_hold_bars=48,
            cost_scenario=CostScenario.REALISTIC,
        )
        trades = engine.run(data, adapter, cost_model)
        stats = compute_baseline_stats(trades)
        results[name] = {
            "components": components,
            "trades": stats.get("total_trades", 0),
            "win_rate": stats.get("win_rate", 0),
            "expectancy": stats.get("expectancy_after_cost", 0),
            "profit_factor": stats.get("profit_factor", 0),
            "total_pnl": stats.get("total_pnl_after_cost", 0),
        }
        logger.info("    -> %d trades, WR=%.1f%%, PF=%.2f, Net=$%.2f",
                     stats.get("total_trades", 0),
                     stats.get("win_rate", 0) * 100,
                     stats.get("profit_factor", 0),
                     stats.get("total_pnl_after_cost", 0))

    # Save ablation results
    ablation_path = output / f"ablation_{symbol}.json"
    ablation_path.write_text(json.dumps(results, indent=2))

    # Generate comparison report
    lines = []
    lines.append("=" * 70)
    lines.append(f"  ABLATION TEST RESULTS — {symbol}")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"  {'Config':20s} {'Trades':>8s} {'WR':>8s} {'PF':>8s} {'Net PnL':>12s}")
    lines.append("  " + "-" * 56)
    for name, r in results.items():
        lines.append(f"  {name:20s} {r['trades']:8d} {r['win_rate']:8.1%} "
                      f"{r['profit_factor']:8.2f} ${r['total_pnl']:11.2f}")
    lines.append("")

    # Identify what contributes
    full_exp = results.get("full", {}).get("expectancy", 0)
    no_trend_exp = results.get("full_no_trend", {}).get("expectancy", 0)
    trend_contribution = full_exp - no_trend_exp

    lines.append("  CONTRIBUTION ANALYSIS:")
    for name, r in results.items():
        if name == "full":
            continue
        base_exp = results.get("full", {}).get("expectancy", 0) - r.get("expectancy", 0)
        lines.append(f"    Removing {name}: delta=${base_exp:.2f}/trade")

    lines.append("")
    lines.append(f"  Trend filter contribution: ${trend_contribution:.2f}/trade")
    lines.append("=" * 70)

    ablation_report = "\n".join(lines)
    report_path = output / f"ablation_report_{symbol}.md"
    report_path.write_text(ablation_report)
    logger.info("  Ablation report saved to %s", report_path)

    return results


def _format_walk_forward(result) -> str:
    """Format walk-forward result as markdown."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"  WALK-FORWARD VALIDATION — {result.symbol}")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"  Folds: {result.n_folds}")
    lines.append(f"  Profitable Folds: {result.folds_profitable}/{result.n_folds}")
    lines.append(f"  Overfit Folds: {result.folds_overfit}/{result.n_folds}")
    lines.append(f"  Robustness Score: {result.robustness_score:.2f}")
    lines.append("")
    lines.append("  POOLED OOS RESULTS:")
    lines.append(f"    Trades:            {result.pooled_oos_trades}")
    lines.append(f"    Win Rate:          {result.pooled_oos_win_rate:.1%}")
    lines.append(f"    Expectancy:        ${result.pooled_oos_expectancy:.2f}")
    lines.append(f"    Profit Factor:     {result.pooled_oos_profit_factor:.2f}")
    lines.append(f"    Total PnL:         ${result.pooled_oos_total_pnl:.2f}")
    lines.append(f"    Max Drawdown:      ${result.pooled_oos_max_dd:.2f}")
    lines.append("")
    lines.append("  FOLD DETAILS:")
    lines.append(f"  {'Fold':>6s} {'IS Trades':>10s} {'IS Exp':>10s} {'OOS Trades':>11s} {'OOS Exp':>10s} {'Overfit':>8s}")
    lines.append("  " + "-" * 60)
    for f in result.folds:
        overfit_flag = "YES" if f.is_overfit else ""
        lines.append(f"  {f.fold:6d} {f.is_trades:10d} ${f.is_expectancy:9.2f} "
                      f"{f.oos_trades:11d} ${f.oos_expectancy:9.2f} {overfit_flag:>8s}")
    lines.append("")
    lines.append("=" * 70)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Phase 2 Backtest Runner")
    parser.add_argument("--symbol", default="EURUSD", help="Symbol to test")
    parser.add_argument("--bars", type=int, default=5000, help="Number of bars")
    parser.add_argument("--output", default="research_output", help="Output directory")
    parser.add_argument("--ablation", action="store_true", help="Run ablation tests")
    args = parser.parse_args()

    result = run_baseline(
        symbol=args.symbol,
        n_bars=args.bars,
        output_dir=args.output,
    )

    if args.ablation:
        run_ablation(
            symbol=args.symbol,
            n_bars=args.bars,
            output_dir=args.output,
        )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
