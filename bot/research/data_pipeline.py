"""Historical data pipeline — fetches real MT5 M5 bars.

Data model:
- MT5 provides OHLCV (open, high, low, close, tick_volume) per bar
- MT5 does NOT provide historical bid/ask/spread per bar
- Spread is MODELED, not observed, for historical periods
- Current live spread is used as baseline; historical stress multipliers applied

Preserves BID/ASK/MID distinction where possible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Bar:
    """Single OHLCV bar with spread model."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: float = 0.0
    spread_points: float = 0.0  # MODELED, not observed

    @property
    def mid(self) -> float:
        return (self.open + self.high + self.low + self.close) / 4.0

    @property
    def bid(self) -> float:
        """Approximate bid = close - spread/2."""
        return self.close - self.spread_points / 2.0

    @property
    def ask(self) -> float:
        """Approximate ask = close + spread/2."""
        return self.close + self.spread_points / 2.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def bar_range(self) -> float:
        return self.high - self.low


@dataclass
class SymbolData:
    """Complete historical data for one symbol."""
    symbol: str
    bars: list[Bar]
    pip: float
    contract_size: float
    point: float
    volume_step: float
    volume_min: float
    volume_max: float
    # Spread model
    base_spread_points: float  # current live spread in points
    spread_stress_factors: dict[str, float] = field(default_factory=dict)
    # USD conversion: multiplier to convert PnL from quote currency to USD
    # 1.0 for EURUSD/GBPUSD (quote=USD), 1/price for USDJPY (quote=JPY)
    usd_per_quote_unit: float = 1.0

    @property
    def closes(self) -> np.ndarray:
        return np.array([b.close for b in self.bars], dtype=float)

    @property
    def opens(self) -> np.ndarray:
        return np.array([b.open for b in self.bars], dtype=float)

    @property
    def highs(self) -> np.ndarray:
        return np.array([b.high for b in self.bars], dtype=float)

    @property
    def lows(self) -> np.ndarray:
        return np.array([b.low for b in self.bars], dtype=float)

    @property
    def volumes(self) -> np.ndarray:
        return np.array([b.tick_volume for b in self.bars], dtype=float)

    @property
    def timestamps(self) -> list[datetime]:
        return [b.timestamp for b in self.bars]

    @property
    def spreads(self) -> np.ndarray:
        return np.array([b.spread_points for b in self.bars], dtype=float)

    def __len__(self) -> int:
        return len(self.bars)


def fetch_mt5_bars(
    symbol: str,
    timeframe: int,
    n_bars: int = 5000,
    mt5_path: Optional[str] = None,
) -> list[Bar]:
    """Fetch historical M5 bars from MT5.

    Returns list of Bar objects with OHLCV data.
    Spread is set to 0 (unknown for historical bars).
    Caller must apply spread model separately.
    """
    import MetaTrader5 as mt5

    # Try to connect if not already
    try:
        account = mt5.account_info()
        if account is None:
            if mt5_path:
                mt5.initialize(path=mt5_path)
            else:
                initialized = mt5.initialize()
                if not initialized:
                    logger.error("MT5 initialize() failed")
                    return []
            # Re-check after init
            account = mt5.account_info()
            if account is None:
                logger.error("MT5 not connected after initialize()")
                return []
    except Exception:
        logger.error("MT5 connection failed")
        return []

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n_bars)
    if rates is None or len(rates) == 0:
        logger.warning("No data returned for %s", symbol)
        return []

    bars = []
    for r in rates:
        bars.append(Bar(
            timestamp=datetime.fromtimestamp(r["time"], tz=timezone.utc),
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            tick_volume=r["tick_volume"],
            spread_points=0.0,  # unknown historically
        ))

    logger.info("Fetched %d bars for %s", len(bars), symbol)
    return bars


def fetch_symbol_metadata(symbol: str) -> dict:
    """Get symbol metadata from MT5 (point, contract_size, volume limits)."""
    import MetaTrader5 as mt5

    info = mt5.symbol_info(symbol)
    if info is None:
        return {}

    return {
        "point": info.point,
        "contract_size": info.trade_contract_size,
        "volume_step": info.volume_step,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "spread": info.spread * info.point,  # current spread in price units
        "digits": info.digits,
    }


def load_or_fetch(
    symbol: str,
    timeframe: int = 5,  # M5
    n_bars: int = 5000,
    mt5_path: Optional[str] = None,
    spread_points: Optional[float] = None,
) -> Optional[SymbolData]:
    """Load data for a symbol, applying spread model.

    If spread_points is None, attempts to read current spread from MT5.
    """
    import MetaTrader5 as mt5

    bars = fetch_mt5_bars(symbol, timeframe, n_bars, mt5_path)
    if not bars:
        return None

    meta = fetch_symbol_metadata(symbol)
    if not meta:
        return None

    # Determine spread
    if spread_points is not None:
        base_spread = spread_points
    else:
        base_spread = meta.get("spread", 0.0)

    # Apply spread to all bars
    for bar in bars:
        bar.spread_points = base_spread

    # Get pip from symbol name
    pip = _detect_pip(symbol, meta.get("digits", 5))

    # Compute USD conversion factor for PnL
    # For pairs quoted in USD (EURUSD, GBPUSD): factor = 1.0
    # For pairs where USD is base (USDJPY): factor = 1/current_price (divide JPY by rate)
    usd_per_quote_unit = _compute_usd_conversion(symbol, bars[-1].close if bars else 1.0)

    return SymbolData(
        symbol=symbol,
        bars=bars,
        pip=pip,
        contract_size=meta["contract_size"],
        point=meta["point"],
        volume_step=meta["volume_step"],
        volume_min=meta["volume_min"],
        volume_max=meta["volume_max"],
        base_spread_points=base_spread,
        usd_per_quote_unit=usd_per_quote_unit,
    )


def _detect_pip(symbol: str, digits: int) -> float:
    """Detect pip size from symbol name and digits."""
    if "JPY" in symbol:
        return 0.001
    elif digits <= 3:
        return 0.01  # e.g. XAUUSD
    else:
        return 0.0001


def _compute_usd_conversion(symbol: str, current_price: float) -> float:
    """Compute factor to convert PnL from quote currency to USD.

    For pairs quoted in USD (EURUSD, GBPUSD): factor = 1.0
    For pairs where USD is base (USDJPY): factor = 1/current_price
    """
    # If symbol ends with USD, quote is USD → no conversion needed
    if symbol.upper().endswith("USD"):
        return 1.0
    # If symbol starts with USD, quote is foreign → divide by exchange rate
    if symbol.upper().startswith("USD"):
        return 1.0 / current_price if current_price > 0 else 1.0
    # Fallback: assume USD quote
    return 1.0


def apply_spread_model(
    data: SymbolData,
    stress_factor: float = 1.0,
) -> SymbolData:
    """Apply spread stress factor to all bars.

    stress_factor=1.0 → base spread
    stress_factor=1.25 → +25% spread
    stress_factor=2.0 → doubled spread
    """
    for bar in data.bars:
        bar.spread_points = data.base_spread_points * stress_factor
    return data


def split_chronological(
    data: SymbolData,
    train_pct: float = 0.6,
    val_pct: float = 0.2,
) -> tuple[SymbolData, SymbolData, SymbolData]:
    """Split data chronologically into train/val/test.

    No shuffling. No leakage.
    """
    n = len(data.bars)
    train_end = int(n * train_pct)
    val_end = int(n * (train_pct + val_pct))

    def _slice(data: SymbolData, start: int, end: int) -> SymbolData:
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
        )

    return _slice(data, 0, train_end), _slice(data, train_end, val_end), _slice(data, val_end, n)
