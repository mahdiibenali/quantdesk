"""Generic Scalping Strategy — works with any symbol.

Uses EMA crossover + RSI + momentum + candlestick patterns.
3-phase lifecycle: on_idle (scan), on_open (manage), on_busy (pending).
"""

import numpy as np
from datetime import datetime, timezone
from bot.strategies.base import Strategy, Signal, Side, Intent, OpenPosition, ArmedState
from bot.strategies.signals import SignalAggregator
from bot.strategies.patterns import detect_all as detect_patterns


# Default instrument profiles — pip size, min SL/TP, max spread
PROFILES = {
    # Majors (Tier 1 — tightest spreads, cleanest trends)
    "EURUSD": {"pip": 0.0001, "min_stop_pips": 5.0,  "min_target_pips": 8.0,  "spread_cap": 30},
    "GBPUSD": {"pip": 0.0001, "min_stop_pips": 5.0,  "min_target_pips": 8.0,  "spread_cap": 30},
    "USDJPY": {"pip": 0.001,  "min_stop_pips": 20.0, "min_target_pips": 35.0, "spread_cap": 35},
    "USDCHF": {"pip": 0.0001, "min_stop_pips": 5.0,  "min_target_pips": 8.0,  "spread_cap": 30},
    "USDCAD": {"pip": 0.0001, "min_stop_pips": 5.0,  "min_target_pips": 8.0,  "spread_cap": 30},
    "AUDUSD": {"pip": 0.0001, "min_stop_pips": 5.0,  "min_target_pips": 8.0,  "spread_cap": 30},
    "NZDUSD": {"pip": 0.0001, "min_stop_pips": 5.0,  "min_target_pips": 8.0,  "spread_cap": 30},

    "XAUUSD": {"pip": 0.01,   "min_stop_pips": 30.0, "min_target_pips": 50.0, "spread_cap": 60},

    "USDBRL": {"pip": 0.0001, "min_stop_pips": 15.0, "min_target_pips": 25.0, "spread_cap": 200},
    "USDINR": {"pip": 0.001,  "min_stop_pips": 15.0, "min_target_pips": 25.0, "spread_cap": 40},
    "USDSGD": {"pip": 0.0001, "min_stop_pips": 10.0, "min_target_pips": 15.0, "spread_cap": 40},
    "USDHKD": {"pip": 0.0001, "min_stop_pips": 5.0,  "min_target_pips": 8.0,  "spread_cap": 25},
}


class Scalp(Strategy):
    """Generic scalping strategy — works with any symbol via instrument profiles."""

    def __init__(
        self,
        symbol: str = "EURUSD",
        name: str = None,
        ema_fast: int = 5,
        ema_slow: int = 20,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        atr_period: int = 14,
        stop_atr_mult: float = 1.5,
        target_atr_mult: float = 2.5,
        min_bars: int = 30,
        timeframe: int = 5,
    ):
        self.symbol = symbol
        self.name = name or f"scalp_{symbol.lower()}"

        # Load profile
        profile = PROFILES.get(symbol, PROFILES["EURUSD"])
        self.pip = profile["pip"]
        self.min_stop_pips = profile["min_stop_pips"]
        self.min_target_pips = profile["min_target_pips"]
        self.spread_cap = profile["spread_cap"]

        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.target_atr_mult = target_atr_mult
        self.min_bars = min_bars
        self.timeframe = timeframe

        self._armed = ArmedState()
        self._pnl_history = {}  # ticket -> [(time, pnl)]

    def on_idle(self, data: dict, equity: float) -> Signal | None:
        """Phase 1: No position — scan for entry."""
        closes = data.get("closes")
        highs = data.get("highs")
        lows = data.get("lows")

        if closes is None or len(closes) < self.min_bars:
            return None

        closes_arr = np.array(closes, dtype=float)
        fast_ema = self._ema(closes_arr, self.ema_fast)
        slow_ema = self._ema(closes_arr, self.ema_slow)
        ema50 = self._ema(closes_arr, 50) if len(closes) >= 50 else None
        rsi = self._rsi(closes_arr, self.rsi_period)
        atr = self._atr(
            np.array(highs, dtype=float),
            np.array(lows, dtype=float),
            closes_arr,
            self.atr_period,
        )

        if atr <= 0 or np.isnan(atr):
            return None

        cur_fast = fast_ema[-1]
        cur_slow = slow_ema[-1]
        cur_rsi = rsi[-1]
        cur_price = closes_arr[-1]

        prev_fast = fast_ema[-2] if len(fast_ema) > 1 else cur_fast
        prev_slow = slow_ema[-2] if len(slow_ema) > 1 else cur_slow

        if any(np.isnan(x) for x in [cur_fast, cur_slow, cur_rsi, prev_fast, prev_slow]):
            return None

        agg = SignalAggregator(["ema_cross", "rsi", "momentum", "patterns"])

        # EMA crossover
        cross_up = prev_fast <= prev_slow and cur_fast > cur_slow
        cross_down = prev_fast >= prev_slow and cur_fast < cur_slow
        above = cur_fast > cur_slow

        if cross_up:
            agg.add_component("ema_cross", score=0.8, weight=1.5)
        elif cross_down:
            agg.add_component("ema_cross", score=-0.8, weight=1.5)
        elif above:
            agg.add_component("ema_cross", score=0.3, weight=1.0)
        else:
            agg.add_component("ema_cross", score=-0.3, weight=1.0)

        # RSI
        if cur_rsi < self.rsi_oversold:
            agg.add_component("rsi", score=0.7, weight=1.0)
        elif cur_rsi > self.rsi_overbought:
            agg.add_component("rsi", score=-0.7, weight=1.0)
        else:
            rsi_mid = (cur_rsi - 50) / 50
            agg.add_component("rsi", score=-rsi_mid * 0.3, weight=0.8)

        # Momentum
        momentum = (cur_price - cur_slow) / atr if atr > 0 else 0
        momentum = max(-1, min(1, momentum))
        agg.add_component("momentum", score=momentum * 0.5, weight=1.0)

        # Candlestick patterns
        opens = data.get("opens", [])
        highs_list = data.get("highs", [])
        lows_list = data.get("lows", [])
        if len(opens) >= 5:
            pat = detect_patterns(opens, highs_list, lows_list, closes)
            if pat["combined"] != 0.0:
                agg.add_component("patterns", score=pat["combined"], weight=1.3)

        sig = agg.compute()

        if sig.score == 0 and sig.confidence == 0:
            return None

        # TREND FILTER: only trade in direction of EMA50
        if ema50 is not None and not np.isnan(ema50[-1]):
            if sig.score > 0 and cur_price < ema50[-1]:
                return None  # BUY signal but price below EMA50 — skip
            if sig.score < 0 and cur_price > ema50[-1]:
                return None  # SELL signal but price above EMA50 — skip

        stop_pts = max(atr * self.stop_atr_mult, self.min_stop_pips * self.pip)
        target_pts = max(atr * self.target_atr_mult, self.min_target_pips * self.pip)

        if sig.score > 0.1:
            return Signal(
                side=Side.BUY,
                confidence=sig.confidence,
                stop_distance_pts=stop_pts,
                target_distance_pts=target_pts,
                score=sig.score,
                reasons=sig.reasons,
                strategy_name=self.name,
            )
        elif sig.score < -0.1:
            return Signal(
                side=Side.SELL,
                confidence=sig.confidence,
                stop_distance_pts=stop_pts,
                target_distance_pts=target_pts,
                score=abs(sig.score),
                reasons=sig.reasons,
                strategy_name=self.name,
            )

        return None

    def on_open(self, position: OpenPosition, data: dict, equity: float) -> Signal | None:
        """Phase 2: Follow every trade. Move SL to lock profit."""
        closes = data.get("closes")
        if closes is None or len(closes) < self.min_bars:
            return None

        closes_arr = np.array(closes, dtype=float)
        cur_price = closes_arr[-1]
        pip = self.pip
        min_sl_distance = self.min_stop_pips * pip  # minimum SL distance from entry

        if position.side == Side.BUY:
            risk = position.entry_price - position.stop_price
            if risk <= 0:
                return None
            profit = cur_price - position.entry_price

            if profit > 0:
                new_sl = position.entry_price + profit * 0.5
                # Only move if above minimum distance from entry
                if new_sl >= position.entry_price + min_sl_distance and new_sl > position.stop_price:
                    return Signal(
                        side=Side.HOLD, confidence=1.0,
                        stop_distance_pts=0, target_distance_pts=0,
                        modify_sl=new_sl, modify_tp=position.target_price,
                        reasons=["lock_profit"],
                        strategy_name=self.name,
                    )

        elif position.side == Side.SELL:
            risk = position.stop_price - position.entry_price
            if risk <= 0:
                return None
            profit = position.entry_price - cur_price

            if profit > 0:
                new_sl = position.entry_price - profit * 0.5
                if new_sl <= position.entry_price - min_sl_distance and new_sl < position.stop_price:
                    return Signal(
                        side=Side.HOLD, confidence=1.0,
                        stop_distance_pts=0, target_distance_pts=0,
                        modify_sl=new_sl, modify_tp=position.target_price,
                        reasons=["lock_profit"],
                        strategy_name=self.name,
                    )

        return None

    def on_busy(self, data: dict, equity: float) -> Signal | None:
        return None

    def arm(self, signal: Signal) -> None:
        from datetime import timedelta
        self._armed = ArmedState(
            is_armed=True,
            armed_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            signal=signal,
        )

    def disarm(self) -> None:
        self._armed = ArmedState()

    def check_arm(self) -> Signal | None:
        if not self._armed.is_armed:
            return None
        if self._armed.expires_at and datetime.now(timezone.utc) > self._armed.expires_at:
            self.disarm()
            return None
        return self._armed.signal

    # ------------------------------------------------------------------
    # Indicators
    # ------------------------------------------------------------------

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> np.ndarray:
        alpha = 2 / (period + 1)
        ema = np.empty_like(data)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
        return ema

    @staticmethod
    def _rsi(data: np.ndarray, period: int) -> np.ndarray:
        deltas = np.diff(data)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = np.empty(len(data))
        avg_loss = np.empty(len(data))
        avg_gain[:] = np.nan
        avg_loss[:] = np.nan

        avg_gain[period] = np.mean(gains[:period])
        avg_loss[period] = np.mean(losses[:period])

        for i in range(period + 1, len(data)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period

        rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
        rsi = 100 - 100 / (1 + rs)
        rsi[:period] = np.nan
        return rsi

    @staticmethod
    def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> float:
        if len(closes) < period + 1:
            return 0.0

        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1]),
            ),
        )

        if len(tr) < period:
            return 0.0

        atr = np.mean(tr[-period:])
        return float(atr)
