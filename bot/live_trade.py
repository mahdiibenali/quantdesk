"""Live Trading Bot — connects to the already-running broker MT5 terminal.

Same strategy as demo but on live account. The broker terminal must be
open and logged in before running this script.
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
            r"C:\Users\mahdi\OneDrive\Documents\New OpenCode Project\Trading King\logs\live.log"
        ),
    ],
)
logger = logging.getLogger(__name__)

import MetaTrader5 as mt5
from bot import Bot, BotConfig, TradingMode
from bot.strategies.scalp import Scalp

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

LIVE_PATH = r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"


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
    logger.info("  LIVE TRADING BOT - DemoBroker-Live")
    logger.info("=" * 60)

    # Connect to already-running broker terminal (no login needed)
    if not mt5.initialize(path=LIVE_PATH):
        logger.error("MT5 initialize failed — is broker terminal open?")
        return

    account = mt5.account_info()
    logger.info("Connected: %s | Login: %s | Balance: $%.2f",
                account.server, account.login, account.balance)

    # Discover available symbols
    available = discover_symbols()

    # Filter out XAUUSD — spread too wide for scalping
    available = [s for s in available if s != "XAUUSD"]

    if not available:
        logger.error("No symbols available!")
        return

    # Create config — conservative for $31 account
    # LIVE mode: places real orders with real money
    config = BotConfig(
        trading_mode=TradingMode.LIVE,
        mt5_path=LIVE_PATH,
        mt5_login=12345678,
        mt5_password="",
        mt5_server="DemoBroker-Live",
        max_risk_per_trade_pct=0.01,
        max_daily_loss_pct=0.03,
        max_drawdown_pct=0.10,
        max_concurrent_positions=3,
        cooldown_base_minutes=5,
        vol_target_annual=0.15,
        magic=20250821,
        slippage_pts=5.0,
        max_spread_pts=100.0,
        min_signal_confidence=0.55,
        instruments=available,
        state_dir=r"C:\Users\mahdi\OneDrive\Documents\New OpenCode Project\Trading King\state",
        log_dir=r"C:\Users\mahdi\OneDrive\Documents\New OpenCode Project\Trading King\logs",
    )

    bot = Bot(config)

    # Create one strategy per symbol (no XAUUSD)
    for sym in available:
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


