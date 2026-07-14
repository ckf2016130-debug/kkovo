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
    cal = pro.trade_cal(exchange="SSE", start_date=(end_date - timedelta(days=120)).strftime("%Y%m%d"), end_date=end_date.strftime("%Y%m%d"))
    all_dates = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist())
    history_dates = all_dates[-60:]
    dates = history_dates[-5:]
    start_date, end_date = dates[0], dates[-1]
    print("K-line window:", history_dates[0], history_dates[-1])
    print("Flow window:", start_date, end_date)

    manifest.append(fetch(
        pro, "stock_basic", "stock_basic.csv", exchange="", list_status="L",
        fields="ts_code,symbol,name,area,industry,market,list_date"
    ))
    manifest.append(fetch(
        pro, "fund_basic", "etf_basic.csv", market="E", status="L", fund_type="ETF",
        fields="ts_code,name,management,market,fund_type,list_date,benchmark,invest_type,type,issue_amount"
    ))

    for trade_date in history_dates:
        for api_name, prefix, fields in [
            ("daily", "daily", ""),
            ("daily_basic", "daily_basic", "ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,pe,pb,total_share,float_share,free_share,total_mv,circ_mv"),
            ("moneyflow", "moneyflow", ""),
            ("limit_list_d", "limit_list", ""),
        ]:
            if trade_date not in dates and api_name != "daily":
                continue
            kwargs = {"trade_date": trade_date}
            if fields:
                kwargs["fields"] = fields
            manifest.append(fetch(pro, api_name, f"{prefix}_{trade_date}.csv", **kwargs))
            time.sleep(0.25)
        if trade_date in dates:
            manifest.append(fetch(pro, "fund_daily", f"etf_daily_{trade_date}.csv", trade_date=trade_date))
            manifest.append(fetch(pro, "fund_share", f"fund_share_{trade_date}.csv", trade_date=trade_date))
            if trade_date == dates[-1]:
                manifest.append(fetch(pro, "etf_sh_cons", f"etf_sh_cons_{trade_date}.csv", trade_date=trade_date))
                manifest.append(fetch(pro, "etf_sz_cons", f"etf_sz_cons_{trade_date}.csv", trade_date=trade_date))
                manifest.append(fetch(pro, "fund_nav", f"fund_nav_{trade_date}.csv", trade_date=trade_date))
            time.sleep(0.25)

    for api_name, filename, kwargs in [
        ("index_classify", "sw_index_classify.csv", {"level": "L1", "src": "SW2021"}),
        ("index_member_all", "sw_index_members.csv", {"l1_code": ""}),
        ("moneyflow_hsgt", "moneyflow_hsgt.csv", {"start_date": start_date, "end_date": end_date}),
        ("margin", "margin.csv", {"start_date": start_date, "end_date": end_date}),
    ]:
        manifest.append(fetch(pro, api_name, filename, **kwargs))

    # Index data is required to distinguish index strength from stock breadth.
    # Keep each benchmark separate so a failed index does not erase the others.
    for index_code in ["000001.SH", "000300.SH", "000905.SH", "000852.SH", "399006.SZ"]:
        manifest.append(fetch(pro, "index_daily", f"index_daily_{index_code.replace('.', '_')}.csv", ts_code=index_code, start_date=history_dates[0], end_date=history_dates[-1]))

    (OUT / "manifest.json").write_text(
        json.dumps({"start_date": start_date, "end_date": end_date, "results": manifest}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
