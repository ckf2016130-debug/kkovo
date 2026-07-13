import os
import time
from datetime import date, timedelta
from pathlib import Path

import tinyshare as ts


OUT = Path("data/history")
ts.set_token(os.environ["TINYSHARE_TOKEN"])
pro = ts.pro_api()
pro.timeout = 25
OUT.mkdir(parents=True, exist_ok=True)

end = date.today()
start = end - timedelta(days=120)
cal = pro.trade_cal(
    exchange="SSE",
    start_date=start.strftime("%Y%m%d"),
    end_date=end.strftime("%Y%m%d"),
)
dates = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist())[-60:]
print("K-line history dates", dates[0] if dates else None, dates[-1] if dates else None)

for trade_date in dates:
    target = OUT / f"daily_{trade_date}.csv"
    existing = Path("data") / f"daily_{trade_date}.csv"
    if target.exists() and target.stat().st_size > 100:
        continue
    if existing.exists() and existing.stat().st_size > 100:
        target.write_bytes(existing.read_bytes())
        continue
    try:
        frame = pro.daily(trade_date=trade_date)
        if not frame.empty:
            frame.to_csv(target, index=False, encoding="utf-8-sig")
            print("OK", trade_date, len(frame))
        else:
            print("EMPTY", trade_date)
    except Exception as exc:
        print("FAIL", trade_date, type(exc).__name__, exc)
    time.sleep(0.3)
