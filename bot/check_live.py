import MetaTrader5 as mt5
mt5.initialize(path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe")

positions = mt5.positions_get()
for p in positions:
    side = "BUY" if p.type == 0 else "SELL"
    sl_ok = (p.type == 0 and p.sl < p.price_open) or (p.type == 1 and p.sl > p.price_open)
    print(f"{p.symbol:8s} {side:4s} entry={p.price_open:.5f} sl={p.sl:.5f} tp={p.tp:.5f} pnl={p.profit:+.2f} sl_correct={sl_ok}")

account = mt5.account_info()
print(f"\nBalance: {account.balance:.2f} Equity: {account.equity:.2f}")

# Check deal history
from datetime import datetime, timedelta
deals = mt5.history_deals_get(datetime.now() - timedelta(hours=24), datetime.now())
closed = [d for d in deals if d.entry == 1]
total_closed_pnl = sum(d.profit for d in closed)
print(f"\nClosed deals (24h): {len(closed)} trades, PnL: {total_closed_pnl:+.2f}")
for d in closed:
    print(f"  {d.symbol:8s} pnl={d.profit:+.2f} price={d.price}")

mt5.shutdown()

