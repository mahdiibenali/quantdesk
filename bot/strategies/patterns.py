"""Candlestick pattern detection — scalping-optimized.

Patterns: engulfing, pin bar, doji, inside bar, hammer, harami, shooting star.
Each returns a score: +1 (bullish) to -1 (bearish), 0 = no pattern.
"""

import numpy as np
from dataclasses import dataclass


@dataclass
class Candle:
    """Single candle OHLCV."""
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open


def detect_engulfing(candles: list[Candle]) -> float:
    """Bullish/bearish engulfing — reversal signal.

    Bullish: bearish candle fully engulfed by next bullish candle.
    Bearish: bullish candle fully engulfed by next bearish candle.
    """
    if len(candles) < 2:
        return 0.0

    prev, cur = candles[-2], candles[-1]

    if prev.body < prev.range * 0.1:  # prev too small
        return 0.0

    if cur.body < cur.range * 0.1:  # cur too small
        return 0.0

    # Bullish engulfing: bearish prev, bullish cur, cur body engulfs prev
    if prev.is_bearish and cur.is_bullish:
        if cur.open <= prev.close and cur.close >= prev.open:
            strength = min(cur.body / prev.body, 3.0) / 3.0
            return 0.3 + 0.5 * strength  # 0.3 to 0.8

    # Bearish engulfing: bullish prev, bearish cur, cur body engulfs prev
    if prev.is_bullish and cur.is_bearish:
        if cur.open >= prev.close and cur.close <= prev.open:
            strength = min(cur.body / prev.body, 3.0) / 3.0
            return -(0.3 + 0.5 * strength)

    return 0.0


def detect_pin_bar(candles: list[Candle]) -> float:
    """Pin bar / rejection wick — strong reversal signal.

    Bullish pin bar: long lower wick, small body at top.
    Bearish pin bar: long upper wick, small body at bottom.
    """
    if len(candles) < 1:
        return 0.0

    c = candles[-1]
    if c.range <= 0:
        return 0.0

    body_ratio = c.body / c.range
    if body_ratio > 0.35:  # body too large for pin bar
        return 0.0

    # Bullish pin bar: long lower wick
    if c.lower_wick > c.range * 0.6 and c.upper_wick < c.range * 0.2:
        strength = min(c.lower_wick / c.range, 1.0)
        return 0.4 + 0.4 * strength  # 0.4 to 0.8

    # Bearish pin bar: long upper wick
    if c.upper_wick > c.range * 0.6 and c.lower_wick < c.range * 0.2:
        strength = min(c.upper_wick / c.range, 1.0)
        return -(0.4 + 0.4 * strength)

    return 0.0


def detect_doji(candles: list[Candle]) -> float:
    """Doji — indecision, low signal alone, confirms other patterns.

    Very small body relative to range.
    """
    if len(candles) < 1:
        return 0.0

    c = candles[-1]
    if c.range <= 0:
        return 0.0

    body_ratio = c.body / c.range

    if body_ratio < 0.08:  # very small body
        # Long-legged doji: long wicks both sides
        if c.upper_wick > c.range * 0.3 and c.lower_wick > c.range * 0.3:
            return 0.2  # indecision, slight bullish bias if at support
        return 0.1  # regular doji

    return 0.0


def detect_inside_bar(candles: list[Candle]) -> float:
    """Inside bar — consolidation before breakout.

    Current candle's range is entirely within previous candle's range.
    """
    if len(candles) < 2:
        return 0.0

    prev, cur = candles[-2], candles[-1]

    if cur.high <= prev.high and cur.low >= prev.low:
        # Tighter consolidation = stronger signal
        compression = 1.0 - (cur.range / prev.range) if prev.range > 0 else 0.0
        return 0.2 + 0.3 * compression  # 0.2 to 0.5

    return 0.0


def detect_hammer(candles: list[Candle]) -> float:
    """Hammer / hanging man — reversal signal.

    Hammer (bullish): small body at top, long lower wick, after downtrend.
    Hanging man (bearish): small body at top, long lower wick, after uptrend.
    """
    if len(candles) < 3:
        return 0.0

    c = candles[-1]
    if c.range <= 0:
        return 0.0

    body_ratio = c.body / c.range

    if body_ratio > 0.35:
        return 0.0

    # Hammer: long lower wick, small upper wick
    if c.lower_wick > c.body * 2 and c.upper_wick < c.body * 0.5:
        # Check if after downtrend (last 3 candles mostly bearish)
        bearish_count = sum(1 for x in candles[-3:] if x.is_bearish)
        if bearish_count >= 2:
            return 0.5  # bullish reversal

    return 0.0


def detect_harami(candles: list[Candle]) -> float:
    """Harami (fatman) — reversal pattern.

    Bullish harami: large bearish candle followed by small bullish candle
    contained within the previous body.
    Bearish harami: large bullish candle followed by small bearish candle
    contained within the previous body.
    """
    if len(candles) < 2:
        return 0.0

    prev, cur = candles[-2], candles[-1]

    if prev.body < prev.range * 0.3:  # prev body too small
        return 0.0

    if cur.body > prev.body * 0.6:  # cur body too large for harami
        return 0.0

    # Bullish harami: bearish prev, bullish cur inside prev body
    if prev.is_bearish and cur.is_bullish:
        if cur.close < prev.open and cur.open > prev.close:
            return 0.35

    # Bearish harami: bullish prev, bearish cur inside prev body
    if prev.is_bullish and cur.is_bearish:
        if cur.close > prev.open and cur.open < prev.close:
            return -0.35

    return 0.0


def detect_shooting_star(candles: list[Candle]) -> float:
    """Shooting star — bearish reversal at top.

    Small body at bottom, long upper wick, after uptrend.
    """
    if len(candles) < 3:
        return 0.0

    c = candles[-1]
    if c.range <= 0:
        return 0.0

    body_ratio = c.body / c.range

    if body_ratio > 0.35:
        return 0.0

    if c.upper_wick > c.body * 2 and c.lower_wick < c.body * 0.5:
        bullish_count = sum(1 for x in candles[-3:] if x.is_bullish)
        if bullish_count >= 2:
            return -0.5  # bearish reversal

    return 0.0


def detect_all(opens: list, highs: list, lows: list, closes: list) -> dict:
    """Run all pattern detectors on recent candles.

    Returns dict with individual scores and combined score.
    Combined score: -1.0 (bearish) to +1.0 (bullish).
    """
    if len(closes) < 5:
        return {"combined": 0.0, "patterns": {}}

    # Build candle objects (last 10 for pattern detection)
    n = min(len(closes), 10)
    candles = [
        Candle(open=opens[-n+i], high=highs[-n+i], low=lows[-n+i], close=closes[-n+i])
        for i in range(n)
    ]

    patterns = {
        "engulfing": detect_engulfing(candles),
        "pin_bar": detect_pin_bar(candles),
        "doji": detect_doji(candles),
        "inside_bar": detect_inside_bar(candles),
        "hammer": detect_hammer(candles),
        "harami": detect_harami(candles),
        "shooting_star": detect_shooting_star(candles),
    }

    # Filter out zero scores
    active = {k: v for k, v in patterns.items() if v != 0.0}

    if not active:
        return {"combined": 0.0, "patterns": {}}

    # Weighted combination
    weights = {
        "engulfing": 1.5,
        "pin_bar": 1.5,
        "hammer": 1.2,
        "shooting_star": 1.2,
        "harami": 1.0,
        "inside_bar": 0.8,
        "doji": 0.5,
    }

    total_weight = sum(weights.get(k, 1.0) for k in active)
    combined = sum(v * weights.get(k, 1.0) for k, v in active.items()) / total_weight

    # Cap at [-1, 1]
    combined = max(-1.0, min(1.0, combined))

    return {"combined": round(combined, 4), "patterns": active}
