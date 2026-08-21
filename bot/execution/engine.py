"""Execution Layer — order management, position tracking, checkpoint recovery.

Stolen gems:
- Checkpoint recovery from mt5-quiet-automata (write-ahead log pattern)
- Latency-aware entry gating from KORStockScan (SAFE/CAUTION/DANGER)
- State machine for connection lifecycle from ScalpingBot
"""

from __future__ import annotations

import json
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """Connection lifecycle state machine — stolen from ScalpingBot."""
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    BOT_STARTING = "BOT_STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


class LatencyState(Enum):
    """Entry gating states — stolen from KORStockScan."""
    SAFE = "SAFE"
    CAUTION = "CAUTION"
    DANGER = "DANGER"


@dataclass
class OrderResult:
    """Result of order submission."""
    success: bool
    ticket: int = 0
    price: float = 0.0
    lots: float = 0.0
    error: str = ""
    latency_ms: float = 0.0


@dataclass
class Position:
    """Tracked open position."""
    ticket: int
    symbol: str
    side: str
    lots: float
    entry_price: float
    stop_price: float
    target_price: float
    opened_at: datetime
    magic: int = 0
    pnl: float = 0.0
    closed: bool = False
    closed_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""


class ExecutionEngine:
    """Order execution with checkpoint recovery and latency awareness.

    Stolen gems:
    - Checkpoint recovery: write intent to disk before execution
    - Latency gating: SAFE/CAUTION/DANGER based on recent fill quality
    - State machine: formal lifecycle management
    """

    def __init__(
        self,
        mt5_path: str = "",
        login: int = 0,
        password: str = "",
        server: str = "",
        magic: int = 20250820,
        checkpoint_dir: Optional[Path] = None,
    ):
        self.mt5_path = mt5_path
        self.login = login
        self.password = password
        self.server = server
        self.magic = magic
        self.checkpoint_dir = checkpoint_dir or Path("state")
        self.checkpoint_dir.mkdir(exist_ok=True)

        self._state = ConnectionState.DISCONNECTED
        self._positions: dict[int, Position] = {}
        self._latency_history: list[float] = []
        self._slippage_history: list[float] = []
        self._mt5 = None

    # ------------------------------------------------------------------
    # Connection lifecycle (ScalpingBot stolen gem)
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to MT5 terminal."""
        self._state = ConnectionState.CONNECTING
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5

            # If already initialized and on the right terminal, reuse
            try:
                existing = mt5.account_info()
                if existing:
                    same_server = (not self.server) or (self.server in existing.server) or (existing.server in self.server)
                    same_login = (not self.login) or (existing.login == self.login)
                    if same_server and same_login:
                        self._state = ConnectionState.CONNECTED
                        logger.info("MT5 already connected: %s | Login: %s", existing.server, existing.login)
                        return True
            except Exception:
                pass

            kwargs = {}
            if self.login:
                kwargs["login"] = self.login
            if self.password:
                kwargs["password"] = self.password
            if self.server:
                kwargs["server"] = self.server
            if self.mt5_path:
                kwargs["path"] = self.mt5_path

            if not mt5.initialize(**kwargs):
                self._state = ConnectionState.DISCONNECTED
                return False

            self._state = ConnectionState.CONNECTED
            logger.info("MT5 connected: %s", self.server)
            return True
        except Exception as e:
            logger.error("MT5 connect failed: %s", e)
            self._state = ConnectionState.DISCONNECTED
            return False

    def disconnect(self) -> None:
        self._state = ConnectionState.DISCONNECTED

    @property
    def state(self) -> ConnectionState:
        return self._state

    # ------------------------------------------------------------------
    # Latency-aware entry gating (KORStockScan stolen gem)
    # ------------------------------------------------------------------

    def latency_state(self) -> LatencyState:
        """Classify current execution quality based on recent fills."""
        if len(self._latency_history) < 10:
            return LatencyState.SAFE

        recent = self._latency_history[-10:]
        avg_latency = sum(recent) / len(recent)

        if avg_latency > 3000:
            return LatencyState.DANGER
        elif avg_latency > 1500:
            return LatencyState.CAUTION
        return LatencyState.SAFE

    # ------------------------------------------------------------------
    # Checkpoint recovery (mt5-quiet-automata stolen gem)
    # ------------------------------------------------------------------

    def checkpoint(self, intent: dict) -> None:
        """Write trade intent to disk BEFORE execution (write-ahead log)."""
        path = self.checkpoint_dir / "pending_order.json"
        intent["timestamp"] = datetime.now(timezone.utc).isoformat()
        with open(path, "w") as f:
            json.dump(intent, f, indent=2)

    def clear_checkpoint(self) -> None:
        """Clear pending order checkpoint after successful execution."""
        path = self.checkpoint_dir / "pending_order.json"
        if path.exists():
            path.unlink()

    def recover_pending(self) -> Optional[dict]:
        """Check for unfinished orders from crash recovery."""
        path = self.checkpoint_dir / "pending_order.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return None

    # ------------------------------------------------------------------
    # Order submission
    # ------------------------------------------------------------------

    def submit_order(
        self,
        symbol: str,
        side: str,
        lots: float,
        stop_price: float,
        target_price: float,
    ) -> OrderResult:
        """Submit market order with checkpoint and retry.

        Stolen: write intent to disk first, then execute, then clear.
        """
        if self._mt5 is None:
            return OrderResult(success=False, error="Not connected")

        mt5 = self._mt5

        # Checkpoint before execution
        self.checkpoint({
            "symbol": symbol,
            "side": side,
            "lots": lots,
            "stop_price": stop_price,
            "target_price": target_price,
        })

        # Get current price and symbol info
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            self.clear_checkpoint()
            return OrderResult(success=False, error=f"No tick for {symbol}")

        sym_info = mt5.symbol_info(symbol)

        # Clamp lots to symbol's min/max/step
        if sym_info:
            min_lot = sym_info.volume_min
            max_lot = sym_info.volume_max
            lot_step = sym_info.volume_step
            lots = max(min_lot, min(lots, max_lot))
            if lot_step > 0:
                lots = round(lots / lot_step) * lot_step
            lots = round(lots, 2)

        price = tick.ask if side == "BUY" else tick.bid
        order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL

        # Auto-detect filling mode from symbol (bitmask: 1=FOK, 2=IOC, 3=both)
        filling = mt5.ORDER_FILLING_FOK  # safe default
        if sym_info:
            if sym_info.filling_mode & 2:
                filling = mt5.ORDER_FILLING_IOC
            elif sym_info.filling_mode & 1:
                filling = mt5.ORDER_FILLING_FOK
            elif sym_info.filling_mode & 4:
                filling = mt5.ORDER_FILLING_RETURN

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lots,
            "type": order_type,
            "price": price,
            "sl": stop_price,
            "tp": target_price,
            "deviation": 5,
            "magic": self.magic,
            "comment": f"SAA_{self.magic:08d}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        start = time.time()
        for attempt in range(3):
            try:
                result = mt5.order_send(request)
                latency = (time.time() - start) * 1000

                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    fill_price = result.price if result.price > 0 else price
                    self._latency_history.append(latency)
                    self.clear_checkpoint()
                    slippage = abs(fill_price - price)
                    self._slippage_history.append(slippage)

                    pos = Position(
                        ticket=result.order,
                        symbol=symbol,
                        side=side,
                        lots=lots,
                        entry_price=fill_price,
                        stop_price=stop_price,
                        target_price=target_price,
                        opened_at=datetime.now(timezone.utc),
                        magic=self.magic,
                    )
                    self._positions[result.order] = pos

                    logger.info(
                        "Order filled: %s %s %.2f lots @ %.5f (latency %.0fms, slip %.2f)",
                        symbol, side, lots, fill_price, latency, slippage,
                    )
                    return OrderResult(
                        success=True,
                        ticket=result.order,
                        price=result.price,
                        lots=lots,
                        latency_ms=latency,
                    )
                else:
                    error = result.comment if result else "Unknown error"
                    logger.warning("Order attempt %d failed: %s", attempt + 1, error)
                    if attempt < 2:
                        time.sleep(0.5)
            except Exception as e:
                logger.error("Order exception: %s", e)
                if attempt < 2:
                    time.sleep(0.5)

        self.clear_checkpoint()
        return OrderResult(success=False, error="All attempts failed")

    # ------------------------------------------------------------------
    # Position tracking
    # ------------------------------------------------------------------

    def get_positions(self, symbol: Optional[str] = None) -> list[Position]:
        """Get tracked positions."""
        positions = list(self._positions.values())
        if symbol:
            positions = [p for p in positions if p.symbol == symbol]
        return [p for p in positions if not p.closed]

    def modify_position(self, ticket: int, new_sl: float = 0.0, new_tp: float = 0.0) -> bool:
        """Modify SL/TP of an open position via MT5."""
        if self._mt5 is None:
            return False

        mt5 = self._mt5
        pos = self._positions.get(ticket)
        if not pos:
            return False

        # Get current server values to avoid rejection
        mt5_pos = mt5.positions_get(ticket=ticket)
        if not mt5_pos:
            return False

        server_pos = mt5_pos[0]
        sl = new_sl if new_sl > 0 else server_pos.sl
        tp = new_tp if new_tp > 0 else server_pos.tp

        # Skip if nothing changed
        if abs(sl - server_pos.sl) < 0.00001 and abs(tp - server_pos.tp) < 0.00001:
            return True

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": sl,
            "tp": tp,
            "magic": self.magic,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            pos.stop_price = sl if new_sl > 0 else pos.stop_price
            pos.target_price = tp if new_tp > 0 else pos.target_price
            logger.info("Position modified: ticket=%d sl=%.5f tp=%.5f", ticket, sl, tp)
            return True
        else:
            error = result.comment if result else "Unknown"
            logger.warning("Modify failed: ticket=%d error=%s", ticket, error)
            return False

    def close_position(self, ticket: int, reason: str = "") -> bool:
        """Mark position as closed."""
        pos = self._positions.get(ticket)
        if pos and not pos.closed:
            pos.closed = True
            pos.closed_at = datetime.now(timezone.utc)
            pos.exit_reason = reason
            return True
        return False

    def reconcile(self) -> list[dict]:
        """Reconcile tracked positions with MT5 server state."""
        if self._mt5 is None:
            return []

        mt5 = self._mt5
        server_positions = mt5.positions_get()
        if server_positions is None:
            return []

        orphans = []
        server_tickets = {p.ticket for p in server_positions}

        for ticket, pos in self._positions.items():
            if not pos.closed and ticket not in server_tickets:
                pos.closed = True
                pos.closed_at = datetime.now(timezone.utc)
                pos.exit_reason = "orphan_reconcile"
                orphans.append({
                    "ticket": ticket,
                    "symbol": pos.symbol,
                    "action": "orphan_detected",
                })
                logger.warning("Orphan detected: ticket %d", ticket)

        return orphans

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def save_state(self) -> None:
        """Save positions to disk for crash recovery."""
        path = self.checkpoint_dir / "positions.json"
        data = {}
        for ticket, pos in self._positions.items():
            data[str(ticket)] = {
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "side": pos.side,
                "lots": pos.lots,
                "entry_price": pos.entry_price,
                "stop_price": pos.stop_price,
                "target_price": pos.target_price,
                "opened_at": pos.opened_at.isoformat(),
                "magic": pos.magic,
                "closed": pos.closed,
                "closed_at": pos.closed_at.isoformat() if pos.closed_at else None,
                "exit_price": pos.exit_price,
                "exit_reason": pos.exit_reason,
            }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def load_state(self) -> None:
        """Load positions from disk for crash recovery."""
        path = self.checkpoint_dir / "positions.json"
        if not path.exists():
            return
        with open(path) as f:
            data = json.load(f)
        for ticket_str, pdata in data.items():
            ticket = int(ticket_str)
            pos = Position(
                ticket=pdata["ticket"],
                symbol=pdata["symbol"],
                side=pdata["side"],
                lots=pdata["lots"],
                entry_price=pdata["entry_price"],
                stop_price=pdata["stop_price"],
                target_price=pdata["target_price"],
                opened_at=datetime.fromisoformat(pdata["opened_at"]),
                magic=pdata.get("magic", self.magic),
                closed=pdata.get("closed", False),
                closed_at=(
                    datetime.fromisoformat(pdata["closed_at"])
                    if pdata.get("closed_at") else None
                ),
                exit_price=pdata.get("exit_price"),
                exit_reason=pdata.get("exit_reason", ""),
            )
            self._positions[ticket] = pos
