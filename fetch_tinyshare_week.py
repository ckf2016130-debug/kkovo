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


def fetch_active_concepts(pro, trade_date, start_date, end_date):
    """Fetch the active THS concept universe and only the members we can use now."""
    results = []
    flow_path = OUT / f"ths_moneyflow_{trade_date}.csv"
    results.append(fetch(pro, "ths_index", "ths_index.csv", exchange="A", type="N"))
    results.append(fetch(pro, "moneyflow_cnt_ths", flow_path.name, trade_date=trade_date))
    try:
        flow = pd.read_csv(flow_path)
        flow["net_amount"] = pd.to_numeric(flow.get("net_amount"), errors="coerce")
        flow["pct_change"] = pd.to_numeric(flow.get("pct_change"), errors="coerce")
        ranked = flow.assign(
            activity_score=flow["net_amount"].abs().fillna(0) + flow["pct_change"].abs().fillna(0) * 5
        ).sort_values("activity_score", ascending=False)
        active = ranked.dropna(subset=["ts_code"]).drop_duplicates("ts_code").head(16)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, KeyError) as exc:
        results.append({"api": "ths_active", "rows": 0, "file": None, "error": str(exc)})
        return results

    member_rows = []
    daily_rows = []
    errors = []
    for row in active.to_dict("records"):
        code = str(row.get("ts_code") or "")
        if not code:
            continue
        try:
            members = pro.query("ths_member", ts_code=code)
            if not members.empty:
                members["concept_code"] = code
                members["concept_name"] = row.get("name")
                member_rows.append(members)
        except Exception as exc:
            errors.append(f"{code} members: {exc}")
        try:
            history = pro.query("ths_daily", ts_code=code, start_date=start_date, end_date=end_date)
            if not history.empty:
                history["concept_name"] = row.get("name")
                daily_rows.append(history)
        except Exception as exc:
            errors.append(f"{code} daily: {exc}")
        time.sleep(0.2)

    member_path = OUT / f"ths_active_members_{trade_date}.csv"
    daily_path = OUT / f"ths_active_daily_{trade_date}.csv"
    if member_rows:
        members = pd.concat(member_rows, ignore_index=True).drop_duplicates()
        members.to_csv(member_path, index=False, encoding="utf-8-sig")
        results.append({"api": "ths_member", "rows": len(members), "file": str(member_path), "error": "; ".join(errors) or None})
    else:
        results.append({"api": "ths_member", "rows": 0, "file": str(member_path), "error": "; ".join(errors) or "empty response"})
    if daily_rows:
        history = pd.concat(daily_rows, ignore_index=True).drop_duplicates()
        history.to_csv(daily_path, index=False, encoding="utf-8-sig")
        results.append({"api": "ths_daily", "rows": len(history), "file": str(daily_path), "error": "; ".join(errors) or None})
    else:
        results.append({"api": "ths_daily", "rows": 0, "file": str(daily_path), "error": "; ".join(errors) or "empty response"})
    return results


def fetch_focus_etf_components(pro, trade_date):
    """Fetch complete PCF baskets for liquid equity ETFs instead of a truncated exchange dump."""
    daily_path = OUT / f"etf_daily_{trade_date}.csv"
    basic_path = OUT / "etf_basic.csv"
    target_path = OUT / f"etf_focus_cons_{trade_date}.csv"
    try:
        daily = pd.read_csv(daily_path)
        basic = pd.read_csv(basic_path)
        frame = daily.merge(basic[["ts_code", "name", "benchmark"]], on="ts_code", how="left")
        frame["amount"] = pd.to_numeric(frame.get("amount"), errors="coerce")
        text = frame["name"].fillna("").astype(str) + " " + frame["benchmark"].fillna("").astype(str)
        excluded = text.str.contains("债|货币|同业存单|短融|政金|美元|日元", regex=True)
        targets = frame[~excluded].sort_values("amount", ascending=False).drop_duplicates("ts_code").head(10)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, KeyError) as exc:
        return {"api": "etf_focus_cons", "rows": 0, "file": str(target_path), "error": str(exc)}

    rows = []
    errors = []
    for code in targets["ts_code"].astype(str):
        api_name = "etf_sh_cons" if code.endswith(".SH") else "etf_sz_cons"
        try:
            frame = pro.query(api_name, trade_date=trade_date, ts_code=code)
            if not frame.empty:
                rows.append(frame)
        except Exception as exc:
            errors.append(f"{code}: {exc}")
        time.sleep(0.2)
    if not rows:
        return {"api": "etf_focus_cons", "rows": 0, "file": str(target_path), "error": "; ".join(errors) or "empty response"}
    combined = pd.concat(rows, ignore_index=True).drop_duplicates()
    combined.to_csv(target_path, index=False, encoding="utf-8-sig")
    return {"api": "etf_focus_cons", "rows": len(combined), "file": str(target_path), "error": "; ".join(errors) or None}


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
