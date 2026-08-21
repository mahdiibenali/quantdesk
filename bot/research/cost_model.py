"""Transaction cost model — spread, commission, slippage, total cost.

Provides BASE / REALISTIC / STRESSED cost scenarios.
Computes cost_in_R for every trade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CostScenario(Enum):
    BASE = "BASE"           # minimum realistic costs
    REALISTIC = "REALISTIC" # typical live conditions
    STRESSED = "STRESSED"   # degraded execution


@dataclass
class TradeCost:
    """Complete cost breakdown for a single trade."""
    spread_cost: float       # in price units
    commission: float        # in price units
    slippage: float          # estimated in price units
    total_entry_cost: float  # spread + slippage at entry
    total_exit_cost: float   # spread + slippage at exit
    total_round_trip: float  # full round-trip cost in price units
    cost_in_r: float         # cost expressed as R-multiple (negative)
    # Metadata
    spread_points: float
    pip_value: float
    contract_size: float = 100000.0
    symbol: str = ""


@dataclass
class CostModel:
    """Per-symbol cost configuration."""
    symbol: str
    pip: float
    point: float
    contract_size: float
    # Base costs
    base_spread_points: float  # from MT5 in points
    commission_per_lot: float = 0.0  # per round-trip in account currency
    # Stress multipliers
    spread_stress: float = 1.0
    slippage_pct: float = 0.0  # slippage as % of ATR
    # Derived
    slippage_points: float = 0.0  # fixed slippage estimate

    @classmethod
    def from_symbol_data(cls, symbol_data, commission_per_lot: float = 0.0) -> "CostModel":
        """Create cost model from SymbolData."""
        return cls(
            symbol=symbol_data.symbol,
            pip=symbol_data.pip,
            point=symbol_data.point,
            contract_size=symbol_data.contract_size,
            base_spread_points=symbol_data.base_spread_points,
            commission_per_lot=commission_per_lot,
        )

    def compute_cost(
        self,
        side: str,
        entry_price: float,
        stop_distance_pts: float,
        lots: float,
        scenario: CostScenario = CostScenario.REALISTIC,
        atr: float = 0.0,
    ) -> TradeCost:
        """Compute full round-trip cost for a trade.

        Args:
            side: "BUY" or "SELL"
            entry_price: entry price
            stop_distance_pts: stop distance in price units
            lots: position size
            scenario: cost scenario
            atr: current ATR for slippage estimation

        Returns:
            TradeCost with full breakdown
        """
        # Spread cost (entry + exit)
        spread = self._get_spread(scenario)
        spread_cost_entry = spread / 2.0  # half at entry
        spread_cost_exit = spread / 2.0   # half at exit

        # Commission (per lot, round-trip)
        commission = self.commission_per_lot * lots

        # Slippage estimate
        slippage = self._estimate_slippage(scenario, atr)

        # Total costs in price units
        total_entry = spread_cost_entry + slippage
        total_exit = spread_cost_exit + slippage
        total_rt = total_entry + total_exit + commission

        # Cost in R-multiple
        cost_in_r = -(total_rt / stop_distance_pts) if stop_distance_pts > 0 else -999.0

        return TradeCost(
            spread_cost=spread_cost_entry + spread_cost_exit,
            commission=commission,
            slippage=slippage * 2,
            total_entry_cost=total_entry,
            total_exit_cost=total_exit,
            total_round_trip=total_rt,
            cost_in_r=cost_in_r,
            spread_points=spread,
            pip_value=self.pip,
            contract_size=self.contract_size,
            symbol=self.symbol,
        )

    def _get_spread(self, scenario: CostScenario) -> float:
        """Get spread in price units for scenario."""
        base = self.base_spread_points
        if scenario == CostScenario.BASE:
            return base * 0.8  # tight conditions
        elif scenario == CostScenario.REALISTIC:
            return base * self.spread_stress
        else:  # STRESSED
            return base * self.spread_stress * 2.0

    def _estimate_slippage(self, scenario: CostScenario, atr: float) -> float:
        """Estimate slippage in price units."""
        if atr <= 0:
            atr = self.base_spread_points * 5  # fallback

        if scenario == CostScenario.BASE:
            return atr * 0.01  # ~1% of ATR
        elif scenario == CostScenario.REALISTIC:
            return atr * 0.03  # ~3% of ATR
        else:  # STRESSED
            return atr * 0.08  # ~8% of ATR


def get_cost_scenarios(
    symbol: str,
    pip: float,
    point: float,
    contract_size: float,
    base_spread_points: float,
    commission_per_lot: float = 0.0,
) -> dict[CostScenario, CostModel]:
    """Create all three cost scenarios for a symbol."""
    scenarios = {}
    for stress, scenario in [
        (0.8, CostScenario.BASE),
        (1.0, CostScenario.REALISTIC),
        (2.0, CostScenario.STRESSED),
    ]:
        model = CostModel(
            symbol=symbol,
            pip=pip,
            point=point,
            contract_size=contract_size,
            base_spread_points=base_spread_points,
            commission_per_lot=commission_per_lot,
            spread_stress=stress,
        )
        scenarios[scenario] = model
    return scenarios
