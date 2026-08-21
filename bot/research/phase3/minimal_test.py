"""Minimal test: fixed breakout + loosened vol transition on USDJPY H1."""

import logging
import numpy as np
from bot.research.data_pipeline import load_or_fetch
from bot.research.cost_model import CostModel
from bot.research.execution_model import BacktestEngine
from bot.research.analysis import compute_baseline_stats
from bot.research.phase3.data_splits import split_data

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

MT5_TIMEFRAME_H1 = 16385


def run(data, strategy_fn, cost):
    engine = BacktestEngine(initial_equity=10000, max_positions=1, max_hold_bars=72)
    trades = engine.run(data, strategy_fn, cost)
    stats = compute_baseline_stats(trades)
    return trades, stats


def main():
    import MetaTrader5 as mt5
    mt5.initialize()

    data = load_or_fetch("USDJPY", timeframe=MT5_TIMEFRAME_H1, n_bars=5000)
    if data is None:
        print("ERROR"); return

    splits = split_data(data)
    cost = CostModel(symbol="USDJPY", pip=data.pip, point=data.point,
                     contract_size=data.contract_size,
                     base_spread_points=data.base_spread_points)

    print(f"Data: {len(data)} bars")
    print()

    # Breakout original (FIXED)
    from bot.research.phase3.breakout import make_breakout
    print("Breakout (fixed, original):")
    _, s = run(splits.train, make_breakout("USDJPY"), cost)
    print(f"  IS:  n={s.get('total_trades',0)}, WR={s.get('win_rate',0):.1%}, PF={s.get('profit_factor',0):.2f}, E=${s.get('expectancy_after_cost',0):.4f}")
    _, s = run(splits.test, make_breakout("USDJPY"), cost)
    print(f"  OOS: n={s.get('total_trades',0)}, WR={s.get('win_rate',0):.1%}, PF={s.get('profit_factor',0):.2f}, E=${s.get('expectancy_after_cost',0):.4f}")

    # Breakout loosened
    print("Breakout (loosened: bw_pct=0.8, min_score=0.05):")
    _, s = run(splits.train, make_breakout("USDJPY", compression_threshold_pct=0.8, min_score=0.05), cost)
    print(f"  IS:  n={s.get('total_trades',0)}, WR={s.get('win_rate',0):.1%}, PF={s.get('profit_factor',0):.2f}, E=${s.get('expectancy_after_cost',0):.4f}")
    _, s = run(splits.test, make_breakout("USDJPY", compression_threshold_pct=0.8, min_score=0.05), cost)
    print(f"  OOS: n={s.get('total_trades',0)}, WR={s.get('win_rate',0):.1%}, PF={s.get('profit_factor',0):.2f}, E=${s.get('expectancy_after_cost',0):.4f}")

    # Volatility transition original
    from bot.research.phase3.volatility_transition import make_volatility_transition
    print("Vol Transition (original):")
    _, s = run(splits.train, make_volatility_transition("USDJPY"), cost)
    print(f"  IS:  n={s.get('total_trades',0)}, WR={s.get('win_rate',0):.1%}, PF={s.get('profit_factor',0):.2f}, E=${s.get('expectancy_after_cost',0):.4f}")
    _, s = run(splits.test, make_volatility_transition("USDJPY"), cost)
    print(f"  OOS: n={s.get('total_trades',0)}, WR={s.get('win_rate',0):.1%}, PF={s.get('profit_factor',0):.2f}, E=${s.get('expectancy_after_cost',0):.4f}")

    # Vol transition loosened
    print("Vol Transition (loosened: bw_pct=0.85, atr_mult=1.0, min_score=0.05):")
    vt_loose = make_volatility_transition("USDJPY", bw_compression_pct=0.85, atr_expansion_mult=1.0, min_score=0.05)
    _, s = run(splits.train, vt_loose, cost)
    print(f"  IS:  n={s.get('total_trades',0)}, WR={s.get('win_rate',0):.1%}, PF={s.get('profit_factor',0):.2f}, E=${s.get('expectancy_after_cost',0):.4f}")
    _, s = run(splits.test, vt_loose, cost)
    print(f"  OOS: n={s.get('total_trades',0)}, WR={s.get('win_rate',0):.1%}, PF={s.get('profit_factor',0):.2f}, E=${s.get('expectancy_after_cost',0):.4f}")

    # Vol transition very loose
    print("Vol Transition (very loose: bw_pct=0.9, atr_mult=0.9, min_score=0.01):")
    vt_vloose = make_volatility_transition("USDJPY", bw_compression_pct=0.9, atr_expansion_mult=0.9, min_score=0.01, stop_atr_mult=1.5, target_atr_mult=2.0)
    _, s = run(splits.train, vt_vloose, cost)
    print(f"  IS:  n={s.get('total_trades',0)}, WR={s.get('win_rate',0):.1%}, PF={s.get('profit_factor',0):.2f}, E=${s.get('expectancy_after_cost',0):.4f}")
    _, s = run(splits.test, vt_vloose, cost)
    print(f"  OOS: n={s.get('total_trades',0)}, WR={s.get('win_rate',0):.1%}, PF={s.get('profit_factor',0):.2f}, E=${s.get('expectancy_after_cost',0):.4f}")

    # Walk-forward for vt_vloose
    print("  Walk-forward (vt_vloose):")
    from bot.research.phase3.tournament import run_walk_forward
    wf = run_walk_forward(splits, vt_vloose, cost)
    for r in wf:
        print(f"    Fold {r['fold']}: n={r['trades']}, E=${r['expectancy_after_cost']:.4f}, PF={r['profit_factor']:.2f}")
    wf_pos = sum(1 for r in wf if r["expectancy_after_cost"] > 0)
    print(f"    Profitable: {wf_pos}/{len(wf)}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
