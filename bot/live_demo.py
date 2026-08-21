"""Live Demo Bot — multi-symbol scalping on MetaQuotes-Demo.

Runs the unified bot with real MT5 connection, live data, and live orders.
Auto-discovers all tradable symbols and creates strategies for each.
"""

import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, r"C:\Users\mahdi\OneDrive\Documents\New OpenCode Project\Trading King")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            r"C:\Users\mahdi\OneDrive\Documents\New OpenCode Project\Trading King\logs\demo.log"
        ),
    ],
)
logger = logging.getLogger(__name__)

import MetaTrader5 as mt5
from bot import Bot, BotConfig, TradingMode
from bot.strategies.scalp import Scalp
from bot.strategies.xauusd_scalp import XAUUSDScalp

TIMEFRAMES = {
    "EURUSD": mt5.TIMEFRAME_M5,
    "GBPUSD": mt5.TIMEFRAME_M5,
    "USDJPY": mt5.TIMEFRAME_M5,
    "USDCHF": mt5.TIMEFRAME_M5,
    "USDCAD": mt5.TIMEFRAME_M5,
    "AUDUSD": mt5.TIMEFRAME_M5,
    "NZDUSD": mt5.TIMEFRAME_M5,
    "XAUUSD": mt5.TIMEFRAME_M5,
    "USDBRL": mt5.TIMEFRAME_M5,
    "USDINR": mt5.TIMEFRAME_M5,
    "USDSGD": mt5.TIMEFRAME_M5,
    "USDHKD": mt5.TIMEFRAME_M5,
}


def get_data(symbol: str, n_bars: int = 200) -> dict:
    """Fetch OHLCV data from MT5 for any symbol."""
    tf = TIMEFRAMES.get(symbol, mt5.TIMEFRAME_M5)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, n_bars)
    if rates is None or len(rates) == 0:
        return {}

    return {
        "opens": [r["open"] for r in rates],
        "highs": [r["high"] for r in rates],
        "lows": [r["low"] for r in rates],
        "closes": [r["close"] for r in rates],
        "volumes": [r["tick_volume"] for r in rates],
        "times": [r["time"] for r in rates],
    }


def discover_symbols() -> list[str]:
    """Auto-discover all tradable symbols with reasonable spreads."""
    from bot.strategies.scalp import PROFILES

    symbols = mt5.symbols_get()
    available = []
    for s in symbols:
        if s.trade_mode != 4:
            continue
        if s.spread <= 0:
            continue
        if s.volume_min > 0.01:
            continue
        profile = PROFILES.get(s.name)
        if profile and s.spread <= profile["spread_cap"]:
            available.append(s.name)
            logger.info("  %s: spread=%d pip=%.4f contract=%.0f",
                        s.name, s.spread, profile["pip"], s.trade_contract_size)
    return available


def main():
    logger.info("=" * 60)
    logger.info("  LIVE DEMO BOT - Multi-Symbol Scalping")
    logger.info("=" * 60)

    # Initialize MT5 first for symbol checks
    if not mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe"):
        logger.error("MT5 initialize failed")
        return

    # Discover available symbols
    available = discover_symbols()

    if not available:
        logger.error("No symbols available!")
        return

    # Create config
    # DEMO mode: places orders on demo account (fake money)
    config = BotConfig(
        trading_mode=TradingMode.PAPER,
        mt5_path=r"C:\Program Files\MetaTrader 5\terminal64.exe",
        mt5_login=5054003370,
        mt5_password="",
        mt5_server="MetaQuotes-Demo",
        max_risk_per_trade_pct=0.02,
        max_daily_loss_pct=0.05,
        max_drawdown_pct=0.15,
        max_concurrent_positions=5,
        cooldown_base_minutes=3,
        vol_target_annual=0.20,
        magic=20250820,
        slippage_pts=5.0,
        max_spread_pts=100.0,
        min_signal_confidence=0.50,
        instruments=available,
        state_dir=r"C:\Users\mahdi\OneDrive\Documents\New OpenCode Project\Trading King\state",
        log_dir=r"C:\Users\mahdi\OneDrive\Documents\New OpenCode Project\Trading King\logs",
    )

    bot = Bot(config)

    # Create one strategy per symbol
    for sym in available:
        if sym == "XAUUSD":
            strategy = XAUUSDScalp(
                symbol=sym,
                ema_fast=9,
                ema_slow=21,
                rsi_period=14,
                atr_period=14,
                stop_atr_mult=1.2,
                target_atr_mult=1.8,
                min_stop_atr=0.8,
                min_score=0.50,
                min_bars=30,
                timeframe=5,
                session_start_hour=7,
                session_end_hour=22,
                risk_per_trade_pct=0.004,
                fixed_lots=0.01,
                max_open=5,
                max_hold_minutes=240,
                cooldown_minutes=60,
                use_chop_filter=True,
                use_trend_filter=True,
            )
        else:
            strategy = Scalp(
                symbol=sym,
                ema_fast=5,
                ema_slow=20,
                rsi_period=14,
                rsi_oversold=35,
                rsi_overbought=65,
                atr_period=14,
                stop_atr_mult=1.5,
                target_atr_mult=2.5,
                min_bars=30,
                timeframe=5,
            )
        bot.add_strategy(strategy)

    # Monkey-patch _get_data to use live MT5 data per symbol
    def live_get_data(strategy_name: str, symbol: str = None) -> dict:
        if symbol is None:
            symbol = "EURUSD"
        return get_data(symbol, 200)

    bot._get_data = live_get_data

    # Run
    logger.info("Starting bot with %d strategies for %d symbols",
                len(bot._strategies), len(available))
    logger.info("Instruments: %s", available)
    logger.info("Max risk per trade: %.1f%%", config.max_risk_per_trade_pct * 100)
    logger.info("Max concurrent positions: %d", config.max_concurrent_positions)

    bot.run()


if __name__ == "__main__":
    main()
