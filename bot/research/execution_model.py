"""Backtest execution engine — simulates realistic order execution.

Enforces:
- BUY enters on ASK, exits on BID
- SELL enters on BID, exits on ASK
- Spread, slippage, commission
- Stop execution (next bar high/low check)
- Target execution
- Minimum stop distance
- No same-bar future information
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .data_pipeline import Bar, SymbolData
from .cost_model import CostModel, CostScenario, TradeCost

logger = logging.getLogger(__name__)


class PositionSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class ExitReason(Enum):
    STOP = "STOP"
    TARGET = "TARGET"
    TIME = "TIME"
    SIGNAL = "SIGNAL"
    EMERGENCY = "EMERGENCY"


@dataclass
class BacktestPosition:
    """Open position in the backtester."""
    symbol: str
    side: PositionSide
    lots: float
    entry_price: float
    entry_bar_idx: int
    entry_time: datetime
    stop_price: float
    target_price: float
    cost: TradeCost
    # Tracking
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    mfe: float = 0.0  # maximum favorable excursion
    maе: float = 0.0  # maximum adverse excursion (renamed to avoid encoding issue)
    bars_held: int = 0
    # SL lock tracking
    sl_lock_active: bool = False
    sl_lock_price: float = 0.0


@dataclass
class BacktestTrade:
    """Completed trade record."""
    symbol: str
    side: str
    lots: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    entry_bar_idx: int
    exit_bar_idx: int
    stop_price: float
    target_price: float
    pnl: float
    pnl_after_cost: float
    r_multiple: float
    bars_held: int
    mfe: float
    maе: float
    exit_reason: str
    cost: TradeCost
    # Signal metadata (for analysis)
    signal_score: float = 0.0
    signal_confidence: float = 0.0
    signal_reasons: list[str] = field(default_factory=list)


@dataclass
class BacktestState:
    """Current state of the backtester."""
    equity: float
    initial_equity: float
    peak_equity: float
    positions: list[BacktestPosition]
    closed_trades: list[BacktestTrade]
    bar_idx: int
    current_bar: Optional[Bar] = None
    # Tracking
    total_pnl: float = 0.0
    total_cost: float = 0.0
    winning_trades: int = 0
    losing_trades: int = 0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0


class BacktestEngine:
    """Realistic backtest execution engine.

    Enforces bid/ask semantics, spread costs, and stop/target execution.
    """

    def __init__(
        self,
        initial_equity: float = 10000.0,
        max_positions: int = 1,
        max_hold_bars: int = 48,  # 4 hours on M5
        cost_scenario: CostScenario = CostScenario.REALISTIC,
        sl_lock_multiplier: float = 0.5,
        min_sl_distance_pts: float = 0.0002,  # min 2 pips for EURUSD
    ):
        self.initial_equity = initial_equity
        self.max_positions = max_positions
        self.max_hold_bars = max_hold_bars
        self.cost_scenario = cost_scenario
        self.sl_lock_multiplier = sl_lock_multiplier
        self.min_sl_distance_pts = min_sl_distance_pts

        self._state: Optional[BacktestState] = None
        self._cost_model: Optional[CostModel] = None

    def initialize(self, data: SymbolData, cost_model: CostModel) -> None:
        """Initialize backtester for a symbol."""
        self._cost_model = cost_model
        self._usd_per_quote = getattr(data, 'usd_per_quote_unit', 1.0)
        self._state = BacktestState(
            equity=self.initial_equity,
            initial_equity=self.initial_equity,
            peak_equity=self.initial_equity,
            positions=[],
            closed_trades=[],
            bar_idx=0,
        )

    def _to_usd(self, quote_amount: float) -> float:
        """Convert PnL from quote currency to USD."""
        return quote_amount * self._usd_per_quote

    def run(
        self,
        data: SymbolData,
        strategy_fn,
        cost_model: CostModel,
    ) -> list[BacktestTrade]:
        """Run full backtest.

        Args:
            data: historical data
            strategy_fn: callable(data_slice, state) -> Optional[dict]
                Returns dict with keys: side, stop_distance_pts, target_distance_pts, score, confidence, reasons
                Or None for no signal
            cost_model: cost configuration

        Returns:
            list of completed trades
        """
        self.initialize(data, cost_model)
        bars = data.bars
        state = self._state

        for i in range(len(bars)):
            state.bar_idx = i
            state.current_bar = bars[i]

            # 1. Check stops and targets for open positions
            self._check_exits(bars, i)

            # 2. Apply SL lock for profitable positions
            self._apply_sl_lock(bars, i)

            # 3. Check max hold time
            self._check_time_exits(i)

            # 4. Update position tracking (MFE, MAE)
            self._update_positions(bars, i)

            # 5. Generate signal if no position (and enough bars passed)
            if len(state.positions) < self.max_positions and i >= 30:
                signal = self._get_signal(data, i, strategy_fn)
                if signal is not None:
                    self._enter_position(data, i, signal)

            # 6. Update equity tracking
            self._update_equity(bars, i)

        # Close any remaining positions at last bar
        self._close_all_positions(bars, len(bars) - 1)

        return state.closed_trades

    def _get_signal(self, data: SymbolData, bar_idx: int, strategy_fn):
        """Get signal from strategy at bar close.

        Strategy sees bars[:bar_idx+1] (up to and including current bar).
        Signal is generated at bar close.
        Execution happens on next bar (enforced by caller).
        """
        # Provide data slice up to current bar
        slice_data = SymbolData(
            symbol=data.symbol,
            bars=data.bars[:bar_idx + 1],
            pip=data.pip,
            contract_size=data.contract_size,
            point=data.point,
            volume_step=data.volume_step,
            volume_min=data.volume_min,
            volume_max=data.volume_max,
            base_spread_points=data.base_spread_points,
        )

        try:
            return strategy_fn(slice_data, self._state)
        except Exception as e:
            logger.debug("Strategy error at bar %d: %s", bar_idx, e)
            return None

    def _enter_position(self, data: SymbolData, bar_idx: int, signal: dict) -> None:
        """Enter position on next bar open.

        BUY: enter on ASK = next_bar.open + spread/2
        SELL: enter on BID = next_bar.open - spread/2
        """
        state = self._state
        bars = data.bars

        # Entry happens on next bar's open
        if bar_idx + 1 >= len(bars):
            return  # no next bar

        next_bar = bars[bar_idx + 1]
        side = signal["side"]

        # Compute entry price with spread
        if side == "BUY":
            entry_price = next_bar.open + next_bar.spread_points / 2.0  # BUY on ask
        else:
            entry_price = next_bar.open - next_bar.spread_points / 2.0  # SELL on bid

        # Compute stop and target
        stop_distance = signal.get("stop_distance_pts", 0.0)
        target_distance = signal.get("target_distance_pts", 0.0)

        if stop_distance <= 0 or target_distance <= 0:
            return

        # Minimum stop distance
        if self.min_sl_distance_pts > 0:
            stop_distance = max(stop_distance, self.min_sl_distance_pts)

        if side == "BUY":
            stop_price = entry_price - stop_distance
            target_price = entry_price + target_distance
        else:
            stop_price = entry_price + stop_distance
            target_price = entry_price - target_distance

        # Compute cost
        cost = self._cost_model.compute_cost(
            side=side,
            entry_price=entry_price,
            stop_distance_pts=stop_distance,
            lots=signal.get("lots", 0.01),
            scenario=self.cost_scenario,
            atr=signal.get("atr", 0.0),
        )

        # Position sizing (fixed 0.01 for now)
        lots = signal.get("lots", 0.01)

        position = BacktestPosition(
            symbol=data.symbol,
            side=PositionSide.BUY if side == "BUY" else PositionSide.SELL,
            lots=lots,
            entry_price=entry_price,
            entry_bar_idx=bar_idx + 1,
            entry_time=bars[bar_idx + 1].timestamp,
            stop_price=stop_price,
            target_price=target_price,
            cost=cost,
            current_price=entry_price,
        )

        state.positions.append(position)

    def _check_exits(self, bars: list[Bar], bar_idx: int) -> None:
        """Check stop and target for open positions.

        Uses next bar's high/low to determine fill.
        """
        state = self._state
        closed = []

        for pos in state.positions:
            if pos.entry_bar_idx >= bar_idx:
                continue  # entered on this bar, can't exit same bar

            bar = bars[bar_idx]
            exit_price = None
            exit_reason = None

            if pos.side == PositionSide.BUY:
                # Stop hit: bar low <= stop
                if bar.low <= pos.stop_price:
                    exit_price = pos.stop_price
                    exit_reason = ExitReason.STOP
                # Target hit: bar high >= target
                elif bar.high >= pos.target_price:
                    exit_price = pos.target_price
                    exit_reason = ExitReason.TARGET

            else:  # SELL
                # Stop hit: bar high >= stop
                if bar.high >= pos.stop_price:
                    exit_price = pos.stop_price
                    exit_reason = ExitReason.STOP
                # Target hit: bar low <= target
                elif bar.low <= pos.target_price:
                    exit_price = pos.target_price
                    exit_reason = ExitReason.TARGET

            if exit_price is not None:
                # Compute PnL
                if pos.side == PositionSide.BUY:
                    # Exit on BID
                    exit_price_actual = exit_price - bar.spread_points / 2.0
                    raw_pnl_quote = (exit_price_actual - pos.entry_price) * pos.lots * pos.cost.contract_size
                else:
                    # Exit on ASK
                    exit_price_actual = exit_price + bar.spread_points / 2.0
                    raw_pnl_quote = (pos.entry_price - exit_price_actual) * pos.lots * pos.cost.contract_size

                cost_quote = pos.cost.total_round_trip * pos.lots * pos.cost.contract_size
                raw_pnl = self._to_usd(raw_pnl_quote)
                pnl_after_cost = raw_pnl - self._to_usd(cost_quote)

                # R-multiple
                risk_quote = abs(pos.entry_price - pos.stop_price) * pos.lots * pos.cost.contract_size
                risk_dollars = self._to_usd(risk_quote)
                r_multiple = raw_pnl / risk_dollars if risk_dollars > 0 else 0.0

                trade = BacktestTrade(
                    symbol=pos.symbol,
                    side=pos.side.value,
                    lots=pos.lots,
                    entry_price=pos.entry_price,
                    exit_price=exit_price_actual,
                    entry_time=pos.entry_time,
                    exit_time=bar.timestamp,
                    entry_bar_idx=pos.entry_bar_idx,
                    exit_bar_idx=bar_idx,
                    stop_price=pos.stop_price,
                    target_price=pos.target_price,
                    pnl=raw_pnl,
                    pnl_after_cost=pnl_after_cost,
                    r_multiple=r_multiple,
                    bars_held=bar_idx - pos.entry_bar_idx,
                    mfe=pos.mfe,
                    maе=pos.maе,
                    exit_reason=exit_reason.value,
                    cost=pos.cost,
                    signal_score=0.0,  # filled by caller if available
                    signal_confidence=0.0,
                    signal_reasons=[],
                )

                state.closed_trades.append(trade)
                state.total_pnl += raw_pnl
                state.total_cost += self._to_usd(cost_quote)
                if raw_pnl > 0:
                    state.winning_trades += 1
                else:
                    state.losing_trades += 1

                closed.append(pos)

        for pos in closed:
            state.positions.remove(pos)

    def _apply_sl_lock(self, bars: list[Bar], bar_idx: int) -> None:
        """Apply SL lock: move SL to lock 50% of unrealized profit."""
        state = self._state
        if not state:
            return
        for pos in state.positions:
            if pos.entry_bar_idx >= bar_idx:
                continue

            bar = bars[bar_idx]
            min_distance = self.min_sl_distance_pts

            if pos.side == PositionSide.BUY:
                current = bar.bid
                profit = current - pos.entry_price
                if profit > 0:
                    new_sl = pos.entry_price + profit * self.sl_lock_multiplier
                    if new_sl >= pos.entry_price + min_distance and new_sl > pos.stop_price:
                        pos.stop_price = new_sl
                        pos.sl_lock_active = True
            else:
                current = bar.ask
                profit = pos.entry_price - current
                if profit > 0:
                    new_sl = pos.entry_price - profit * self.sl_lock_multiplier
                    if new_sl <= pos.entry_price - min_distance and new_sl < pos.stop_price:
                        pos.stop_price = new_sl
                        pos.sl_lock_active = True

    def _check_time_exits(self, bar_idx: int) -> None:
        """Exit positions that exceed max hold time."""
        state = self._state
        closed = []

        for pos in state.positions:
            if bar_idx - pos.entry_bar_idx >= self.max_hold_bars:
                # Time exit at current price
                bar = state.current_bar
                if pos.side == PositionSide.BUY:
                    exit_price = bar.bid
                    raw_pnl_quote = (exit_price - pos.entry_price) * pos.lots * pos.cost.contract_size
                else:
                    exit_price = bar.ask
                    raw_pnl_quote = (pos.entry_price - exit_price) * pos.lots * pos.cost.contract_size

                cost_quote = pos.cost.total_round_trip * pos.lots * pos.cost.contract_size
                raw_pnl = self._to_usd(raw_pnl_quote)
                pnl_after_cost = raw_pnl - self._to_usd(cost_quote)

                risk_quote = abs(pos.entry_price - pos.stop_price) * pos.lots * pos.cost.contract_size
                risk_dollars = self._to_usd(risk_quote)
                r_multiple = raw_pnl / risk_dollars if risk_dollars > 0 else 0.0

                trade = BacktestTrade(
                    symbol=pos.symbol,
                    side=pos.side.value,
                    lots=pos.lots,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    entry_time=pos.entry_time,
                    exit_time=bar.timestamp,
                    entry_bar_idx=pos.entry_bar_idx,
                    exit_bar_idx=bar_idx,
                    stop_price=pos.stop_price,
                    target_price=pos.target_price,
                    pnl=raw_pnl,
                    pnl_after_cost=pnl_after_cost,
                    r_multiple=r_multiple,
                    bars_held=bar_idx - pos.entry_bar_idx,
                    mfe=pos.mfe,
                    maе=pos.maе,
                    exit_reason=ExitReason.TIME.value,
                    cost=pos.cost,
                )

                state.closed_trades.append(trade)
                state.total_pnl += raw_pnl
                state.total_cost += self._to_usd(cost_quote)
                if raw_pnl > 0:
                    state.winning_trades += 1
                else:
                    state.losing_trades += 1

                closed.append(pos)

        for pos in closed:
            state.positions.remove(pos)

    def _update_positions(self, bars: list[Bar], bar_idx: int) -> None:
        """Update MFE/MAE for open positions."""
        bar = bars[bar_idx]

        for pos in self._state.positions:
            if pos.entry_bar_idx >= bar_idx:
                continue

            if pos.side == PositionSide.BUY:
                high_pnl = self._to_usd((bar.high - pos.entry_price) * pos.lots * pos.cost.contract_size)
                low_pnl = self._to_usd((bar.low - pos.entry_price) * pos.lots * pos.cost.contract_size)
            else:
                high_pnl = self._to_usd((pos.entry_price - bar.low) * pos.lots * pos.cost.contract_size)
                low_pnl = self._to_usd((pos.entry_price - bar.high) * pos.lots * pos.cost.contract_size)

            pos.mfe = max(pos.mfe, high_pnl)
            pos.maе = max(pos.maе, abs(low_pnl))
            pos.bars_held = bar_idx - pos.entry_bar_idx

    def _update_equity(self, bars: list[Bar], bar_idx: int) -> None:
        """Update equity curve tracking."""
        state = self._state
        bar = bars[bar_idx]

        # Compute unrealized PnL
        unrealized = 0.0
        for pos in state.positions:
            if pos.side == PositionSide.BUY:
                unrealized += self._to_usd((bar.bid - pos.entry_price) * pos.lots * pos.cost.contract_size)
            else:
                unrealized += self._to_usd((pos.entry_price - bar.ask) * pos.lots * pos.cost.contract_size)

        current_equity = state.initial_equity + state.total_pnl + unrealized
        state.equity = current_equity

        if current_equity > state.peak_equity:
            state.peak_equity = current_equity

        dd = (state.peak_equity - current_equity) / state.peak_equity if state.peak_equity > 0 else 0
        state.max_drawdown = max(state.max_drawdown, state.peak_equity - current_equity)
        state.max_drawdown_pct = max(state.max_drawdown_pct, dd)

    def _close_all_positions(self, bars: list[Bar], bar_idx: int) -> None:
        """Force-close all remaining positions."""
        state = self._state
        bar = bars[bar_idx]

        for pos in list(state.positions):
            if pos.side == PositionSide.BUY:
                exit_price = bar.bid
                raw_pnl_quote = (exit_price - pos.entry_price) * pos.lots * pos.cost.contract_size
            else:
                exit_price = bar.ask
                raw_pnl_quote = (pos.entry_price - exit_price) * pos.lots * pos.cost.contract_size

            cost_quote = pos.cost.total_round_trip * pos.lots * pos.cost.contract_size
            raw_pnl = self._to_usd(raw_pnl_quote)
            pnl_after_cost = raw_pnl - self._to_usd(cost_quote)

            risk_quote = abs(pos.entry_price - pos.stop_price) * pos.lots * pos.cost.contract_size
            risk_dollars = self._to_usd(risk_quote)
            r_multiple = raw_pnl / risk_dollars if risk_dollars > 0 else 0.0

            trade = BacktestTrade(
                symbol=pos.symbol,
                side=pos.side.value,
                lots=pos.lots,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                entry_time=pos.entry_time,
                exit_time=bar.timestamp,
                entry_bar_idx=pos.entry_bar_idx,
                exit_bar_idx=bar_idx,
                stop_price=pos.stop_price,
                target_price=pos.target_price,
                pnl=raw_pnl,
                pnl_after_cost=pnl_after_cost,
                r_multiple=r_multiple,
                bars_held=bar_idx - pos.entry_bar_idx,
                mfe=pos.mfe,
                maе=pos.maе,
                exit_reason="END_OF_DATA",
                cost=pos.cost,
            )

            state.closed_trades.append(trade)
            state.total_pnl += raw_pnl
            state.total_cost += self._to_usd(cost_quote)
            if raw_pnl > 0:
                state.winning_trades += 1
            else:
                state.losing_trades += 1

        state.positions.clear()
