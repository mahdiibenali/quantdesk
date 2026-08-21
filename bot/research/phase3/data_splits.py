"""Data splits for Phase 3 — locked OOS holdout and walk-forward folds.

Anti-fishing: OOS is locked before any strategy code runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..data_pipeline import SymbolData

logger = logging.getLogger(__name__)


@dataclass
class DataSplits:
    """Chronological data splits for one symbol."""
    symbol: str
    train: SymbolData       # 60% — in-sample research
    val: SymbolData         # 20% — validation / walk-forward
    test: SymbolData        # 20% — FINAL OOS (locked, touched once)
    # Walk-forward folds within train+val
    wf_folds: list[tuple[SymbolData, SymbolData]]  # (train_fold, oos_fold) pairs

    @property
    def total_bars(self) -> int:
        return len(self.train) + len(self.val) + len(self.test)

    @property
    def test_days(self) -> float:
        if len(self.test.bars) < 2:
            return 0
        return (self.test.bars[-1].timestamp - self.test.bars[0].timestamp).days


def split_data(
    data: SymbolData,
    train_pct: float = 0.60,
    val_pct: float = 0.20,
    test_pct: float = 0.20,
    wf_folds: int = 4,
) -> DataSplits:
    """Split data chronologically into train/val/test.

    OOS test set is the LAST portion — never touched during development.
    Walk-forward folds are created within train+val.
    """
    n = len(data.bars)
    train_end = int(n * train_pct)
    val_end = int(n * (train_pct + val_pct))

    def _slice(start: int, end: int, label: str) -> SymbolData:
        return SymbolData(
            symbol=data.symbol,
            bars=data.bars[start:end],
            pip=data.pip,
            contract_size=data.contract_size,
            point=data.point,
            volume_step=data.volume_step,
            volume_min=data.volume_min,
            volume_max=data.volume_max,
            base_spread_points=data.base_spread_points,
            usd_per_quote_unit=getattr(data, 'usd_per_quote_unit', 1.0),
        )

    train = _slice(0, train_end, "train")
    val = _slice(train_end, val_end, "val")
    test = _slice(val_end, n, "test")

    # Walk-forward folds: split train+val into wf_folds
    combined = data.bars[:val_end]
    fold_size = len(combined) // wf_folds
    folds = []
    for i in range(wf_folds):
        fold_start = i * fold_size
        fold_end = min((i + 1) * fold_size, len(combined))
        oos_start = fold_end
        oos_end = min(oos_start + fold_size, len(combined))

        if oos_end <= oos_start:
            break

        fold_train = _slice(fold_start, fold_end, f"wf_train_{i}")
        fold_oos = _slice(oos_start, oos_end, f"wf_oos_{i}")
        folds.append((fold_train, fold_oos))

    logger.info(
        "Split %s: train=%d val=%d test=%d (OOS locked), wf_folds=%d",
        data.symbol, len(train), len(val), len(test), len(folds),
    )

    return DataSplits(
        symbol=data.symbol,
        train=train,
        val=val,
        test=test,
        wf_folds=folds,
    )
