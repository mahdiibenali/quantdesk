"""Unified Risk Engine — account-relative, vol-targeting, works with any balance.

Stolen gems:
- Vol targeting from QuantWave (Moreira & Muir 2017)
- Inverse-vol sizing from QuantWave (Lopez de Prado risk parity)
- Pre-trade compliance with veto option
- Progressive cooldown escalation from TradingAgents
- Emergency flatten controls from SmallAccountAdvisor
"""

from __future__ import annotations

import math
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TradeIntent:
    """A pre-trade request that must pass all risk gates before execution."""
    symbol: str
    side: str  # "BUY" or "SELL"
    lots: float
    entry_price: float
    stop_price: float
    target_price: float
    contract_size: float = 100000.0
    signal_score: float = 0.0
    strategy: str = ""
    reason: str = ""


@dataclass
class RiskVerdict:
    """Result of risk check on a TradeIntent."""
    approved: bool
    lots: float  # approved lots (may differ from requested)
    risk_dollars: float
    risk_pct: float
    notional: float
    margin_required: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Risk Engine
# ---------------------------------------------------------------------------

class RiskEngine:
    """Account-relative risk management that works with any balance.

    Features (stolen from repos):
    - Volatility targeting (QuantWave): scale size inversely with trailing vol
    - Inverse-vol sizing (QuantWave): position = target_vol / realized_vol * equity / price
    - Pre-trade compliance (QuantWave): hard notional/leverage caps with veto
    - Progressive cooldown (TradingAgents): escalating wait after losses
    - Emergency flatten (SmallAccountAdvisor): kill switch, drawdown breaker
    - Percentage-based guards: all limits relative to equity, never fixed dollar
    """

    def __init__(
        self,
        max_risk_per_trade_pct: float = 0.01,
        max_daily_loss_pct: float = 0.03,
        max_drawdown_pct: float = 0.10,
        max_notional_multiplier: float = 10.0,
        max_leverage: float = 50.0,
        max_concurrent_positions: int = 3,
        cooldown_base_minutes: int = 30,
        cooldown_escalation: float = 0.5,
        vol_target_annual: float = 0.15,
        vol_lookback: int = 20,
        min_lot: float = 0.01,
        max_lot: float = 100.0,
    ):
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.max_notional_multiplier = max_notional_multiplier
        self.max_leverage = max_leverage
        self.max_concurrent_positions = max_concurrent_positions
        self.cooldown_base_minutes = cooldown_base_minutes
        self.cooldown_escalation = cooldown_escalation
        self.vol_target_annual = vol_target_annual
        self.vol_lookback = vol_lookback
        self.min_lot = min_lot
        self.max_lot = max_lot

        # State
        self._equity_peak: float = 0.0
        self._daily_pnl: float = 0.0
        self._daily_start_equity: float = 0.0
        self._day_key: str = ""
        self._cooldown_until: Optional[datetime] = None
        self._streak: int = 0
        self._open_positions: int = 0
        self._trade_log: list[dict] = []

    # ------------------------------------------------------------------
    # Volatility estimation (QuantWave stolen gem)
    # ------------------------------------------------------------------

    @staticmethod
    def trailing_annualized_vol(closes: list[float], lookback: int = 20) -> float:
        """Annualized volatility from log-returns. Foundation for all vol-based sizing.

        Stolen from QuantWave risk.rs — Moreira & Muir 2017.
        """
        if len(closes) < lookback + 1:
            return 0.15  # default 15% if insufficient data

        log_returns = [
            math.log(closes[i] / closes[i - 1])
            for i in range(max(1, len(closes) - lookback), len(closes))
            if closes[i - 1] > 0
        ]
        if len(log_returns) < 2:
            return 0.15

        mean = sum(log_returns) / len(log_returns)
        variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
        daily_vol = math.sqrt(variance) if variance > 0 else 0.001
        annual_vol = daily_vol * math.sqrt(252)
        return max(0.01, min(1.0, annual_vol))

    # ------------------------------------------------------------------
    # Position sizing (QuantWave stolen gem)
    # ------------------------------------------------------------------

    def inverse_vol_size(
        self,
        equity: float,
        price: float,
        realized_vol: float,
        signal_strength: float = 1.0,
    ) -> float:
        """Inverse-volatility position sizing.

        size = sign(signal) * (target_vol / realized_vol) * equity / price

        Stolen from QuantWave risk.rs — Lopez de Prado risk parity.
        Clamped to [min_lot, max_lot].
        """
        if realized_vol <= 0 or price <= 0 or equity <= 0:
            return self.min_lot

        vol_ratio = self.vol_target_annual / realized_vol
        vol_ratio = max(0.1, min(5.0, vol_ratio))  # clamp

        raw_lots = (vol_ratio * equity * abs(signal_strength)) / (price * 100000)
        return max(self.min_lot, min(self.max_lot, round(raw_lots / self.min_lot) * self.min_lot))

    def risk_based_size(
        self,
        equity: float,
        stop_distance_pts: float,
        contract_size: float,
        price: float,
    ) -> float:
        """Risk-based sizing: risk_budget / (stop_distance × contract_size).

        Returns lot size that risks exactly max_risk_per_trade_pct of equity.
        """
        if stop_distance_pts <= 0 or contract_size <= 0 or price <= 0:
            return self.min_lot

        risk_budget = equity * self.max_risk_per_trade_pct
        raw_lots = risk_budget / (stop_distance_pts * contract_size)
        return max(self.min_lot, min(self.max_lot, round(raw_lots / self.min_lot) * self.min_lot))

    # ------------------------------------------------------------------
    # Pre-trade compliance (QuantWave stolen gem)
    # ------------------------------------------------------------------

    def check_pre_trade(
        self,
        intent: TradeIntent,
        equity: float,
        margin_per_lot: float,
        open_positions: int,
        recent_closes: Optional[list[float]] = None,
    ) -> RiskVerdict:
        """Full pre-trade risk check. Returns approved lots or rejection reasons.

        Stolen from QuantWave pre_trade compliance with veto option.
        """
        now = datetime.now(timezone.utc)
        reasons = []
        warnings = []
        approved = True

        # Update daily tracking
        today = now.date().isoformat()
        if self._day_key != today:
            self._day_key = today
            self._daily_pnl = 0.0
            self._daily_start_equity = equity

        # Update equity peak
        if equity > self._equity_peak:
            self._equity_peak = equity

        # --- Guard 1: Cooldown ---
        if self._cooldown_until and now < self._cooldown_until:
            remaining = (self._cooldown_until - now).total_seconds() / 60
            reasons.append(f"cooldown_active ({remaining:.1f} min remaining)")
            approved = False

        # --- Guard 2: Daily loss limit ---
        if self._daily_start_equity > 0:
            daily_loss = self._daily_pnl / self._daily_start_equity
            if daily_loss < -self.max_daily_loss_pct:
                reasons.append(
                    f"daily_loss_limit ({daily_loss:.1%} > -{self.max_daily_loss_pct:.1%})"
                )
                approved = False

        # --- Guard 3: Max drawdown ---
        if self._equity_peak > 0:
            drawdown = (equity - self._equity_peak) / self._equity_peak
            if drawdown < -self.max_drawdown_pct:
                reasons.append(
                    f"max_drawdown ({drawdown:.1%} > -{self.max_drawdown_pct:.1%})"
                )
                approved = False

        # --- Guard 4: Concurrent positions ---
        if open_positions >= self.max_concurrent_positions:
            reasons.append(
                f"max_positions ({open_positions}/{self.max_concurrent_positions})"
            )
            approved = False

        # --- Guard 5: Lot size bounds ---
        lots = max(self.min_lot, min(self.max_lot, intent.lots))

        # --- Guard 6: Notional cap ---
        contract = getattr(intent, "contract_size", 100000) or 100000
        notional = lots * intent.entry_price * contract
        max_notional = equity * self.max_notional_multiplier
        if notional > max_notional:
            warnings.append(f"notional {notional:.0f} > {max_notional:.0f} — capping")
            capped_lots = max_notional / (intent.entry_price * contract)
            lots = max(self.min_lot, round(capped_lots / self.min_lot) * self.min_lot)

        # --- Guard 7: Margin check ---
        margin_required = lots * margin_per_lot
        if margin_required > equity * 0.8:  # never use >80% margin
            reasons.append(
                f"margin_insufficient ({margin_required:.2f} > {equity * 0.8:.2f})"
            )
            approved = False

        # --- Guard 8: Risk per trade ---
        stop_dist = abs(intent.entry_price - intent.stop_price)
        risk_dollars = stop_dist * lots * contract
        risk_pct = risk_dollars / equity if equity > 0 else 999.0
        if risk_pct > self.max_risk_per_trade_pct * 1.5:  # 50% tolerance
            warnings.append(f"risk {risk_pct:.2%} exceeds {self.max_risk_per_trade_pct:.2%}")

        # --- Guard 9: Vol-adjusted sizing (if data available) ---
        if recent_closes and len(recent_closes) > self.vol_lookback:
            vol = self.trailing_annualized_vol(recent_closes, self.vol_lookback)
            vol_lots = self.inverse_vol_size(equity, intent.entry_price, vol)
            if vol_lots < lots:
                warnings.append(f"vol_suggests {vol_lots:.2f} lots (current {lots:.2f})")
                lots = max(self.min_lot, vol_lots)

        return RiskVerdict(
            approved=approved,
            lots=lots,
            risk_dollars=risk_dollars,
            risk_pct=risk_pct,
            notional=notional,
            margin_required=margin_required,
            reasons=reasons,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Post-trade tracking
    # ------------------------------------------------------------------

    def record_trade(self, pnl: float, equity: float) -> None:
        """Record trade result for cooldown/drawdown tracking."""
        self._daily_pnl += pnl

        if pnl < 0:
            self._streak += 1
            multiplier = 1.0 + (self._streak * self.cooldown_escalation)
            minutes = int(self.cooldown_base_minutes * multiplier)
            self._cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
            logger.info(
                "cooldown %d min (streak=%d, mult=%.1fx) until %s",
                minutes, self._streak, multiplier, self._cooldown_until.isoformat(),
            )
        else:
            self._streak = 0

        self._trade_log.append({
            "pnl": pnl,
            "equity": equity,
            "streak": self._streak,
            "daily_pnl": self._daily_pnl,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def record_position_open(self) -> None:
        self._open_positions += 1

    def record_position_close(self) -> None:
        self._open_positions = max(0, self._open_positions - 1)

    # ------------------------------------------------------------------
    # Emergency controls
    # ------------------------------------------------------------------

    def emergency_flatten(self) -> dict:
        """Kill switch — halt all trading, flatten all positions."""
        logger.warning("EMERGENCY FLATTEN triggered")
        self._cooldown_until = datetime.now(timezone.utc) + timedelta(hours=24)
        return {
            "action": "flatten_all",
            "reason": "emergency_stop",
            "cooldown_until": self._cooldown_until.isoformat(),
        }

    def should_flatten(self, equity: float) -> tuple[bool, str]:
        """Check if emergency flatten conditions are met."""
        if self._equity_peak > 0:
            dd = (equity - self._equity_peak) / self._equity_peak
            if dd < -self.max_drawdown_pct:
                return True, f"drawdown {dd:.1%} exceeds -{self.max_drawdown_pct:.1%}"

        if self._daily_start_equity > 0:
            daily_loss = self._daily_pnl / self._daily_start_equity
            if daily_loss < -self.max_daily_loss_pct * 2:
                return True, f"daily loss {daily_loss:.1%} exceeds -{self.max_daily_loss_pct * 2:.1%}"

        return False, ""

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def state_dict(self) -> dict:
        return {
            "equity_peak": self._equity_peak,
            "daily_pnl": self._daily_pnl,
            "daily_start_equity": self._daily_start_equity,
            "day_key": self._day_key,
            "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
            "streak": self._streak,
            "open_positions": self._open_positions,
        }

    def load_state(self, data: dict) -> None:
        self._equity_peak = data.get("equity_peak", 0.0)
        self._daily_pnl = data.get("daily_pnl", 0.0)
        self._daily_start_equity = data.get("daily_start_equity", 0.0)
        self._day_key = data.get("day_key", "")
        cd = data.get("cooldown_until")
        self._cooldown_until = datetime.fromisoformat(cd) if cd else None
        self._streak = data.get("streak", 0)
        self._open_positions = data.get("open_positions", 0)
