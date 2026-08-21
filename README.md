# 📊 QuantDesk

A research-driven, **multi-specialist algorithmic trading desk** for XAUUSD on MetaTrader 5 — built like a professional systematic-trading operation, not an indicator-stacked retail bot.

![Python](https://img.shields.io/badge/Python-3.12+-blue?logo=python)
![MetaTrader5](https://img.shields.io/badge/MetaTrader-5-black?logo=metatrader5)
![pandas](https://img.shields.io/badge/pandas-powered-150458?logo=pandas)
![License](https://img.shields.io/badge/License-MIT-green)

## 🎯 Philosophy

> The goal is NOT to make a bot "more aggressive" or curve-fit parameters until history looks good.
> The goal is a system that behaves like a disciplined trading desk: independent specialists, regime awareness, honest out-of-sample validation, and risk management that can veto everything.

## 🏛️ Desk structure

```
bot/
├── research/        # The lab: data pipeline, cost model, walk-forward, phase tournaments
│   └── phase3/      # Strategy tournament: breakout · mean-reversion · pullback · trend-continuation
├── backtest/        # Event-driven backtest engine with realistic costs
├── execution/       # MT5 order execution engine
├── risk/            # Position sizing & portfolio-level risk manager
├── strategies/      # Specialist strategy implementations
└── data/            # Market data access & caching
```

## 🔬 Research methodology

- **Phase-gated validation** — strategies must survive each research phase before touching capital
- **Walk-forward analysis** — no single backtest number is trusted
- **Tournament selection** — candidate strategies (breakout, mean-reversion, pullback, trend-continuation) compete on out-of-sample folds
- **Explicit cost model** — spread, slippage and commission priced into every experiment
- **Telemetry** — every run is instrumented for later attribution
- **Hard go-live rule** — the desk trades live only after all validation phases pass

## 🚀 Quick start

```bash
pip install -r requirements.txt   # MetaTrader5, pandas, numpy
python bot/research/backtest_runner.py     # run the research pipeline
python bot/check_live.py                   # pre-flight live checks (read-only)
```

> ⚠️ Live modules (`live_trade.py`) are intentionally gated behind completed validation phases. Trading leveraged products carries substantial risk.

## 📄 License

MIT — see [LICENSE](LICENSE).
