"""Focused test: fixed breakout + loosened volatility transition on USDJPY H1."""

from __future__ import annotations
import logging
import numpy as np
from bot.research.data_pipeline import load_or_fetch, SymbolData
from bot.research.cost_model import CostModel
from bot.research.execution_model import BacktestEngine, BacktestState
from bot.research.analysis import compute_baseline_stats
from bot.research.phase3.data_splits import split_data

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

MT5_TIMEFRAME_H1 = 16385


def test_strategy(name, strategy_fn, data, cost_model, min_bars=60):
    """Test a strategy and return stats."""
    engine = BacktestEngine(initial_equity=10000, max_positions=1, max_hold_bars=72)
    trades = engine.run(data, strategy_fn, cost_model)
    stats = compute_baseline_stats(trades)
    return trades, stats


def main():
    import MetaTrader5 as mt5
    mt5.initialize()

    data = load_or_fetch("USDJPY", timeframe=MT5_TIMEFRAME_H1, n_bars=5000)
    if data is None:
        print("ERROR: no data")
        return

    splits = split_data(data)
    cost = CostModel(
        symbol="USDJPY", pip=data.pip, point=data.point,
        contract_size=data.contract_size,
        base_spread_points=data.base_spread_points,
    )

    print(f"Data: {len(data)} bars, train={len(splits.train)}, val={len(splits.val)}, test={len(splits.test)}")
    print()

    # ================================================================
    # TEST 1: Fixed Breakout (original params)
    # ================================================================
    print("=" * 60)
    print("TEST 1: Breakout (FIXED, original params)")
    print("=" * 60)

    from bot.research.phase3.breakout import make_breakout
    breakout_fn = make_breakout("USDJPY")

    # Count signals across full dataset
    signal_count = 0
    for i in range(60, len(data.bars)):
        slice_data = SymbolData(
            symbol="USDJPY", bars=data.bars[:i+1],
            pip=data.pip, contract_size=data.contract_size,
            point=data.point, volume_step=data.volume_step,
            volume_min=data.volume_min, volume_max=data.volume_max,
            base_spread_points=data.base_spread_points,
        )
        state = BacktestState(equity=10000, initial_equity=10000, peak_equity=10000,
                              positions=[], closed_trades=[], bar_idx=i)
        result = breakout_fn(slice_data, state)
        if result is not None:
            signal_count += 1

    print(f"  Signals generated (full data): {signal_count}")

    trades_is, stats_is = test_strategy("breakout", breakout_fn, splits.train, cost)
    print(f"  IS: n={stats_is.get('total_trades', 0)}, WR={stats_is.get('win_rate', 0):.1%}, "
          f"PF={stats_is.get('profit_factor', 0):.2f}, E=${stats_is.get('expectancy_after_cost', 0):.4f}")

    trades_oos, stats_oos = test_strategy("breakout", breakout_fn, splits.test, cost)
    print(f"  OOS: n={stats_oos.get('total_trades', 0)}, WR={stats_oos.get('win_rate', 0):.1%}, "
          f"PF={stats_oos.get('profit_factor', 0):.2f}, E=${stats_oos.get('expectancy_after_cost', 0):.4f}")

    # ================================================================
    # TEST 2: Breakout with loosened params
    # ================================================================
    print()
    print("=" * 60)
    print("TEST 2: Breakout (loosened: compression_threshold=0.8, min_score=0.05)")
    print("=" * 60)

    breakout_loose = make_breakout("USDJPY", compression_threshold_pct=0.8, min_score=0.05)

    signal_count_loose = 0
    for i in range(60, len(data.bars)):
        slice_data = SymbolData(
            symbol="USDJPY", bars=data.bars[:i+1],
            pip=data.pip, contract_size=data.contract_size,
            point=data.point, volume_step=data.volume_step,
            volume_min=data.volume_min, volume_max=data.volume_max,
            base_spread_points=data.base_spread_points,
        )
        state = BacktestState(equity=10000, initial_equity=10000, peak_equity=10000,
                              positions=[], closed_trades=[], bar_idx=i)
        result = breakout_loose(slice_data, state)
        if result is not None:
            signal_count_loose += 1

    print(f"  Signals generated (full data): {signal_count_loose}")

    trades_is2, stats_is2 = test_strategy("breakout_loose", breakout_loose, splits.train, cost)
    print(f"  IS: n={stats_is2.get('total_trades', 0)}, WR={stats_is2.get('win_rate', 0):.1%}, "
          f"PF={stats_is2.get('profit_factor', 0):.2f}, E=${stats_is2.get('expectancy_after_cost', 0):.4f}")

    trades_oos2, stats_oos2 = test_strategy("breakout_loose", breakout_loose, splits.test, cost)
    print(f"  OOS: n={stats_oos2.get('total_trades', 0)}, WR={stats_oos2.get('win_rate', 0):.1%}, "
          f"PF={stats_oos2.get('profit_factor', 0):.2f}, E=${stats_oos2.get('expectancy_after_cost', 0):.4f}")

    # ================================================================
    # TEST 3: Volatility Transition (original — 7 trades)
    # ================================================================
    print()
    print("=" * 60)
    print("TEST 3: Volatility Transition (original params)")
    print("=" * 60)

    from bot.research.phase3.volatility_transition import make_volatility_transition
    vt_fn = make_volatility_transition("USDJPY")

    trades_vt, stats_vt = test_strategy("vt", vt_fn, splits.train, cost)
    print(f"  IS: n={stats_vt.get('total_trades', 0)}, WR={stats_vt.get('win_rate', 0):.1%}, "
          f"PF={stats_vt.get('profit_factor', 0):.2f}, E=${stats_vt.get('expectancy_after_cost', 0):.4f}")

    trades_vt_oos, stats_vt_oos = test_strategy("vt", vt_fn, splits.test, cost)
    print(f"  OOS: n={stats_vt_oos.get('total_trades', 0)}, WR={stats_vt_oos.get('win_rate', 0):.1%}, "
          f"PF={stats_vt_oos.get('profit_factor', 0):.2f}, E=${stats_vt_oos.get('expectancy_after_cost', 0):.4f}")

    # ================================================================
    # TEST 4: Volatility Transition (loosened — lower thresholds)
    # ================================================================
    print()
    print("=" * 60)
    print("TEST 4: Volatility Transition (loosened: bw_pct=0.85, atr_mult=1.0, min_score=0.05)")
    print("=" * 60)

    vt_loose = make_volatility_transition(
        "USDJPY",
        bw_compression_pct=0.85,
        atr_expansion_mult=1.0,
        min_score=0.05,
    )

    signal_count_vt = 0
    for i in range(60, len(data.bars)):
        slice_data = SymbolData(
            symbol="USDJPY", bars=data.bars[:i+1],
            pip=data.pip, contract_size=data.contract_size,
            point=data.point, volume_step=data.volume_step,
            volume_min=data.volume_min, volume_max=data.volume_max,
            base_spread_points=data.base_spread_points,
        )
        state = BacktestState(equity=10000, initial_equity=10000, peak_equity=10000,
                              positions=[], closed_trades=[], bar_idx=i)
        result = vt_loose(slice_data, state)
        if result is not None:
            signal_count_vt += 1

    print(f"  Signals generated (full data): {signal_count_vt}")

    trades_vt2, stats_vt2 = test_strategy("vt_loose", vt_loose, splits.train, cost)
    print(f"  IS: n={stats_vt2.get('total_trades', 0)}, WR={stats_vt2.get('win_rate', 0):.1%}, "
          f"PF={stats_vt2.get('profit_factor', 0):.2f}, E=${stats_vt2.get('expectancy_after_cost', 0):.4f}")

    trades_vt2_oos, stats_vt2_oos = test_strategy("vt_loose", vt_loose, splits.test, cost)
    print(f"  OOS: n={stats_vt2_oos.get('total_trades', 0)}, WR={stats_vt2_oos.get('win_rate', 0):.1%}, "
          f"PF={stats_vt2_oos.get('profit_factor', 0):.2f}, E=${stats_vt2_oos.get('expectancy_after_cost', 0):.4f}")

    # ================================================================
    # TEST 5: Volatility Transition (very loose — just to get sample)
    # ================================================================
    print()
    print("=" * 60)
    print("TEST 5: Volatility Transition (very loose: bw_pct=0.9, atr_mult=0.9, min_score=0.01)")
    print("=" * 60)

    vt_vloose = make_volatility_transition(
        "USDJPY",
        bw_compression_pct=0.9,
        atr_expansion_mult=0.9,
        min_score=0.01,
        stop_atr_mult=1.5,
        target_atr_mult=2.0,
    )

    trades_vt3, stats_vt3 = test_strategy("vt_vloose", vt_vloose, splits.train, cost)
    print(f"  IS: n={stats_vt3.get('total_trades', 0)}, WR={stats_vt3.get('win_rate', 0):.1%}, "
          f"PF={stats_vt3.get('profit_factor', 0):.2f}, E=${stats_vt3.get('expectancy_after_cost', 0):.4f}")

    trades_vt3_oos, stats_vt3_oos = test_strategy("vt_vloose", vt_vloose, splits.test, cost)
    print(f"  OOS: n={stats_vt3_oos.get('total_trades', 0)}, WR={stats_vt3_oos.get('win_rate', 0):.1%}, "
          f"PF={stats_vt3_oos.get('profit_factor', 0):.2f}, E=${stats_vt3_oos.get('expectancy_after_cost', 0):.4f}")

    # Walk-forward for the loosest version
    print()
    print("  Walk-forward (vt_vloose):")
    from bot.research.phase3.tournament import run_walk_forward
    wf = run_walk_forward(splits, vt_vloose, cost)
    wf_profitable = sum(1 for r in wf if r["expectancy_after_cost"] > 0)
    for r in wf:
        print(f"    Fold {r['fold']}: n={r['trades']}, E=${r['expectancy_after_cost']:.4f}, PF={r['profit_factor']:.2f}")
    print(f"    Profitable folds: {wf_profitable}/{len(wf)}")

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print()
    print("Breakout (fixed, original):")
    print(f"  Signals: {signal_count}, IS trades: {stats_is.get('total_trades', 0)}, OOS trades: {stats_oos.get('total_trades', 0)}")
    print(f"  OOS PF: {stats_oos.get('profit_factor', 0):.2f}, OOS E: ${stats_oos.get('expectancy_after_cost', 0):.4f}")
    print()
    print("Breakout (loosened):")
    print(f"  Signals: {signal_count_loose}, IS trades: {stats_is2.get('total_trades', 0)}, OOS trades: {stats_oos2.get('total_trades', 0)}")
    print(f"  OOS PF: {stats_oos2.get('profit_factor', 0):.2f}, OOS E: ${stats_oos2.get('expectancy_after_cost', 0):.4f}")
    print()
    print("Volatility Transition (original):")
    print(f"  IS trades: {stats_vt.get('total_trades', 0)}, OOS trades: {stats_vt_oos.get('total_trades', 0)}")
    print(f"  OOS PF: {stats_vt_oos.get('profit_factor', 0):.2f}, OOS E: ${stats_vt_oos.get('expectancy_after_cost', 0):.4f}")
    print()
    print("Volatility Transition (loosened):")
    print(f"  Signals: {signal_count_vt}, IS trades: {stats_vt2.get('total_trades', 0)}, OOS trades: {stats_vt2_oos.get('total_trades', 0)}")
    print(f"  OOS PF: {stats_vt2_oos.get('profit_factor', 0):.2f}, OOS E: ${stats_vt2_oos.get('expectancy_after_cost', 0):.4f}")
    print()
    print("Volatility Transition (very loose):")
    print(f"  IS trades: {stats_vt3.get('total_trades', 0)}, OOS trades: {stats_vt3_oos.get('total_trades', 0)}")
    print(f"  OOS PF: {stats_vt3_oos.get('profit_factor', 0):.2f}, OOS E: ${stats_vt3_oos.get('expectancy_after_cost', 0):.4f}")
    print(f"  WF profitable: {wf_profitable}/{len(wf)}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
