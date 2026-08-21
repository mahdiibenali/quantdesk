"""Walk-forward validation framework — chronological splits, OOS evaluation.

Prevents data leakage by enforcing strict temporal ordering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

from .data_pipeline import SymbolData, split_chronological
from .execution_model import BacktestEngine, BacktestTrade, CostScenario
from .cost_model import CostModel
from .analysis import compute_baseline_stats

logger = logging.getLogger(__name__)


@dataclass
class FoldResult:
    """Result of a single walk-forward fold."""
    fold: int
    # In-sample
    is_trades: int = 0
    is_win_rate: float = 0.0
    is_expectancy: float = 0.0
    is_profit_factor: float = 0.0
    # Out-of-sample
    oos_trades: int = 0
    oos_win_rate: float = 0.0
    oos_expectancy: float = 0.0
    oos_profit_factor: float = 0.0
    oos_total_pnl: float = 0.0
    oos_max_dd: float = 0.0
    # Overfit detection
    is_overfit: bool = False
    expectancy_decay: float = 0.0  # IS expectancy - OOS expectancy


@dataclass
class WalkForwardResult:
    """Complete walk-forward analysis result."""
    symbol: str
    n_folds: int
    folds: list[FoldResult]
    # Pooled OOS
    pooled_oos_trades: int = 0
    pooled_oos_win_rate: float = 0.0
    pooled_oos_expectancy: float = 0.0
    pooled_oos_profit_factor: float = 0.0
    pooled_oos_total_pnl: float = 0.0
    pooled_oos_max_dd: float = 0.0
    # Verdict
    folds_profitable: int = 0
    folds_overfit: int = 0
    robustness_score: float = 0.0  # 0-1, how consistent across folds


class WalkForwardEngine:
    """Walk-forward validation with overfit detection."""

    def __init__(
        self,
        n_folds: int = 5,
        train_pct: float = 0.6,
        val_pct: float = 0.2,
        overfit_threshold: float = 0.1,
    ):
        self.n_folds = n_folds
        self.train_pct = train_pct
        self.val_pct = val_pct
        self.overfit_threshold = overfit_threshold

    def run(
        self,
        data: SymbolData,
        strategy_fn: Callable,
        cost_model: CostModel,
        engine_kwargs: Optional[dict] = None,
    ) -> WalkForwardResult:
        """Run walk-forward validation.

        Splits data into n_folds chronological chunks.
        For each fold: train on earlier data, test on later data.
        """
        if engine_kwargs is None:
            engine_kwargs = {}

        folds = []
        all_oos_trades = []
        fold_size = len(data) // self.n_folds

        for i in range(self.n_folds):
            # Define fold boundaries
            test_start = i * fold_size
            test_end = min(test_start + fold_size, len(data))
            train_end = test_start

            if train_end < 50 or (test_end - test_start) < 20:
                continue

            # Split data
            train_data = SymbolData(
                symbol=data.symbol,
                bars=data.bars[:train_end],
                pip=data.pip,
                contract_size=data.contract_size,
                point=data.point,
                volume_step=data.volume_step,
                volume_min=data.volume_min,
                volume_max=data.volume_max,
                base_spread_points=data.base_spread_points,
            )

            test_data = SymbolData(
                symbol=data.symbol,
                bars=data.bars[test_start:test_end],
                pip=data.pip,
                contract_size=data.contract_size,
                point=data.point,
                volume_step=data.volume_step,
                volume_min=data.volume_min,
                volume_max=data.volume_max,
                base_spread_points=data.base_spread_points,
            )

            # Run on train (in-sample)
            engine_is = BacktestEngine(
                initial_equity=10000.0,
                max_positions=1,
                cost_scenario=CostScenario.REALISTIC,
                **engine_kwargs,
            )
            is_trades = engine_is.run(train_data, strategy_fn, cost_model)
            is_stats = compute_baseline_stats(is_trades) if is_trades else {}

            # Run on test (out-of-sample)
            engine_oos = BacktestEngine(
                initial_equity=10000.0,
                max_positions=1,
                cost_scenario=CostScenario.REALISTIC,
                **engine_kwargs,
            )
            oos_trades = engine_oos.run(test_data, strategy_fn, cost_model)
            oos_stats = compute_baseline_stats(oos_trades) if oos_trades else {}

            # Overfit detection
            is_exp = is_stats.get("expectancy_after_cost", 0)
            oos_exp = oos_stats.get("expectancy_after_cost", 0)
            decay = is_exp - oos_exp
            is_overfit = decay > self.overfit_threshold and is_exp > 0

            fold_result = FoldResult(
                fold=i,
                is_trades=is_stats.get("total_trades", 0),
                is_win_rate=is_stats.get("win_rate", 0),
                is_expectancy=is_exp,
                is_profit_factor=is_stats.get("profit_factor", 0),
                oos_trades=oos_stats.get("total_trades", 0),
                oos_win_rate=oos_stats.get("win_rate", 0),
                oos_expectancy=oos_exp,
                oos_profit_factor=oos_stats.get("profit_factor", 0),
                oos_total_pnl=oos_stats.get("total_pnl_after_cost", 0),
                oos_max_dd=oos_stats.get("max_drawdown", 0),
                is_overfit=is_overfit,
                expectancy_decay=decay,
            )

            folds.append(fold_result)
            all_oos_trades.extend(oos_trades)

        # Pool OOS results
        pooled_stats = compute_baseline_stats(all_oos_trades) if all_oos_trades else {}

        # Robustness score
        profitable_folds = sum(1 for f in folds if f.oos_expectancy > 0)
        overfit_folds = sum(1 for f in folds if f.is_overfit)
        robustness = profitable_folds / len(folds) if folds else 0

        return WalkForwardResult(
            symbol=data.symbol,
            n_folds=len(folds),
            folds=folds,
            pooled_oos_trades=pooled_stats.get("total_trades", 0),
            pooled_oos_win_rate=pooled_stats.get("win_rate", 0),
            pooled_oos_expectancy=pooled_stats.get("expectancy_after_cost", 0),
            pooled_oos_profit_factor=pooled_stats.get("profit_factor", 0),
            pooled_oos_total_pnl=pooled_stats.get("total_pnl_after_cost", 0),
            pooled_oos_max_dd=pooled_stats.get("max_drawdown", 0),
            folds_profitable=profitable_folds,
            folds_overfit=overfit_folds,
            robustness_score=robustness,
        )
