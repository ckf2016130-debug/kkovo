import os
import time
from datetime import date, timedelta
from pathlib import Path

from tushare_proxy import create_pro


OUT = Path("data/history")


pro = create_pro(timeout=25)
OUT.mkdir(parents=True, exist_ok=True)

end = date.today()
start = end - timedelta(days=120)
cal = pro.trade_cal(exchange="SSE", start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
dates = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist())[-60:]
print("dates", dates)

for date in dates:
    target = OUT / f"daily_{date}.csv"
    existing = Path("data") / f"daily_{date}.csv"
    if target.exists() and target.stat().st_size > 100:
        print("SKIP", date)
        continue
    if existing.exists() and existing.stat().st_size > 100:
        target.write_bytes(existing.read_bytes())
        print("COPY", date)
        continue
    try:
        df = pro.daily(trade_date=date)
        df.to_csv(target, index=False, encoding="utf-8-sig")
        print("OK", date, len(df))
    except Exception as exc:
        print("FAIL", date, type(exc).__name__, exc)
    time.sleep(0.3)
