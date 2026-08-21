"""Unified Bot Orchestrator — ties all components together.

Works with ANY account size. Uses:
- Account-relative risk sizing (SmallAccountAdvisor)
- 3-phase strategy lifecycle (Hyperliquid)
- Composite signal scoring (XAU actg338)
- Checkpoint recovery (mt5-quiet-automata)
- Latency-aware entry gating (KORStockScan)
- Regime classification (Capital41)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Any

from .risk.engine import RiskEngine, TradeIntent, RiskVerdict
from .strategies.base import Strategy, Signal, Side, Intent, OpenPosition, ArmedState
from .strategies.signals import SignalAggregator, CompositeSignal
from .execution.engine import ExecutionEngine, ConnectionState, LatencyState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trading mode gate (RESEARCH_SPEC.md Section 2)
# ---------------------------------------------------------------------------

class TradingMode(Enum):
    """System state gate — controls what the bot is allowed to do.

    RESEARCH:      Default. No orders placed. Data collection and analysis only.
    VALIDATION:    Paper-only. Signals logged but not executed. Backtest/Paper mode.
    PAPER:         Simulated execution. Real data, fake money.
    LIVE:          Real money. Requires explicit operator enablement.
    """
    RESEARCH = "RESEARCH"
    VALIDATION = "VALIDATION"
    PAPER = "PAPER"
    LIVE = "LIVE"


# Modes that are allowed to place real orders
_LIVE_MODES = {TradingMode.LIVE}

# Modes that are allowed to send simulated orders
_EXEC_MODES = _LIVE_MODES | {TradingMode.PAPER}

# Modes that allow signal generation (for logging/analysis)
# ALL modes allow signal generation — even RESEARCH logs signals for analysis
_SIGNAL_MODES = {TradingMode.RESEARCH, TradingMode.VALIDATION, TradingMode.PAPER, TradingMode.LIVE}


@dataclass
class BotConfig:
    """Runtime configuration — works with any account size."""
    # Trading mode gate (RESEARCH_SPEC.md Section 2)
    # Default: RESEARCH. Must be explicitly set to LIVE for real orders.
    # Can also be set via environment variable TRADING_MODE.
    trading_mode: TradingMode = TradingMode.RESEARCH

    # Connection
    mt5_path: str = ""
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""

    # Risk (all percentage-based)
    max_risk_per_trade_pct: float = 0.01
    max_daily_loss_pct: float = 0.03
    max_drawdown_pct: float = 0.10
    max_concurrent_positions: int = 3
    cooldown_base_minutes: int = 30
    vol_target_annual: float = 0.15
    max_lot: float = 0.02  # hard cap on lot size

    # Execution
    magic: int = 20250820
    slippage_pts: float = 5.0
    max_spread_pts: float = 3.0
    cycle_interval_sec: float = 1.0  # seconds between cycles

    # Strategy
    min_signal_confidence: float = 0.55
    instruments: list[str] = None

    # Paths
    state_dir: str = "state"
    log_dir: str = "logs"

    def __post_init__(self):
        if self.instruments is None:
            self.instruments = ["EURUSD"]

        # Allow environment variable override (highest priority)
        env_mode = os.environ.get("TRADING_MODE", "").upper()
        if env_mode and hasattr(TradingMode, env_mode):
            self.trading_mode = TradingMode(env_mode)
            logger.info("Trading mode from env: %s", self.trading_mode.value)

    @property
    def mode(self) -> TradingMode:
        return self.trading_mode

    @property
    def allows_entries(self) -> bool:
        """Can this mode place real orders?"""
        return self.trading_mode in _LIVE_MODES

    @property
    def allows_signals(self) -> bool:
        """Can this mode generate signals (for logging/research)?"""
        return self.trading_mode in _SIGNAL_MODES


class Bot:
    """Unified trading bot — works with any account size.

    Architecture stolen from multiple repos:
    - Risk engine: QuantWave vol targeting + inverse-vol sizing
    - Strategy lifecycle: Hyperliquid 3-phase (idle/open/busy)
    - Signal scoring: XAU composite multi-factor
    - Execution: mt5-quiet-automata checkpoint recovery
    - Entry gating: KORStockScan latency awareness
    - Regime: Capital41 regime matrix
    """

    def __init__(self, config: BotConfig):
        self.config = config
        self._strategies: list[Strategy] = []
        self._running = False

        # Initialize components
        state_dir = Path(config.state_dir)
        state_dir.mkdir(exist_ok=True)

        self.risk = RiskEngine(
            max_risk_per_trade_pct=config.max_risk_per_trade_pct,
            max_daily_loss_pct=config.max_daily_loss_pct,
            max_drawdown_pct=config.max_drawdown_pct,
            max_concurrent_positions=config.max_concurrent_positions,
            cooldown_base_minutes=config.cooldown_base_minutes,
            vol_target_annual=config.vol_target_annual,
            max_lot=config.max_lot,
        )

        self.execution = ExecutionEngine(
            mt5_path=config.mt5_path,
            login=config.mt5_login,
            password=config.mt5_password,
            server=config.mt5_server,
            magic=config.magic,
            checkpoint_dir=state_dir,
        )

        self.signals = SignalAggregator()

    # ------------------------------------------------------------------
    # Strategy management
    # ------------------------------------------------------------------

    def add_strategy(self, strategy: Strategy) -> None:
        self._strategies.append(strategy)
        logger.info("Strategy added: %s", strategy.name)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main trading loop."""
        mode = self.config.trading_mode
        logger.info("=" * 60)
        logger.info("  TRADING MODE: %s", mode.value)
        if mode == TradingMode.RESEARCH:
            logger.info("  No orders will be placed. Data collection and analysis only.")
        elif mode == TradingMode.VALIDATION:
            logger.info("  Signals will be logged but NOT executed.")
        elif mode == TradingMode.PAPER:
            logger.info("  Simulated execution. Real data, fake money.")
        elif mode == TradingMode.LIVE:
            logger.warning("  LIVE TRADING — REAL MONEY AT RISK")
        logger.info("=" * 60)

        if not self.execution.connect():
            logger.error("Failed to connect to MT5")
            return

        # Load state for crash recovery
        self.execution.load_state()
        pending = self.execution.recover_pending()
        if pending:
            logger.warning("Recovering pending order: %s", pending)

        self._running = True
        logger.info("Bot started — %d strategies loaded — mode=%s",
                     len(self._strategies), mode.value)

        try:
            while self._running:
                self._cycle()
                time.sleep(self.config.cycle_interval_sec)
        except KeyboardInterrupt:
            logger.info("Bot stopping...")
        except Exception as e:
            logger.error("Bot error: %s", e, exc_info=True)
        finally:
            self._stop()

    def _cycle(self) -> None:
        """Single cycle of the main loop."""
        import MetaTrader5 as mt5
        account = mt5.account_info()
        if account is None:
            logger.warning("Cannot read account info")
            return

        equity = account.equity

        # Check emergency conditions
        should_flat, reason = self.risk.should_flatten(equity)
        if should_flat:
            logger.warning("Emergency flatten: %s", reason)
            self._flatten_all()
            return

        # Get open positions from MT5 server (not in-memory cache)
        server_positions = mt5.positions_get()
        if server_positions is None:
            server_positions = []

        # Build lookup: symbol -> list of positions
        pos_by_symbol: dict[str, list] = {}
        for p in server_positions:
            if p.magic != self.config.magic:
                continue
            pos_by_symbol.setdefault(p.symbol, []).append(p)

        # Run each strategy independently
        for strategy in self._strategies:
            symbol = getattr(strategy, "symbol", "EURUSD")
            strat_data = self._get_data(strategy.name, symbol)
            my_positions = pos_by_symbol.get(symbol, [])

            if my_positions:
                # Phase 2: manage open positions for this symbol
                # In non-LIVE modes, we still read positions but don't modify them
                if self.config.allows_entries:
                    for pos in my_positions:
                        from bot.execution.engine import Position as PosObj
                        tracked = PosObj(
                            ticket=pos.ticket,
                            symbol=pos.symbol,
                            side="BUY" if pos.type == 0 else "SELL",
                            lots=pos.volume,
                            entry_price=pos.price_open,
                            stop_price=pos.sl,
                            target_price=pos.tp,
                            opened_at=datetime.fromtimestamp(pos.time, tz=timezone.utc),
                            magic=pos.magic,
                            pnl=pos.profit,
                        )
                        signal = strategy.on_open(
                            OpenPosition(
                                side=Side(tracked.side),
                                lots=tracked.lots,
                                entry_price=tracked.entry_price,
                                stop_price=tracked.stop_price,
                                target_price=tracked.target_price,
                                opened_at=tracked.opened_at,
                            ),
                            strat_data,
                            equity,
                        )
                        if signal and signal.side == Side.FLAT:
                            self._close_position(pos.ticket, "strategy_exit")
                        elif signal and (signal.modify_sl > 0 or signal.modify_tp > 0):
                            self.execution.modify_position(
                                pos.ticket,
                                new_sl=signal.modify_sl,
                                new_tp=signal.modify_tp,
                            )

            # Phase 1: scan for entry
            # Signal generation happens in all modes (for research/logging)
            # But execution only happens in LIVE mode
            my_pos_count = len(pos_by_symbol.get(symbol, []))
            total_positions = sum(len(v) for v in pos_by_symbol.values())
            if total_positions < self.config.max_concurrent_positions and my_pos_count == 0:
                armed_signal = strategy.check_arm()
                if armed_signal:
                    signal = armed_signal
                else:
                    signal = strategy.on_idle(strat_data, equity)

                if signal and signal.confidence >= self.config.min_signal_confidence:
                    if self.config.allows_entries:
                        self._try_entry(strategy, signal, equity, symbol)
                        break  # max 1 entry per cycle
                    else:
                        # Research/Validation mode: log the signal but don't execute
                        logger.info(
                            "[MODE:%s] Signal generated: %s %s conf=%.2f score=%.3f reasons=%s",
                            self.config.trading_mode.value,
                            symbol, signal.side.value, signal.confidence,
                            signal.score, signal.reasons,
                        )

        self.execution.reconcile()
        self.execution.save_state()

    def _try_entry(self, strategy: Strategy, signal: Signal, equity: float, symbol: str = None) -> None:
        """Attempt to enter a position after risk check."""
        # HARD SAFETY NET: refuse entries in non-LIVE modes
        if not self.config.allows_entries:
            logger.warning(
                "BLOCKED: _try_entry called in %s mode — this should not happen",
                self.config.trading_mode.value,
            )
            return

        if symbol is None:
            symbol = getattr(strategy, "symbol", self.config.instruments[0] if self.config.instruments else "EURUSD")

        # Check latency state
        lat_state = self.execution.latency_state()
        if lat_state == LatencyState.DANGER:
            logger.info("Skipping entry — latency state DANGER")
            return

        # Build trade intent
        price = self._get_current_price(symbol, signal.side)
        if price <= 0:
            return

        stop_price = (
            price - signal.stop_distance_pts if signal.side == Side.BUY
            else price + signal.stop_distance_pts
        )
        target_price = (
            price + signal.target_distance_pts if signal.side == Side.BUY
            else price - signal.target_distance_pts
        )

        # Get contract size for this symbol
        import MetaTrader5 as mt5
        sym_info = mt5.symbol_info(symbol)
        contract_size = sym_info.trade_contract_size if sym_info else 100000

        # Risk-based sizing
        lots = self.risk.risk_based_size(
            equity, signal.stop_distance_pts, contract_size, price,
        )

        intent = TradeIntent(
            symbol=symbol,
            side=signal.side.value,
            lots=lots,
            entry_price=price,
            stop_price=stop_price,
            target_price=target_price,
            contract_size=contract_size,
            signal_score=signal.score,
            strategy=strategy.name,
            reason=", ".join(signal.reasons[:3]),
        )

        # Pre-trade risk check
        verdict = self.risk.check_pre_trade(
            intent, equity, 0.0,  # margin_per_lot TODO
            len(self.execution.get_positions()),
        )

        if not verdict.approved:
            logger.info("Risk rejected: %s", verdict.reasons)
            return

        if verdict.warnings:
            logger.info("Risk warnings: %s", verdict.warnings)

        # Execute
        result = self.execution.submit_order(
            symbol=intent.symbol,
            side=intent.side,
            lots=verdict.lots,
            stop_price=intent.stop_price,
            target_price=intent.target_price,
        )

        if result.success:
            self.risk.record_position_open()
            logger.info("Entry filled: %s", result)

    def _close_position(self, ticket: int, reason: str) -> bool:
        """Close a position by ticket from MT5 server."""
        import MetaTrader5 as mt5
        mt5_pos = mt5.positions_get(ticket=ticket)
        if not mt5_pos:
            return False

        pos = mt5_pos[0]
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return False

        price = tick.bid if pos.type == 0 else tick.ask
        order_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY

        sym_info = mt5.symbol_info(pos.symbol)
        filling = mt5.ORDER_FILLING_FOK
        if sym_info:
            if sym_info.filling_mode & 2:
                filling = mt5.ORDER_FILLING_IOC
            elif sym_info.filling_mode & 4:
                filling = mt5.ORDER_FILLING_RETURN

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": self.config.magic,
            "comment": "bot_close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
            "position": ticket,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            pnl = pos.profit
            self.risk.record_trade(pnl, mt5.account_info().equity)
            self.execution.close_position(ticket, reason)
            logger.info("Position closed: ticket=%d reason=%s pnl=%.2f", ticket, reason, pnl)
            return True
        else:
            error = result.comment if result else "Unknown"
            logger.warning("Close failed: ticket=%d error=%s", ticket, error)
            return False

    def _flatten_all(self) -> None:
        """Emergency flatten all positions from MT5 server."""
        import MetaTrader5 as mt5
        server_positions = mt5.positions_get()
        if not server_positions:
            self.risk.emergency_flatten()
            return
        for pos in server_positions:
            if pos.magic == self.config.magic:
                self._close_position(pos.ticket, "emergency_flatten")
        self.risk.emergency_flatten()

    def _get_data(self, strategy_name: str, symbol: str = None) -> dict[str, Any]:
        """Get market data for strategy."""
        return {}

    def _get_current_price(self, symbol: str, side: Side) -> float:
        """Get current mid price for a symbol."""
        import MetaTrader5 as mt5
        if symbol is None:
            symbol = self.config.instruments[0] if self.config.instruments else "EURUSD"
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return 0.0
        return (tick.ask + tick.bid) / 2.0

    def _stop(self) -> None:
        """Clean shutdown."""
        self._running = False
        self.execution.save_state()
        self.execution.disconnect()
        logger.info("Bot stopped")
