"""Backtesting Engine — walk-forward validation, Monte Carlo, sensitivity.

Stolen gems:
- Walk-forward with overfit detection from QuantWave
- NaN-safe metric convention from QuantWave
- Multi-fold chronological validation
- Bootstrap Monte Carlo analysis
"""

from __future__ import annotations

import math
import random
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Single trade result for analysis."""
    entry_price: float
    exit_price: float
    side: str
    lots: float
    pnl: float
    r_multiple: float
    bars_held: int
    timestamp: int = 0


@dataclass
class ValidationResult:
    """Result of a single walk-forward fold."""
    fold: int
    train_trades: int
    oos_trades: int
    oos_win_rate: float
    oos_profit_factor: float
    oos_mean_r: float
    oos_sharpe: float
    oos_max_dd: float
    is_overfit: bool
    overfit_threshold: float = 0.1


@dataclass
class MonteCarloResult:
    """Bootstrap analysis results."""
    mean_return: float
    ci_lower: float
    ci_upper: float
    mean_max_dd: float
    dd_ci_lower: float
    dd_ci_upper: float
    win_rate: float
    profit_factor: float
    n_simulations: int
    percent_positive: float  # % of simulations with positive return


class BacktestEngine:
    """Walk-forward backtesting with overfit detection.

    Stolen from QuantWave walk_forward.rs:
    - Rolling OOS folds
    - Overfit threshold: train_metric - oos_metric > threshold
    - NaN-safe metrics (NaN not inf for undefined ratios)
    """

    def __init__(
        self,
        strategy_fn: Callable,
        min_trades: int = 100,
        train_pct: float = 0.8,
        n_folds: int = 5,
        overfit_threshold: float = 0.1,
    ):
        self.strategy_fn = strategy_fn
        self.min_trades = min_trades
        self.train_pct = train_pct
        self.n_folds = n_folds
        self.overfit_threshold = overfit_threshold

    def walk_forward(
        self,
        data: list[dict],
        params: dict,
    ) -> list[ValidationResult]:
        """Run walk-forward validation with overfit detection.

        Stolen from QuantWave — rolling chronological folds.
        """
        fold_size = len(data) // self.n_folds
        results = []

        for i in range(self.n_folds):
            test_start = i * fold_size
            test_end = test_start + fold_size
            train_end = test_start

            if train_end < self.min_trades or (test_end - test_start) < 20:
                continue

            train_data = data[:train_end]
            test_data = data[test_start:test_end]

            # Run strategy on train and test
            train_trades = self.strategy_fn(train_data, params)
            oos_trades = self.strategy_fn(test_data, params)

            # Compute metrics (NaN-safe)
            oos_metrics = self._compute_metrics(oos_trades)
            train_metrics = self._compute_metrics(train_trades)

            # Overfit detection
            is_overfit = False
            if not math.isnan(train_metrics["mean_r"]) and not math.isnan(oos_metrics["mean_r"]):
                is_overfit = (train_metrics["mean_r"] - oos_metrics["mean_r"]) > self.overfit_threshold

            results.append(ValidationResult(
                fold=i,
                train_trades=len(train_trades),
                oos_trades=len(oos_trades),
                oos_win_rate=oos_metrics["win_rate"],
                oos_profit_factor=oos_metrics["profit_factor"],
                oos_mean_r=oos_metrics["mean_r"],
                oos_sharpe=oos_metrics["sharpe"],
                oos_max_dd=oos_metrics["max_dd"],
                is_overfit=is_overfit,
                overfit_threshold=self.overfit_threshold,
            ))

        return results

    def _compute_metrics(self, trades: list[TradeRecord]) -> dict:
        """Compute performance metrics. NaN for undefined (stolen QuantWave convention)."""
        if not trades:
            return {
                "win_rate": float("nan"),
                "profit_factor": float("nan"),
                "mean_r": float("nan"),
                "sharpe": float("nan"),
                "max_dd": float("nan"),
            }

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]

        win_rate = len(wins) / len(trades) if trades else float("nan")

        gross_profit = sum(t.pnl for t in wins) if wins else 0.0
        gross_loss = abs(sum(t.pnl for t in losses)) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else float("nan")
        )

        r_values = [t.r_multiple for t in trades]
        mean_r = sum(r_values) / len(r_values) if r_values else float("nan")

        # Sharpe (annualized, assuming 252 trading days)
        if len(r_values) > 1:
            mean_r_val = sum(r_values) / len(r_values)
            variance = sum((r - mean_r_val) ** 2 for r in r_values) / (len(r_values) - 1)
            std_r = math.sqrt(variance) if variance > 0 else 0.001
            sharpe = (mean_r_val / std_r) * math.sqrt(252) if std_r > 0 else 0.0
        else:
            sharpe = float("nan")

        # Max drawdown
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            equity += t.pnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

        return {
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "mean_r": mean_r,
            "sharpe": sharpe,
            "max_dd": max_dd,
        }


class MonteCarloAnalyzer:
    """Bootstrap analysis for confidence intervals.

    Stolen from QuantWave — 10,000 resamples with CI computation.
    """

    def __init__(self, n_simulations: int = 10000, confidence: float = 0.95):
        self.n_simulations = n_simulations
        self.confidence = confidence

    def analyze(self, trades: list[TradeRecord]) -> MonteCarloResult:
        """Bootstrap resample trades for confidence intervals."""
        if not trades:
            return MonteCarloResult(
                mean_return=0, ci_lower=0, ci_upper=0,
                mean_max_dd=0, dd_ci_lower=0, dd_ci_upper=0,
                win_rate=0, profit_factor=0,
                n_simulations=0, percent_positive=0,
            )

        returns = []
        max_dds = []

        for _ in range(self.n_simulations):
            # Resample with replacement
            sample = random.choices(trades, k=len(trades))
            total_return = sum(t.pnl for t in sample)
            returns.append(total_return)

            # Max DD for this sample
            equity = 0.0
            peak = 0.0
            sample_dd = 0.0
            for t in sample:
                equity += t.pnl
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak if peak > 0 else 0.0
                sample_dd = max(sample_dd, dd)
            max_dds.append(sample_dd)

        returns.sort()
        max_dds.sort()

        alpha = (1 - self.confidence) / 2
        n = len(returns)
        ci_lower = returns[int(alpha * n)]
        ci_upper = returns[int((1 - alpha) * n)]
        dd_ci_lower = max_dds[int(alpha * n)]
        dd_ci_upper = max_dds[int((1 - alpha) * n)]

        wins = sum(1 for t in trades if t.pnl > 0)
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl <= 0))

        return MonteCarloResult(
            mean_return=sum(returns) / n,
            ci_lower=ci_lower,
            ci_upper=ci_upper,
            mean_max_dd=sum(max_dds) / n,
            dd_ci_lower=dd_ci_lower,
            dd_ci_upper=dd_ci_upper,
            win_rate=wins / len(trades),
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else 0,
            n_simulations=self.n_simulations,
            percent_positive=sum(1 for r in returns if r > 0) / n * 100,
        )


class SensitivityAnalyzer:
    """Parameter sensitivity testing — vary ±20% and check robustness.

    Stolen concept from PLAN.md Phase 3.3.
    """

    def __init__(self, strategy_fn: Callable, base_params: dict):
        self.strategy_fn = strategy_fn
        self.base_params = base_params

    def analyze(
        self,
        data: list[dict],
        variation_pct: float = 0.20,
    ) -> dict[str, dict]:
        """Test each parameter at ±variation_pct from base."""
        results = {}

        base_trades = self.strategy_fn(data, self.base_params)
        base_metrics = self._metrics(base_trades)
        results["base"] = base_metrics

        for param_name, base_value in self.base_params.items():
            if not isinstance(base_value, (int, float)):
                continue

            for direction, label in [(-1, "low"), (1, "high")]:
                test_params = self.base_params.copy()
                test_params[param_name] = base_value * (1 + direction * variation_pct)
                test_trades = self.strategy_fn(data, test_params)
                test_metrics = self._metrics(test_trades)
                results[f"{param_name}_{label}"] = test_metrics

        return results

    def _metrics(self, trades: list[TradeRecord]) -> dict:
        if not trades:
            return {"trades": 0, "win_rate": 0, "mean_r": 0, "profit_factor": 0}
        wins = sum(1 for t in trades if t.pnl > 0)
        gross_p = sum(t.pnl for t in trades if t.pnl > 0)
        gross_l = abs(sum(t.pnl for t in trades if t.pnl <= 0))
        return {
            "trades": len(trades),
            "win_rate": wins / len(trades),
            "mean_r": sum(t.r_multiple for t in trades) / len(trades),
            "profit_factor": gross_p / gross_l if gross_l > 0 else 0,
        }
