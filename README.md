# 📊 QuantDesk

A research-driven **algorithmic trading desk** for MetaTrader 5 — built like a professional systematic-trading operation, not an indicator-stacked retail bot.

![CI](https://github.com/mahdiibenali/quantdesk/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.12+-blue?logo=python)
![MetaTrader5](https://img.shields.io/badge/MetaTrader-5-black?logo=metatrader5)
![numpy](https://img.shields.io/badge/numpy-powered-013243?logo=numpy)
![License](https://img.shields.io/badge/License-MIT-green)

## 🎯 Philosophy

> The goal is NOT to make a bot "more aggressive" or curve-fit parameters until history looks good.
> The goal is a system that behaves like a disciplined trading desk: honest out-of-sample validation, realistic execution costs, and a risk manager that can veto everything.

## 🛡️ The hard safety net

Every order passes through a mode gate — `TRADING_MODE` ∈ `RESEARCH` (default) · `VALIDATION` · `PAPER` · `LIVE`. Order submission is **refused outside LIVE**, and the live launcher additionally excludes the riskiest symbols. Research code cannot accidentally trade money.

## 🏛️ Desk structure

```
bot/
├── research/        # The lab: data pipeline, cost model, walk-forward, phase tournaments
│   └── phase3/      # Strategy tournament + pre-registered scoring
├── backtest/        # Event-driven backtest engine with explicit costs
├── execution/       # MT5 order execution engine
├── risk/            # Position sizing & portfolio-level risk manager
└── strategies/      # Specialist strategy implementations
```

## 🔬 Research methodology

- **Phase-gated validation** — strategies must survive each phase before touching capital; promotion verdicts (`REJECTED / UNPROVEN / PROMISING / STRONG`) were written in `phase3/scoring.py` *before* any results existed
- **Walk-forward analysis with overfit detection** — chronological folds flag strategies whose out-of-sample expectancy decays vs in-sample (`is_overfit`, `expectancy_decay`)
- **Realistic execution modeling** — BUY enters at ASK and exits at BID (and inverse), with BASE / REALISTIC / STRESSED cost scenarios priced in R-multiples
- **Account-relative risk engine** — volatility targeting, inverse-vol sizing, progressive cooldown escalation, daily-loss/drawdown limits with emergency flatten-all
- **Tournament selection** — breakout, mean-reversion, pullback and trend-continuation families compete on out-of-sample folds

## 🚀 Quick start

### Prerequisites

- Windows (MetaTrader5 Python API requirement)
- A local [MetaTrader 5 terminal](https://www.metatrader5.com/) installed
- Python ≥ 3.12

```bash
pip install -r requirements.txt          # MetaTrader5, numpy

# Research pipeline (no terminal needed for pure-backtest phases)
python bot/research/backtest_runner.py --symbol EURUSD --bars 5000 --output research_output

# Pre-flight live checks (read-only)
python bot/check_live.py

# Demo-account launcher
python bot/live_demo.py

# Live launcher — gated behind TradingMode.LIVE
set TRADING_MODE=LIVE && python bot/live_trade.py
```

> ⚠️ Live modules are intentionally gated behind completed validation phases. Trading leveraged products carries substantial risk of loss. Educational/research project — not financial advice.

**Status:** active research project — the desk currently runs on EURUSD/FX majors; test suite and CI beyond syntax gating are on the roadmap.

## 🔭 Roadmap

- [ ] pytest suites for agents, bid/ask execution and emergency flattening (spec'd in [`docs/RESEARCH_SPEC.md`](docs/RESEARCH_SPEC.md))
- [ ] Config-file-driven credentials instead of launcher fields
- [ ] Multi-symbol portfolio tournaments
- [ ] Telemetry dashboard for run attribution

## 📄 License

MIT — see [LICENSE](LICENSE).
