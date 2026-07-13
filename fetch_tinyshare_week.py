import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import tinyshare as ts


OUT = Path("data")


def fetch(pro, api_name, filename, **kwargs):
    path = OUT / filename
    try:
        df = pro.query(api_name, **kwargs)
        if df.empty:
            if path.exists() and path.stat().st_size > 0:
                print(f"EMPTY {api_name}: keep previous snapshot at {path}")
                return {"api": api_name, "rows": 0, "file": str(path), "error": "empty response; previous snapshot kept"}
            raise ValueError("empty response and no previous snapshot")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"OK {api_name}: {len(df):,} rows -> {path}")
        return {"api": api_name, "rows": len(df), "file": str(path), "error": None}
    except Exception as exc:
        print(f"FAIL {api_name}: {type(exc).__name__}: {exc}")
        return {"api": api_name, "rows": 0, "file": str(path), "error": str(exc)}


def main():
    OUT.mkdir(exist_ok=True)
    ts.set_token(os.environ["TINYSHARE_TOKEN"])
    pro = ts.pro_api()
    pro.timeout = 15
    manifest = []

    end_date = date.today()
    cal = pro.trade_cal(exchange="SSE", start_date=(end_date - timedelta(days=14)).strftime("%Y%m%d"), end_date=end_date.strftime("%Y%m%d"))
    dates = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist())
    dates = dates[-5:]
    start_date, end_date = dates[0], dates[-1]
    print("Window:", start_date, end_date)
    print("Trading dates:", dates)

    manifest.append(fetch(
        pro, "stock_basic", "stock_basic.csv", exchange="", list_status="L",
        fields="ts_code,symbol,name,area,industry,market,list_date"
    ))

    for trade_date in dates:
        for api_name, prefix, fields in [
            ("daily", "daily", ""),
            ("daily_basic", "daily_basic", "ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,pe,pb,total_share,float_share,free_share,total_mv,circ_mv"),
            ("moneyflow", "moneyflow", ""),
            ("limit_list_d", "limit_list", ""),
        ]:
            kwargs = {"trade_date": trade_date}
            if fields:
                kwargs["fields"] = fields
            manifest.append(fetch(pro, api_name, f"{prefix}_{trade_date}.csv", **kwargs))
            time.sleep(0.25)

    for api_name, filename, kwargs in [
        ("index_classify", "sw_index_classify.csv", {"level": "L1", "src": "SW2021"}),
        ("index_member_all", "sw_index_members.csv", {"l1_code": ""}),
        ("moneyflow_hsgt", "moneyflow_hsgt.csv", {"start_date": start_date, "end_date": end_date}),
        ("margin", "margin.csv", {"start_date": start_date, "end_date": end_date}),
    ]:
        manifest.append(fetch(pro, api_name, filename, **kwargs))

    (OUT / "manifest.json").write_text(
        json.dumps({"start_date": start_date, "end_date": end_date, "results": manifest}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
