"""Composite Signal Scoring — multi-factor signal with confidence decay.

Stolen from XAU (actg338) build_signal.py — combines multiple independent
components into a single explainable score with confidence penalty for
missing data.
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SignalComponent:
    """One independent signal source."""
    name: str
    score: float  # -1.0 to +1.0
    weight: float = 1.0
    available: bool = True
    reason: str = ""


@dataclass
class CompositeSignal:
    """Combined signal with confidence — stolen from XAU build_signal.py."""
    score: float  # -1.0 to +1.0
    confidence: float  # 0.0 to 1.0
    direction: str  # "LONG", "SHORT", "NEUTRAL"
    components: list[SignalComponent] = field(default_factory=list)
    coverage: float = 0.0  # fraction of available components
    agreement: float = 0.0  # fraction pointing same direction
    reasons: list[str] = field(default_factory=list)

    def is_actionable(self, min_confidence: float = 0.55) -> bool:
        """Can we trade on this signal?"""
        return self.confidence >= min_confidence and abs(self.score) > 0.3


class SignalAggregator:
    """Multi-source signal combiner with confidence decay.

    Stolen from XAU (actg338) build_signal.py:
    - Each component scored independently
    - Combined with weighted average
    - Confidence penalized for missing data
    - Agreement bonus when components align
    """

    def __init__(self, components: Optional[list[str]] = None):
        self.component_names = components or [
            "ema_alignment",
            "rsi_turn",
            "williams_r_turn",
            "momentum",
            "volume",
            "regime",
            "vwap_side",
        ]
        self._components: dict[str, SignalComponent] = {}

    def add_component(
        self,
        name: str,
        score: float,
        weight: float = 1.0,
        available: bool = True,
        reason: str = "",
    ) -> None:
        """Add or update a signal component."""
        self._components[name] = SignalComponent(
            name=name,
            score=max(-1.0, min(1.0, score)),
            weight=weight,
            available=available,
            reason=reason,
        )

    def compute(self) -> CompositeSignal:
        """Compute composite signal with confidence.

        Stolen formula from XAU:
        confidence = min(0.92, 0.42 + coverage*0.28 + agreement*0.04 + abs(score)*0.2)
        """
        available = [c for c in self._components.values() if c.available]
        total = len(self.component_names)
        coverage = len(available) / total if total > 0 else 0.0

        if not available:
            return CompositeSignal(
                score=0.0, confidence=0.0, direction="NEUTRAL",
                coverage=0.0, agreement=0.0,
            )

        # Weighted average score
        total_weight = sum(c.weight for c in available)
        if total_weight <= 0:
            return CompositeSignal(
                score=0.0, confidence=0.0, direction="NEUTRAL",
                coverage=coverage, agreement=0.0,
            )

        weighted_score = sum(c.score * c.weight for c in available) / total_weight
        weighted_score = max(-1.0, min(1.0, weighted_score))

        # Agreement: fraction of components pointing same direction as final score
        if abs(weighted_score) > 0.01:
            same_direction = sum(
                1 for c in available
                if (c.score > 0) == (weighted_score > 0)
            )
            agreement = same_direction / len(available)
        else:
            agreement = 0.0

        # Confidence formula (stolen from XAU)
        confidence = min(
            0.92,
            0.42 + coverage * 0.28 + agreement * 0.04 + abs(weighted_score) * 0.2
        )

        # Direction
        if weighted_score > 0.1:
            direction = "LONG"
        elif weighted_score < -0.1:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"

        # Build reasons from components
        reasons = [f"{c.name}: {c.score:.2f}" for c in available if abs(c.score) > 0.1]

        return CompositeSignal(
            score=round(weighted_score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            components=list(self._components.values()),
            coverage=coverage,
            agreement=agreement,
            reasons=reasons,
        )

    def reset(self) -> None:
        """Reset all components for next signal cycle."""
        self._components.clear()
