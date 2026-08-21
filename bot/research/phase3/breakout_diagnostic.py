"""Breakout diagnostic — why zero trades?

Walk through the breakout strategy logic step by step:
1. How many bars have compression?
2. How many have ATR contraction?
3. How many have breakouts?
4. How many have displacement?
5. Where does the filter chain break?
"""

from __future__ import annotations

import numpy as np
from bot.research.data_pipeline import load_or_fetch

MT5_TIMEFRAME_H1 = 16385


def _atr(highs, lows, closes, period=14):
    trs = np.empty(len(closes), dtype=float)
    trs[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        trs[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr = np.empty_like(trs)
    if len(trs) >= period:
        atr[period-1] = np.mean(trs[:period])
        for i in range(period, len(trs)):
            atr[i] = (atr[i-1] * (period-1) + trs[i]) / period
    else:
        atr[:] = np.nan
    return atr


def _bollinger(closes, period=20, num_std=2.0):
    n = len(closes)
    upper = np.full(n, np.nan)
    middle = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    bandwidth = np.full(n, np.nan)
    for i in range(period-1, n):
        window = closes[i-period+1:i+1]
        ma = np.mean(window)
        std = np.std(window, ddof=1)
        middle[i] = ma
        upper[i] = ma + num_std * std
        lower[i] = ma - num_std * std
        bandwidth[i] = (upper[i] - lower[i]) / ma if ma > 0 else 0
    return upper, middle, lower, bandwidth


def diagnose():
    import MetaTrader5 as mt5
    mt5.initialize()

    data = load_or_fetch("USDJPY", timeframe=MT5_TIMEFRAME_H1, n_bars=5000)
    if data is None:
        print("ERROR: no data")
        return

    closes = np.array(data.closes, dtype=float)
    highs = np.array(data.highs, dtype=float)
    lows = np.array(data.lows, dtype=float)

    bb_upper, bb_middle, bb_lower, bw = _bollinger(closes, 20, 2.0)
    atr = _atr(highs, lows, closes, 14)

    n = len(closes)
    print(f"Total bars: {n}")
    print()

    # Step 1: How many bars have valid BB bandwidth?
    valid_bw = ~np.isnan(bw)
    print(f"Step 1: Bars with valid BB bandwidth: {np.sum(valid_bw)} / {n}")

    # Step 2: Compression check
    compression_lookback = 20
    compression_threshold_pct = 0.5

    compressed_count = 0
    for i in range(compression_lookback, n):
        bw_lookback = bw[i-compression_lookback:i]
        bw_lookback = bw_lookback[~np.isnan(bw_lookback)]
        if len(bw_lookback) < 5:
            continue
        bw_current = bw[i]
        bw_mean = np.mean(bw_lookback)
        bw_min = np.min(bw_lookback)
        is_compressed = bw_current < bw_mean * compression_threshold_pct or bw_current < bw_min * 1.1
        if is_compressed:
            compressed_count += 1

    print(f"Step 2: Bars with compression: {compressed_count} / {n - compression_lookback}")

    # Step 3: ATR contraction
    contraction_count = 0
    for i in range(compression_lookback, n):
        bw_lookback = bw[i-compression_lookback:i]
        bw_lookback = bw_lookback[~np.isnan(bw_lookback)]
        if len(bw_lookback) < 5:
            continue
        bw_current = bw[i]
        bw_mean = np.mean(bw_lookback)
        bw_min = np.min(bw_lookback)
        is_compressed = bw_current < bw_mean * compression_threshold_pct or bw_current < bw_min * 1.1
        if not is_compressed:
            continue

        atr_series = atr[i-compression_lookback:i]
        atr_series = atr_series[~np.isnan(atr_series)]
        if len(atr_series) < 5:
            continue
        atr_mean = np.mean(atr_series)
        atr_contracting = atr[i] < atr_mean * 0.8
        if atr_contracting:
            contraction_count += 1

    print(f"Step 3: Bars with compression + ATR contraction: {contraction_count}")

    # Step 4: Breakout detection
    breakout_count = 0
    for i in range(compression_lookback, n):
        bw_lookback = bw[i-compression_lookback:i]
        bw_lookback = bw_lookback[~np.isnan(bw_lookback)]
        if len(bw_lookback) < 5:
            continue
        bw_current = bw[i]
        bw_mean = np.mean(bw_lookback)
        bw_min = np.min(bw_lookback)
        is_compressed = bw_current < bw_mean * compression_threshold_pct or bw_current < bw_min * 1.1
        if not is_compressed:
            continue

        atr_series = atr[i-compression_lookback:i]
        atr_series = atr_series[~np.isnan(atr_series)]
        if len(atr_series) < 5:
            continue
        atr_mean = np.mean(atr_series)
        atr_contracting = atr[i] < atr_mean * 0.8
        if not atr_contracting:
            continue

        # Breakout check
        if np.isnan(bb_upper[i]) or np.isnan(bb_lower[i]):
            continue
        cur_price = closes[i]
        prev_price = closes[i-1] if i > 0 else cur_price
        displacement = abs(cur_price - prev_price)

        breakout_up = cur_price > bb_upper[i]
        breakout_down = cur_price < bb_lower[i]

        lookback = min(20, i)
        range_high = np.max(highs[i-lookback:i])
        range_low = np.min(lows[i-lookback:i])
        range_breakout_up = cur_price > range_high and displacement > atr[i] * 0.5
        range_breakout_down = cur_price < range_low and displacement > atr[i] * 0.5

        is_breakout = breakout_up or breakout_down or range_breakout_up or range_breakout_down
        if is_breakout:
            breakout_count += 1

    print(f"Step 4: Bars with compression + contraction + breakout: {breakout_count}")

    # Step 5: Displacement confirmation
    displacement_count = 0
    for i in range(compression_lookback, n):
        bw_lookback = bw[i-compression_lookback:i]
        bw_lookback = bw_lookback[~np.isnan(bw_lookback)]
        if len(bw_lookback) < 5:
            continue
        bw_current = bw[i]
        bw_mean = np.mean(bw_lookback)
        bw_min = np.min(bw_lookback)
        is_compressed = bw_current < bw_mean * compression_threshold_pct or bw_current < bw_min * 1.1
        if not is_compressed:
            continue

        atr_series = atr[i-compression_lookback:i]
        atr_series = atr_series[~np.isnan(atr_series)]
        if len(atr_series) < 5:
            continue
        atr_mean = np.mean(atr_series)
        atr_contracting = atr[i] < atr_mean * 0.8
        if not atr_contracting:
            continue

        if np.isnan(bb_upper[i]) or np.isnan(bb_lower[i]):
            continue
        cur_price = closes[i]
        prev_price = closes[i-1] if i > 0 else cur_price
        displacement = abs(cur_price - prev_price)

        breakout_up = cur_price > bb_upper[i]
        breakout_down = cur_price < bb_lower[i]
        lookback = min(20, i)
        range_high = np.max(highs[i-lookback:i])
        range_low = np.min(lows[i-lookback:i])
        range_breakout_up = cur_price > range_high and displacement > atr[i] * 0.5
        range_breakout_down = cur_price < range_low and displacement > atr[i] * 0.5
        is_breakout = breakout_up or breakout_down or range_breakout_up or range_breakout_down
        if not is_breakout:
            continue

        bar_range = highs[i] - lows[i]
        has_displacement = bar_range > atr[i] * 0.8 or displacement > atr[i] * 0.5
        if has_displacement:
            displacement_count += 1

    print(f"Step 5: Bars with all filters + displacement: {displacement_count}")
    print()

    # Also check: what does the score look like for the few candidates?
    print("--- Checking score components for surviving candidates ---")
    from bot.research.phase3.breakout import make_breakout
    strategy_fn = make_breakout("USDJPY")

    from bot.research.execution_model import BacktestEngine, BacktestState
    from bot.research.cost_model import CostModel

    # Count how many times strategy_fn returns non-None
    from bot.research.data_pipeline import SymbolData
    signal_count = 0
    for i in range(60, n):
        slice_data = SymbolData(
            symbol="USDJPY",
            bars=data.bars[:i+1],
            pip=data.pip,
            contract_size=data.contract_size,
            point=data.point,
            volume_step=data.volume_step,
            volume_min=data.volume_min,
            volume_max=data.volume_max,
            base_spread_points=data.base_spread_points,
        )
        state = BacktestState(equity=10000, initial_equity=10000, peak_equity=10000,
                              positions=[], closed_trades=[], bar_idx=i)
        result = strategy_fn(slice_data, state)
        if result is not None:
            signal_count += 1
            if signal_count <= 5:
                print(f"  Signal at bar {i}: {result['side']}, score={result['score']:.3f}, "
                      f"reasons={result['reasons']}")

    print(f"\nTotal signals generated by strategy_fn: {signal_count}")

    # Now check with score threshold lowered
    print("\n--- With min_score=0.05 ---")
    signal_count_low = 0
    for i in range(60, n):
        slice_data = SymbolData(
            symbol="USDJPY",
            bars=data.bars[:i+1],
            pip=data.pip,
            contract_size=data.contract_size,
            point=data.point,
            volume_step=data.volume_step,
            volume_min=data.volume_min,
            volume_max=data.volume_max,
            base_spread_points=data.base_spread_points,
        )
        state = BacktestState(equity=10000, initial_equity=10000, peak_equity=10000,
                              positions=[], closed_trades=[], bar_idx=i)
        # Manually call with lower threshold
        from bot.research.phase3.breakout import make_breakout
        strategy_fn_low = make_breakout("USDJPY", min_score=0.05)
        result = strategy_fn_low(slice_data, state)
        if result is not None:
            signal_count_low += 1
            if signal_count_low <= 5:
                print(f"  Signal at bar {i}: {result['side']}, score={result['score']:.3f}, "
                      f"reasons={result['reasons']}")

    print(f"\nTotal signals with min_score=0.05: {signal_count_low}")

    # With even lower threshold
    print("\n--- With min_score=0.01 ---")
    signal_count很低 = 0
    for i in range(60, n):
        slice_data = SymbolData(
            symbol="USDJPY",
            bars=data.bars[:i+1],
            pip=data.pip,
            contract_size=data.contract_size,
            point=data.point,
            volume_step=data.volume_step,
            volume_min=data.volume_min,
            volume_max=data.volume_max,
            base_spread_points=data.base_spread_points,
        )
        state = BacktestState(equity=10000, initial_equity=10000, peak_equity=10000,
                              positions=[], closed_trades=[], bar_idx=i)
        strategy_fn_vlow = make_breakout("USDJPY", min_score=0.01)
        result = strategy_fn_vlow(slice_data, state)
        if result is not None:
            signal_count很低 += 1
            if signal_count很低 <= 5:
                print(f"  Signal at bar {i}: {result['side']}, score={result['score']:.3f}, "
                      f"reasons={result['reasons']}")

    print(f"\nTotal signals with min_score=0.01: {signal_count很低}")

    # Check: what are the BB bandwidth values?
    print("\n--- BB Bandwidth Distribution ---")
    valid_bw_vals = bw[~np.isnan(bw)]
    print(f"  Min: {np.min(valid_bw_vals):.6f}")
    print(f"  P5: {np.percentile(valid_bw_vals, 5):.6f}")
    print(f"  P25: {np.percentile(valid_bw_vals, 25):.6f}")
    print(f"  Median: {np.median(valid_bw_vals):.6f}")
    print(f"  P75: {np.percentile(valid_bw_vals, 75):.6f}")
    print(f"  P95: {np.percentile(valid_bw_vals, 95):.6f}")
    print(f"  Max: {np.max(valid_bw_vals):.6f}")

    # Check: how often is bandwidth < mean * 0.5?
    below_threshold = 0
    for i in range(20, n):
        bw_lookback = bw[i-20:i]
        bw_lookback = bw_lookback[~np.isnan(bw_lookback)]
        if len(bw_lookback) < 5:
            continue
        bw_mean = np.mean(bw_lookback)
        if bw[i] < bw_mean * 0.5:
            below_threshold += 1
    print(f"\n  Bars where BW < mean*0.5: {below_threshold} / {n-20}")

    # Check: how often is BW < min*1.1?
    below_min = 0
    for i in range(20, n):
        bw_lookback = bw[i-20:i]
        bw_lookback = bw_lookback[~np.isnan(bw_lookback)]
        if len(bw_lookback) < 5:
            continue
        bw_min = np.min(bw_lookback)
        if bw[i] < bw_min * 1.1:
            below_min += 1
    print(f"  Bars where BW < min*1.1: {below_min} / {n-20}")

    mt5.shutdown()


if __name__ == "__main__":
    diagnose()
