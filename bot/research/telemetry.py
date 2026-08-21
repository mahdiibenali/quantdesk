"""Telemetry — rejection logging, candidate journal, signal tracking.

Records EVERY candidate, not just filled trades.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class CandidateRecord:
    """Record of every signal candidate (filled or rejected)."""
    timestamp: str
    symbol: str
    score: float
    confidence: float
    direction: str  # BUY, SELL, NONE
    spread: float
    atr: float
    ema_state: str  # "above", "below", "cross_up", "cross_down"
    rsi: float
    momentum: float
    pattern: str
    trend_state: str  # "above_ema50", "below_ema50", "unknown"
    rejection_reason: str  # "" if filled
    # Hypothetical trade
    hypothetical_entry: float = 0.0
    hypothetical_exit: float = 0.0
    hypothetical_r: float = 0.0
    # Actual trade (if filled)
    actual_entry: float = 0.0
    actual_exit: float = 0.0
    actual_pnl: float = 0.0
    actual_r: float = 0.0
    filled: bool = False


@dataclass
class RejectionStats:
    """Aggregate rejection statistics."""
    total_candidates: int = 0
    total_filled: int = 0
    total_rejected: int = 0
    rejections_by_reason: dict[str, int] = field(default_factory=dict)
    # Per-reason win rate of rejected candidates (if we could have traded them)
    rejected_hypothetical_win_rate: float = 0.0
    filled_win_rate: float = 0.0


class Telemetry:
    """Logs every candidate and tracks rejection reasons."""

    def __init__(self, output_dir: str = "state"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self._candidates: list[CandidateRecord] = []
        self._rejection_counts: dict[str, int] = {}

    def log_candidate(
        self,
        symbol: str,
        score: float,
        confidence: float,
        direction: str,
        spread: float,
        atr: float,
        ema_state: str,
        rsi: float,
        momentum: float,
        pattern: str,
        trend_state: str,
        rejection_reason: str = "",
        entry_price: float = 0.0,
        filled: bool = False,
    ) -> None:
        """Log a candidate signal (whether filled or rejected)."""
        record = CandidateRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol=symbol,
            score=score,
            confidence=confidence,
            direction=direction,
            spread=spread,
            atr=atr,
            ema_state=ema_state,
            rsi=rsi,
            momentum=momentum,
            pattern=pattern,
            trend_state=trend_state,
            rejection_reason=rejection_reason,
            hypothetical_entry=entry_price,
            filled=filled,
        )
        self._candidates.append(record)

        if rejection_reason:
            self._rejection_counts[rejection_reason] = (
                self._rejection_counts.get(rejection_reason, 0) + 1
            )

    def log_fill(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        r_multiple: float,
    ) -> None:
        """Update the last candidate for this symbol with fill info."""
        for rec in reversed(self._candidates):
            if rec.symbol == symbol and not rec.filled:
                rec.filled = True
                rec.actual_entry = entry_price
                rec.actual_exit = exit_price
                rec.actual_pnl = pnl
                rec.actual_r = r_multiple
                rec.rejection_reason = ""
                break

    def get_stats(self) -> RejectionStats:
        """Compute aggregate statistics."""
        filled = [c for c in self._candidates if c.filled]
        rejected = [c for c in self._candidates if not c.filled]

        filled_wr = 0.0
        if filled:
            filled_wr = sum(1 for c in filled if c.actual_pnl > 0) / len(filled)

        return RejectionStats(
            total_candidates=len(self._candidates),
            total_filled=len(filled),
            total_rejected=len(rejected),
            rejections_by_reason=dict(self._rejection_counts),
            filled_win_rate=filled_wr,
        )

    def save(self, filename: str = "telemetry.jsonl") -> None:
        """Save all candidates to JSONL file."""
        path = self.output_dir / filename
        with open(path, "w") as f:
            for rec in self._candidates:
                f.write(json.dumps(asdict(rec)) + "\n")
        logger.info("Saved %d candidates to %s", len(self._candidates), path)

    def load(self, filename: str = "telemetry.jsonl") -> None:
        """Load candidates from JSONL file."""
        path = self.output_dir / filename
        if not path.exists():
            return
        self._candidates.clear()
        self._rejection_counts.clear()
        with open(path) as f:
            for line in f:
                data = json.loads(line)
                rec = CandidateRecord(**data)
                self._candidates.append(rec)
                if rec.rejection_reason:
                    self._rejection_counts[rec.rejection_reason] = (
                        self._rejection_counts.get(rec.rejection_reason, 0) + 1
                    )
