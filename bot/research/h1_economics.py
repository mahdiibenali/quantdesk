"""H1 Economics Test — spread/ATR tradeability audit before strategy design.

No strategies. No indicators. No optimization.
Just: does H1 create enough movement relative to spread to make FX trading
economically viable on this broker?

Gate:
  Spread/ATR < 10%  -> STRONG    -> prioritize for strategy discovery
  Spread/ATR 10-25% -> ACCEPTABLE -> research strategy families
  Spread/ATR 25-50% -> MARGINAL  -> proceed with caution
  Spread/ATR > 50%  -> POOR      -> abandon this symbol/timeframe
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

OUTPUT = Path("research_output")

# MT5 H1 timeframe constant
MT5_TIMEFRAME_H1 = 16385

# Tradeability gate thresholds (spread/ATR ratio)
GATE_STRONG = 0.10
GATE_ACCEPTABLE = 0.25
GATE_MARGINAL = 0.50


def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """Compute ATR(period) as median of True Range series."""
    if len(closes) < period + 1:
        return 0.0

    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    if len(trs) < period:
        return 0.0

    # Use last `period` values for most recent ATR
    atr = np.mean(trs[-period:])
    return float(atr)


def compute_atr_series(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute rolling ATR series for distribution analysis."""
    if len(closes) < period + 1:
        return np.array([])

    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    trs = np.array(trs)
    atrs = np.array([np.mean(trs[max(0, i - period + 1):i + 1]) for i in range(len(trs))])
    return atrs


def classify_tradeability(spread_atr_ratio: float) -> str:
    """Classify based on spread/ATR ratio."""
    if spread_atr_ratio < GATE_STRONG:
        return "STRONG"
    elif spread_atr_ratio < GATE_ACCEPTABLE:
        return "ACCEPTABLE"
    elif spread_atr_ratio < GATE_MARGINAL:
        return "MARGINAL"
    else:
        return "POOR"


def run_h1_economics(symbols: list[str] | None = None, n_bars: int = 5000) -> dict:
    """Run H1 economics audit for given symbols.

    Returns dict with per-symbol results and overall verdict.
    """
    import MetaTrader5 as mt5

    if symbols is None:
        symbols = ["EURUSD", "GBPUSD", "USDJPY"]

    # Connect
    if mt5.account_info() is None:
        initialized = mt5.initialize()
        if not initialized:
            logger.error("MT5 initialize() failed")
            return {"error": "MT5 init failed"}

    results = {}

    for sym in symbols:
        logger.info("Fetching H1 data for %s...", sym)

        # Fetch H1 bars
        rates = mt5.copy_rates_from_pos(sym, MT5_TIMEFRAME_H1, 0, n_bars)
        if rates is None or len(rates) == 0:
            logger.warning("No H1 data for %s", sym)
            results[sym] = {"error": "no data"}
            continue

        highs = np.array([r["high"] for r in rates], dtype=float)
        lows = np.array([r["low"] for r in rates], dtype=float)
        closes = np.array([r["close"] for r in rates], dtype=float)
        opens = np.array([r["open"] for r in rates], dtype=float)
        volumes = np.array([r["tick_volume"] for r in rates], dtype=float)

        # Time range
        from datetime import datetime, timezone
        t_start = datetime.fromtimestamp(rates[0]["time"], tz=timezone.utc)
        t_end = datetime.fromtimestamp(rates[-1]["time"], tz=timezone.utc)
        days = (t_end - t_start).days

        # Get symbol info
        info = mt5.symbol_info(sym)
        if info is None:
            results[sym] = {"error": "no symbol info"}
            continue

        point = info.point
        digits = info.digits
        contract_size = info.trade_contract_size
        current_spread_pts = info.spread * point  # spread in price units

        # Detect pip
        if "JPY" in sym:
            pip = 0.001
        elif digits <= 3:
            pip = 0.01
        else:
            pip = 0.0001

        # ATR(14) on H1
        atr_14 = compute_atr(highs, lows, closes, period=14)
        atr_10 = compute_atr(highs, lows, closes, period=10)
        atr_20 = compute_atr(highs, lows, closes, period=20)

        # ATR series for distribution
        atr_series = compute_atr_series(highs, lows, closes, period=14)

        # Current spread in price units
        spread_price = current_spread_pts

        # Key ratios
        spread_atr_ratio = spread_price / atr_14 if atr_14 > 0 else float("inf")
        spread_pips = spread_price / pip if pip > 0 else 0
        atr_pips = atr_14 / pip if pip > 0 else 0

        # Round-trip cost: 2 * spread (entry + exit)
        round_trip_cost = 2 * spread_price
        rt_cost_atr_ratio = round_trip_cost / atr_14 if atr_14 > 0 else float("inf")

        # Typical favorable excursion: median positive bar range
        bar_ranges = highs - lows
        median_range = float(np.median(bar_ranges))
        mean_range = float(np.mean(bar_ranges))

        # Daily range estimate: average of hourly ranges * ~10 active hours
        # More accurate: sum of ATRs per day (approximately atr_14 * hours_active)
        # But simpler: just use the ATR ratio directly

        # USD conversion
        last_close = closes[-1] if len(closes) > 0 else 1.0
        if sym.endswith("USD"):
            usd_per_quote = 1.0
        elif sym.startswith("USD"):
            usd_per_quote = 1.0 / last_close if last_close > 0 else 1.0
        else:
            usd_per_quote = 1.0

        # Round-trip cost in USD per standard lot (100k units)
        rt_cost_usd_per_lot = round_trip_cost * contract_size * usd_per_quote

        # ATR in USD per standard lot
        atr_usd_per_lot = atr_14 * contract_size * usd_per_quote

        # Tradeability gate
        classification = classify_tradeability(spread_atr_ratio)

        # ATR distribution percentiles
        atr_p5 = float(np.percentile(atr_series, 5)) if len(atr_series) > 0 else 0
        atr_p25 = float(np.percentile(atr_series, 25)) if len(atr_series) > 0 else 0
        atr_p50 = float(np.percentile(atr_series, 50)) if len(atr_series) > 0 else 0
        atr_p75 = float(np.percentile(atr_series, 75)) if len(atr_series) > 0 else 0
        atr_p95 = float(np.percentile(atr_series, 95)) if len(atr_series) > 0 else 0

        # Spread at different stress levels
        stress_results = {}
        for label, mult in [("1.0x", 1.0), ("1.25x", 1.25), ("1.5x", 1.5), ("2.0x", 2.0)]:
            stressed_spread = spread_price * mult
            stressed_ratio = stressed_spread / atr_14 if atr_14 > 0 else float("inf")
            stressed_rt_ratio = (2 * stressed_spread) / atr_14 if atr_14 > 0 else float("inf")
            stress_results[label] = {
                "spread_pips": stressed_spread / pip,
                "spread_atr_ratio": stressed_ratio,
                "rt_cost_atr_ratio": stressed_rt_ratio,
                "classification": classify_tradeability(stressed_ratio),
            }

        results[sym] = {
            "bars": len(rates),
            "days": days,
            "date_range": f"{t_start.date()} to {t_end.date()}",
            "point": point,
            "digits": digits,
            "pip": pip,
            "contract_size": contract_size,
            "current_spread_points": info.spread,
            "current_spread_price": spread_price,
            "current_spread_pips": spread_pips,
            "atr_14_h1": atr_14,
            "atr_14_pips": atr_pips,
            "atr_10_h1": atr_10,
            "atr_20_h1": atr_20,
            "spread_atr_ratio": spread_atr_ratio,
            "rt_cost_atr_ratio": rt_cost_atr_ratio,
            "classification": classification,
            "atr_distribution": {
                "p5": atr_p5, "p25": atr_p25, "p50": atr_p50,
                "p75": atr_p75, "p95": atr_p95,
            },
            "cost_usd_per_lot": {
                "round_trip": rt_cost_usd_per_lot,
                "atr_14": atr_usd_per_lot,
                "rt_as_pct_of_atr": rt_cost_atr_ratio * 100,
            },
            "stress_test": stress_results,
            "median_bar_range_pips": median_range / pip,
            "mean_bar_range_pips": mean_range / pip,
        }

    return results


def format_report(results: dict) -> str:
    """Format results into a readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append("  H1 ECONOMICS AUDIT — SPREAD/ATR TRADEABILITY")
    lines.append("=" * 70)
    lines.append("")
    lines.append("  No strategies tested. No indicators. No optimization.")
    lines.append("  This answers: does the market + broker + timeframe have")
    lines.append("  enough movement to justify further research?")
    lines.append("")

    if "error" in results:
        lines.append(f"  ERROR: {results['error']}")
        return "\n".join(lines)

    # Summary table
    lines.append("  GATE: Spread/ATR ratio")
    lines.append(f"    <{GATE_STRONG:.0%} STRONG    -> prioritize")
    lines.append(f"    <{GATE_ACCEPTABLE:.0%} ACCEPTABLE -> research")
    lines.append(f"    <{GATE_MARGINAL:.0%} MARGINAL   -> caution")
    lines.append(f"    >={GATE_MARGINAL:.0%} POOR       -> abandon")
    lines.append("")

    # Per-symbol
    for sym, r in results.items():
        if "error" in r:
            lines.append(f"  {sym}: ERROR — {r['error']}")
            continue

        lines.append("-" * 70)
        lines.append(f"  {sym}")
        lines.append("-" * 70)
        lines.append(f"  Data: {r['bars']} H1 bars, {r['days']} days ({r['date_range']})")
        lines.append(f"  Point: {r['point']}, Digits: {r['digits']}, Pip: {r['pip']}")
        lines.append(f"  Contract size: {r['contract_size']:,.0f}")
        lines.append("")
        lines.append(f"  Current spread:  {r['current_spread_points']:.0f} pts = {r['current_spread_pips']:.1f} pips")
        lines.append(f"  ATR(14) H1:      {r['atr_14_h1']:.5f} = {r['atr_14_pips']:.1f} pips")
        lines.append(f"  ATR(10) H1:      {r['atr_10_h1']:.5f}")
        lines.append(f"  ATR(20) H1:      {r['atr_20_h1']:.5f}")
        lines.append("")
        lines.append(f"  Spread / ATR:     {r['spread_atr_ratio']:.1%}  ->  {r['classification']}")
        lines.append(f"  RT cost / ATR:    {r['rt_cost_atr_ratio']:.1%}")
        lines.append("")
        lines.append(f"  Round-trip cost:  ${r['cost_usd_per_lot']['round_trip']:.2f} / lot")
        lines.append(f"  ATR(14) in USD:   ${r['cost_usd_per_lot']['atr_14']:.2f} / lot")
        lines.append(f"  RT cost as % ATR: {r['cost_usd_per_lot']['rt_as_pct_of_atr']:.1f}%")
        lines.append("")

        # ATR distribution
        ad = r["atr_distribution"]
        lines.append(f"  ATR(14) distribution (pips):")
        lines.append(f"    P5:  {ad['p5'] / r['pip']:.1f}  |  P25: {ad['p25'] / r['pip']:.1f}  |  "
                     f"P50: {ad['p50'] / r['pip']:.1f}  |  P75: {ad['p75'] / r['pip']:.1f}  |  "
                     f"P95: {ad['p95'] / r['pip']:.1f}")
        lines.append(f"  Bar range: median={r['median_bar_range_pips']:.1f} pips, "
                     f"mean={r['mean_bar_range_pips']:.1f} pips")
        lines.append("")

        # Stress test
        lines.append(f"  Spread stress test:")
        lines.append(f"    {'Level':8s} {'Spread':>8s} {'S/ATR':>8s} {'RT/ATR':>8s} {'Gate':>12s}")
        for label, sr in r["stress_test"].items():
            lines.append(f"    {label:8s} {sr['spread_pips']:7.1f}p {sr['spread_atr_ratio']:7.1%} "
                         f"{sr['rt_cost_atr_ratio']:7.1%} {sr['classification']:>12s}")
        lines.append("")

    # Overall verdict
    lines.append("=" * 70)
    lines.append("  OVERALL VERDICT")
    lines.append("=" * 70)

    classifications = {sym: r.get("classification", "UNKNOWN") for sym, r in results.items() if "error" not in r}

    strong = [s for s, c in classifications.items() if c == "STRONG"]
    acceptable = [s for s, c in classifications.items() if c == "ACCEPTABLE"]
    marginal = [s for s, c in classifications.items() if c == "MARGINAL"]
    poor = [s for s, c in classifications.items() if c == "POOR"]

    if strong:
        lines.append(f"  STRONG:    {', '.join(strong)}")
    if acceptable:
        lines.append(f"  ACCEPTABLE: {', '.join(acceptable)}")
    if marginal:
        lines.append(f"  MARGINAL:  {', '.join(marginal)}")
    if poor:
        lines.append(f"  POOR:      {', '.join(poor)}")

    lines.append("")

    if not strong and not acceptable:
        lines.append("  VERDICT: H1 economics are POOR across all tested symbols.")
        lines.append("  Do NOT build strategies for this market/timeframe/broker.")
        lines.append("  The transaction-cost structure makes the problem impossible.")
    elif strong:
        lines.append(f"  VERDICT: H1 economics are STRONG for {', '.join(strong)}.")
        lines.append("  Proceed with strategy-family tournament on these symbols.")
    else:
        lines.append(f"  VERDICT: H1 economics are ACCEPTABLE for {', '.join(acceptable)}.")
        lines.append("  Strategy research is justified but edge will be thin.")
        lines.append("  Prioritize low-frequency, higher-R:R approaches.")

    lines.append("")
    lines.append("  A negative H1 result is a successful research result too.")
    lines.append("  It prevents spending another week engineering a strategy for")
    lines.append("  a market whose transaction-cost structure makes the problem")
    lines.append("  impossible.")

    return "\n".join(lines)


def main():
    """Run H1 economics test and save report."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    results = run_h1_economics()

    report = format_report(results)
    print(report)

    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "h1_economics_report.md").write_text(report)
    with open(OUTPUT / "h1_economics_data.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    logger.info("Reports saved to %s/", OUTPUT)

    import MetaTrader5 as mt5
    mt5.shutdown()


if __name__ == "__main__":
    main()
