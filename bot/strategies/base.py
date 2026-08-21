"""Strategy Framework — 3-phase lifecycle stolen from Hyperliquid.

Every strategy implements: on_idle (scan), on_open (manage), on_busy (pending).
Plus Arm/Disarm delayed entry for "patience" as a first-class concept.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, Any

logger = logging.getLogger(__name__)


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"
    FLAT = "FLAT"
    HOLD = "HOLD"


class Intent(Enum):
    """Composable action vocabulary stolen from Hyperliquid."""
    OPEN = "OPEN"
    REDUCE = "REDUCE"
    FLATTEN = "FLATTEN"
    ARM = "ARM"
    DISARM = "DISARM"
    ABORT = "ABORT"
    HOLD = "HOLD"


@dataclass
class Signal:
    """Strategy output — direction, confidence, stop, target."""
    side: Side
    confidence: float  # 0.0 to 1.0
    stop_distance_pts: float
    target_distance_pts: float
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    strategy_name: str = ""
    modify_sl: float = 0.0  # new SL price (0 = don't modify)
    modify_tp: float = 0.0  # new TP price (0 = don't modify)


@dataclass
class OpenPosition:
    """Info about an open position passed to on_open."""
    side: Side
    lots: float
    entry_price: float
    stop_price: float
    target_price: float
    opened_at: datetime
    current_price: float = 0.0
    unrealized_pnl: float = 0.0


@dataclass
class ArmedState:
    """Delayed entry state — Hyperliquid Arm/Disarm gem."""
    is_armed: bool = False
    armed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    signal: Optional[Signal] = None


class Strategy(ABC):
    """Base strategy with 3-phase lifecycle stolen from Hyperliquid.

    Every strategy must implement:
    - on_idle(): No position — scan for entry
    - on_open(): Position open — manage/exit
    - on_busy(): Order pending — handle timeout/abort

    Plus optional:
    - arm(): Delayed entry with auto-expiry
    """

    def __init__(self, name: str):
        self.name = name
        self._armed = ArmedState()

    # ------------------------------------------------------------------
    # 3-phase lifecycle (Hyperliquid stolen gem)
    # ------------------------------------------------------------------

    @abstractmethod
    def on_idle(
        self,
        data: dict[str, Any],
        equity: float,
    ) -> Optional[Signal]:
        """No position open — scan for entry opportunity.

        Args:
            data: Market data (bars, ticks, indicators)
            equity: Current account equity

        Returns:
            Signal if entry found, None otherwise
        """
        ...

    @abstractmethod
    def on_open(
        self,
        position: OpenPosition,
        data: dict[str, Any],
        equity: float,
    ) -> Optional[Signal]:
        """Position open — manage (breakeven, trail, exit).

        Returns:
            Signal with side=FLAT to exit, or None to keep holding
        """
        ...

    @abstractmethod
    def on_busy(
        self,
        busy_reason: str,
        data: dict[str, Any],
    ) -> Optional[Signal]:
        """Order pending — handle timeout, abort, retry.

        Args:
            busy_reason: Why we're busy ("pending_fill", "timeout", etc.)

        Returns:
            Signal to abort/retry, or None to wait
        """
        ...

    # ------------------------------------------------------------------
    # Arm/Disarm delayed entry (Hyperliquid stolen gem)
    # ------------------------------------------------------------------

    def arm(self, signal: Signal, duration_minutes: int = 15) -> None:
        """Arm delayed entry — "I see a setup but want confirmation."

        The arm expires automatically if no confirmation arrives.
        """
        now = datetime.now(timezone.utc)
        self._armed = ArmedState(
            is_armed=True,
            armed_at=now,
            expires_at=now + timedelta(minutes=duration_minutes),
            signal=signal,
        )
        logger.info("Strategy %s armed for %d min", self.name, duration_minutes)

    def disarm(self) -> None:
        """Cancel armed entry."""
        self._armed = ArmedState()

    def check_arm(self) -> Optional[Signal]:
        """Check if armed entry is still valid."""
        if not self._armed.is_armed:
            return None
        now = datetime.now(timezone.utc)
        if self._armed.expires_at and now > self._armed.expires_at:
            logger.info("Strategy %s arm expired", self.name)
            self._armed = ArmedState()
            return None
        return self._armed.signal

    @property
    def is_armed(self) -> bool:
        return self._armed.is_armed

    # ------------------------------------------------------------------
    # Regime classification (Capital41 stolen gem)
    # ------------------------------------------------------------------

    @staticmethod
    def classify_regime(
        adx: float = 50.0,
        atr_ratio: float = 1.0,
        hurst: float = 0.5,
    ) -> str:
        """Market regime from Capital41 Regime Matrix.

        Returns: 'trending', 'expanding', 'compressing', 'mean_reverting'
        """
        if adx > 25 and atr_ratio > 1.1:
            return "trending"
        elif adx > 25 and atr_ratio <= 1.1:
            return "expanding"
        elif adx <= 18 and atr_ratio < 0.85:
            return "compressing"
        else:
            return "mean_reverting"
