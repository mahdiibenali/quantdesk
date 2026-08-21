"""Phase 3 tournament runner — sequential family testing with full rigor.

Runs each strategy family through:
1. Data fetch + split
2. In-sample research
3. Walk-forward validation
4. Parameter robustness
5. Cost stress
6. Monte Carlo
7. Final OOS
8. Promotion criteria check
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path

import numpy as np

from ..data_pipeline import load_or_fetch
from ..cost_model import CostModel, CostScenario
from ..execution_model import BacktestEngine, BacktestTrade
from ..analysis import compute_baseline_stats
from .data_splits import split_data, DataSplits
from .scoring import check_promotion, format_promotion_result, PromotionResult

# Strategy family imports
from .trend_continuation import make_trend_continuation
from .pullback import make_pullback
from .breakout import make_breakout
from .mean_reversion import make_mean_reversion
from .volatility_transition import make_volatility_transition

logger = logging.getLogger(__name__)

OUTPUT = Path("research_output")

FAMILIES = {
    "trend_continuation": make_trend_continuation,
    "pullback": make_pullback,
    "breakout": make_breakout,
    "mean_reversion": make_mean_reversion,
    "volatility_transition": make_volatility_transition,
}


def run_backtest(data, strategy_fn, cost_model, initial_equity=10000, max_hold_bars=72):
    """Run backtest and return trades."""
    engine = BacktestEngine(
        initial_equity=initial_equity,
        max_positions=1,
        max_hold_bars=max_hold_bars,
    )
    return engine.run(data, strategy_fn, cost_model)


def run_walk_forward(splits: DataSplits, strategy_fn, cost_model) -> list[dict]:
    """Run walk-forward validation across folds."""
    results = []
    for i, (train_fold, oos_fold) in enumerate(splits.wf_folds):
        trades = run_backtest(oos_fold, strategy_fn, cost_model)
        stats = compute_baseline_stats(trades)
        results.append({
            "fold": i,
            "trades": stats.get("total_trades", 0),
            "win_rate": stats.get("win_rate", 0),
            "profit_factor": stats.get("profit_factor", 0),
            "expectancy": stats.get("expectancy", 0),
            "expectancy_after_cost": stats.get("expectancy_after_cost", 0),
            "total_pnl": stats.get("total_pnl_after_cost", 0),
        })
    return results


def run_cost_stress(data, strategy_fn, base_cost: CostModel, symbol: str) -> dict:
    """Run backtest at different cost stress levels."""
    results = {}
    for label, mult in [("1.0x", 1.0), ("1.25x", 1.25), ("1.5x", 1.5), ("2.0x", 2.0)]:
        stressed = CostModel(
            symbol=symbol,
            pip=base_cost.pip,
            point=base_cost.point,
            contract_size=base_cost.contract_size,
            base_spread_points=base_cost.base_spread_points * mult,
        )
        trades = run_backtest(data, strategy_fn, stressed)
        stats = compute_baseline_stats(trades)
        results[label] = {
            "spread_mult": mult,
            "trades": stats.get("total_trades", 0),
            "pf": stats.get("profit_factor", 0),
            "expectancy": stats.get("expectancy_after_cost", 0),
            "pnl_net": stats.get("total_pnl_after_cost", 0),
        }
    return results


def run_robustness(data, strategy_fn, cost_model, symbol: str, family_fn, param_ranges: dict) -> list[dict]:
    """Run parameter robustness test: perturb each parameter +/-20%."""
    results = []

    # Get default kwargs from the family function
    import inspect
    sig = inspect.signature(family_fn)
    default_kwargs = {
        k: v.default for k, v in sig.parameters.items()
        if v.default is not inspect.Parameter.empty and k != "symbol"
    }

    for param_name, default_val in param_ranges.items():
        if param_name not in default_kwargs:
            continue

        for direction, factor in [(-0.8, 0.8), (1.2, 1.2)]:
            perturbed_kwargs = default_kwargs.copy()
            if isinstance(default_val, (int, float)):
                perturbed_kwargs[param_name] = default_val * factor
            else:
                continue

            try:
                perturbed_fn = family_fn(symbol=symbol, **perturbed_kwargs)
                trades = run_backtest(data, perturbed_fn, cost_model)
                stats = compute_baseline_stats(trades)
                results.append({
                    "param": param_name,
                    "direction": "down" if factor < 1 else "up",
                    "value": perturbed_kwargs[param_name],
                    "trades": stats.get("total_trades", 0),
                    "expectancy_after_cost": stats.get("expectancy_after_cost", 0),
                    "pf": stats.get("profit_factor", 0),
                })
            except Exception as e:
                logger.debug("Robustness test failed: %s %s: %s", param_name, direction, e)

    return results


def run_monte_carlo(trades: list[BacktestTrade], n_resamples: int = 1000, initial_equity: float = 10000) -> dict:
    """Monte Carlo resampling of trades to estimate drawdown distribution."""
    if not trades:
        return {"mc_95th_dd": initial_equity}

    pnls = [t.pnl_after_cost for t in trades]
    max_dds = []

    for _ in range(n_resamples):
        sampled = random.choices(pnls, k=len(pnls))
        equity = initial_equity
        peak = initial_equity
        max_dd = 0
        for pnl in sampled:
            equity += pnl
            if equity > peak:
                peak = equity
            dd = peak - equity
            max_dd = max(max_dd, dd)
        max_dds.append(max_dd)

    return {
        "mc_mean_dd": np.mean(max_dds),
        "mc_95th_dd": np.percentile(max_dds, 95),
        "mc_99th_dd": np.percentile(max_dds, 99),
        "mc_max_dd": np.max(max_dds),
    }


def run_subset_analysis(trades: list[BacktestTrade]) -> dict:
    """Analyze performance by session, direction, regime."""
    results = {}

    # By session
    for sess_name, h_start, h_end in [("asian", 0, 7), ("london", 7, 13),
                                        ("overlap", 13, 17), ("new_york", 17, 22)]:
        sess_trades = [t for t in trades if h_start <= t.entry_time.hour < h_end]
        if sess_trades:
            stats = compute_baseline_stats(sess_trades)
            results[f"session_{sess_name}"] = {
                "n": stats["total_trades"],
                "e": stats["expectancy_after_cost"],
                "pf": stats["profit_factor"],
            }

    # By direction
    for d in ["BUY", "SELL"]:
        dir_trades = [t for t in trades if t.side == d]
        if dir_trades:
            stats = compute_baseline_stats(dir_trades)
            results[f"dir_{d}"] = {
                "n": stats["total_trades"],
                "e": stats["expectancy_after_cost"],
                "pf": stats["profit_factor"],
            }

    return results


def test_family(
    family_name: str,
    symbol: str,
    data_splits: DataSplits,
    family_fn,
    param_ranges: dict,
) -> tuple[PromotionResult, dict]:
    """Run full pipeline for one family on one symbol.

    Returns (PromotionResult, full_report_dict).
    """
    logger.info("=" * 60)
    logger.info("TESTING FAMILY: %s on %s", family_name, symbol)
    logger.info("=" * 60)

    report = {"family": family_name, "symbol": symbol}

    # Create strategy adapter
    strategy_fn = family_fn(symbol=symbol)

    # Create cost model from training data
    cost_model = CostModel(
        symbol=symbol,
        pip=data_splits.train.pip,
        point=data_splits.train.point,
        contract_size=data_splits.train.contract_size,
        base_spread_points=data_splits.train.base_spread_points,
    )

    # 1. In-sample research (on train set)
    logger.info("  Step 1: In-sample research...")
    is_trades = run_backtest(data_splits.train, strategy_fn, cost_model)
    is_stats = compute_baseline_stats(is_trades)
    report["in_sample"] = {
        "trades": is_stats.get("total_trades", 0),
        "wr": is_stats.get("win_rate", 0),
        "pf": is_stats.get("profit_factor", 0),
        "e": is_stats.get("expectancy_after_cost", 0),
    }
    logger.info("  IS: n=%d, WR=%.1f%%, PF=%.2f, E=$%.4f",
                is_stats.get("total_trades", 0), is_stats.get("win_rate", 0) * 100,
                is_stats.get("profit_factor", 0), is_stats.get("expectancy_after_cost", 0))

    # 2. Walk-forward validation
    logger.info("  Step 2: Walk-forward validation...")
    wf_results = run_walk_forward(data_splits, strategy_fn, cost_model)
    wf_profitable = sum(1 for r in wf_results if r["expectancy_after_cost"] > 0)
    report["walk_forward"] = wf_results
    logger.info("  WF: %d/%d folds profitable", wf_profitable, len(wf_results))

    # 3. Parameter robustness
    logger.info("  Step 3: Parameter robustness...")
    robustness = run_robustness(data_splits.val, strategy_fn, cost_model, symbol, family_fn, param_ranges)
    rob_positive = sum(1 for r in robustness if r["expectancy_after_cost"] > 0)
    report["robustness"] = {"results": robustness, "positive": rob_positive, "total": len(robustness)}
    logger.info("  Robustness: %d/%d positive", rob_positive, len(robustness))

    # 4. Cost stress (on val set)
    logger.info("  Step 4: Cost stress...")
    cost_stress = run_cost_stress(data_splits.val, strategy_fn, cost_model, symbol)
    report["cost_stress"] = cost_stress
    logger.info("  Cost stress 1.5x: E=$%.4f", cost_stress.get("1.5x", {}).get("expectancy", 0))

    # 5. Monte Carlo
    logger.info("  Step 5: Monte Carlo...")
    mc = run_monte_carlo(is_trades)  # Use IS trades for MC distribution
    report["monte_carlo"] = mc
    logger.info("  MC 95th DD: $%.2f", mc["mc_95th_dd"])

    # 6. Subset analysis
    logger.info("  Step 6: Subset analysis...")
    subsets = run_subset_analysis(is_trades)
    report["subsets"] = subsets

    # 7. FINAL OOS (locked - touched once)
    logger.info("  Step 7: FINAL OOS (locked)...")
    oos_trades = run_backtest(data_splits.test, strategy_fn, cost_model)
    oos_stats = compute_baseline_stats(oos_trades)
    report["oos"] = {
        "trades": oos_stats.get("total_trades", 0),
        "wr": oos_stats.get("win_rate", 0),
        "pf": oos_stats.get("profit_factor", 0),
        "e": oos_stats.get("expectancy_after_cost", 0),
    }
    logger.info("  OOS: n=%d, WR=%.1f%%, PF=%.2f, E=$%.4f",
                oos_stats.get("total_trades", 0), oos_stats.get("win_rate", 0) * 100,
                oos_stats.get("profit_factor", 0), oos_stats.get("expectancy_after_cost", 0))

    # 8. Promotion criteria check
    logger.info("  Step 8: Checking promotion criteria...")
    promotion = check_promotion(
        family=family_name,
        symbol=symbol,
        oos_trades=oos_trades,
        wf_results=wf_results,
        cost_stress_results=cost_stress,
        robustness_results=robustness,
        mc_95th_dd=mc["mc_95th_dd"],
        data_days=data_splits.test_days,
    )

    report["promotion"] = {
        "verdict": promotion.verdict,
        "passed": promotion.passed_criteria,
        "failed": promotion.failed_criteria,
        "failures": promotion.failures,
    }

    logger.info("  VERDICT: %s (%d/9 criteria passed)", promotion.verdict, promotion.passed_criteria)
    if promotion.failures:
        for f in promotion.failures:
            logger.info("    FAIL: %s", f)

    return promotion, report


def run_tournament(
    symbols: list[str] | None = None,
    n_bars: int = 5000,
) -> dict:
    """Run full Phase 3 tournament.

    Sequential testing: one family at a time, full rigor per family.
    """
    if symbols is None:
        symbols = ["USDJPY"]  # Priority #1

    import MetaTrader5 as mt5
    if mt5.account_info() is None:
        mt5.initialize()

    all_results = {}
    all_promotions = {}

    for symbol in symbols:
        logger.info("")
        logger.info("#" * 70)
        logger.info("  SYMBOL: %s", symbol)
        logger.info("#" * 70)

        # Fetch data
        data = load_or_fetch(symbol, timeframe=MT5_TIMEFRAME_H1, n_bars=n_bars)
        if data is None:
            logger.error("Failed to fetch data for %s", symbol)
            continue

        # Split data
        splits = split_data(data)

        symbol_results = {}
        symbol_promotions = {}

        for family_name, family_fn in FAMILIES.items():
            # Parameter ranges for robustness testing
            param_ranges = _get_param_ranges(family_name)

            promotion, report = test_family(
                family_name=family_name,
                symbol=symbol,
                data_splits=splits,
                family_fn=family_fn,
                param_ranges=param_ranges,
            )

            symbol_results[family_name] = report
            symbol_promotions[family_name] = promotion

            # Print result
            print(format_promotion_result(promotion))
            print()

        all_results[symbol] = symbol_results
        all_promotions[symbol] = symbol_promotions

    # Save results
    OUTPUT.mkdir(exist_ok=True)

    # Summary
    summary_lines = []
    summary_lines.append("=" * 70)
    summary_lines.append("  PHASE 3 TOURNAMENT RESULTS")
    summary_lines.append("=" * 70)
    summary_lines.append("")

    for symbol, promos in all_promotions.items():
        summary_lines.append(f"  {symbol}:")
        for family, promo in promos.items():
            summary_lines.append(f"    {family:25s}: {promo.verdict} ({promo.passed_criteria}/9)")
        summary_lines.append("")

    # Count survivors
    survivors = []
    for symbol, promos in all_promotions.items():
        for family, promo in promos.items():
            if promo.verdict in ("PROMISING", "STRONG"):
                survivors.append((symbol, family, promo.verdict))

    summary_lines.append(f"  Survivors: {len(survivors)}")
    for sym, fam, verdict in survivors:
        summary_lines.append(f"    {sym}/{fam}: {verdict}")

    if not survivors:
        summary_lines.append("  No families survived. H1 forex is structurally untradeable.")

    summary = "\n".join(summary_lines)
    print(summary)

    (OUTPUT / "phase3_tournament_summary.md").write_text(summary)
    with open(OUTPUT / "phase3_tournament_data.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    logger.info("Results saved to %s/", OUTPUT)

    mt5.shutdown()
    return all_results


def _get_param_ranges(family_name: str) -> dict:
    """Get parameter ranges for robustness testing per family."""
    if family_name == "trend_continuation":
        return {
            "ema_fast": 8,
            "ema_slow": 21,
            "adx_threshold": 20.0,
            "stop_atr_mult": 1.5,
            "target_atr_mult": 2.5,
        }
    elif family_name == "pullback":
        return {
            "ema_fast": 8,
            "ema_slow": 21,
            "pullback_min_pct": 0.3,
            "pullback_max_pct": 0.7,
            "stop_atr_mult": 1.5,
        }
    elif family_name == "breakout":
        return {
            "bb_period": 20,
            "compression_threshold_pct": 0.5,
            "atr_expansion_mult": 1.3,
            "stop_atr_mult": 2.0,
            "target_atr_mult": 3.0,
        }
    elif family_name == "mean_reversion":
        return {
            "ema_period": 20,
            "adx_max": 25.0,
            "extreme_atr_mult": 1.5,
            "stop_atr_mult": 1.5,
            "target_atr_mult": 2.0,
        }
    elif family_name == "volatility_transition":
        return {
            "bb_period": 20,
            "atr_expansion_mult": 1.2,
            "stop_atr_mult": 2.0,
            "target_atr_mult": 3.0,
            "ema_fast": 10,
        }
    return {}


# MT5 H1 constant
MT5_TIMEFRAME_H1 = 16385


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_tournament()
