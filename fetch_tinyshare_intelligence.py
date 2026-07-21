import json
import os
from pathlib import Path

import pandas as pd
from tushare_proxy import create_pro

from fetch_tinyshare_week import OUT, fetch, fetch_active_concepts, fetch_focus_etf_components


def fetch_focus_etf_valuation(pro, dates):
    latest_date = dates[-1]
    nav_path = OUT / f"fund_nav_focus_{latest_date}.csv"
    adj_path = OUT / f"fund_adj_focus_{latest_date}.csv"
    try:
        daily = pd.read_csv(OUT / f"etf_daily_{latest_date}.csv")
        basic = pd.read_csv(OUT / "etf_basic.csv")
        frame = daily.merge(basic[["ts_code", "name", "benchmark"]], on="ts_code", how="left")
        frame["amount"] = pd.to_numeric(frame.get("amount"), errors="coerce")
        text = frame["name"].fillna("").astype(str) + " " + frame["benchmark"].fillna("").astype(str)
        excluded = text.str.contains("债|货币|同业存单|短融|政金|美元|日元", regex=True)
        codes = frame[~excluded].sort_values("amount", ascending=False).drop_duplicates("ts_code").head(12)["ts_code"].astype(str).tolist()
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, KeyError) as exc:
        return [{"api": "fund_focus", "rows": 0, "file": None, "error": str(exc)}]

    nav_rows = []
    adj_rows = []
    errors = []
    for code in codes:
        try:
            nav = pro.query("fund_nav", ts_code=code, start_date=dates[0], end_date=latest_date)
            if not nav.empty:
                nav_rows.append(nav)
        except Exception as exc:
            errors.append(f"{code} nav: {exc}")
        try:
            adj = pro.query("fund_adj", ts_code=code, start_date=dates[0], end_date=latest_date)
            if not adj.empty:
                adj_rows.append(adj)
        except Exception as exc:
            errors.append(f"{code} adj: {exc}")
    results = []
    if nav_rows:
        nav = pd.concat(nav_rows, ignore_index=True).drop_duplicates()
        nav.to_csv(nav_path, index=False, encoding="utf-8-sig")
        print(f"OK fund_nav_focus: {len(nav):,} rows -> {nav_path}")
        results.append({"api": "fund_nav_focus", "rows": len(nav), "file": str(nav_path), "error": "; ".join(errors) or None})
    else:
        results.append({"api": "fund_nav_focus", "rows": 0, "file": str(nav_path), "error": "; ".join(errors) or "empty response"})
    if adj_rows:
        adj = pd.concat(adj_rows, ignore_index=True).drop_duplicates()
        adj.to_csv(adj_path, index=False, encoding="utf-8-sig")
        print(f"OK fund_adj_focus: {len(adj):,} rows -> {adj_path}")
        results.append({"api": "fund_adj_focus", "rows": len(adj), "file": str(adj_path), "error": "; ".join(errors) or None})
    else:
        results.append({"api": "fund_adj_focus", "rows": 0, "file": str(adj_path), "error": "; ".join(errors) or "empty response"})
    return results


def main():
    OUT.mkdir(exist_ok=True)
    pro = create_pro(timeout=20)

    daily_dates = sorted(
        path.stem.split("_")[-1]
        for path in OUT.glob("etf_daily_*.csv")
        if path.stem.split("_")[-1].isdigit()
    )
    stock_dates = sorted(
        path.stem.split("_")[-1]
        for path in list(OUT.glob("daily_*.csv")) + list((OUT / "history").glob("daily_*.csv"))
        if path.stem.split("_")[-1].isdigit()
    )
    if not daily_dates or not stock_dates:
        raise RuntimeError("ETF or stock daily snapshots are unavailable")

    latest_date = daily_dates[-1]
    history_start = stock_dates[-60] if len(stock_dates) >= 60 else stock_dates[0]
    results = []
    for trade_date in daily_dates[-5:]:
        results.append(fetch(pro, "fund_adj", f"fund_adj_{trade_date}.csv", trade_date=trade_date))
    results.append(fetch(pro, "fund_nav", f"fund_nav_{latest_date}.csv", nav_date=latest_date, market="E"))
    results.extend(fetch_focus_etf_valuation(pro, daily_dates[-5:]))
    results.extend(fetch_active_concepts(pro, latest_date, history_start, latest_date))
    results.append(fetch_focus_etf_components(pro, latest_date))

    path = Path("data/intelligence_manifest.json")
    path.write_text(json.dumps({"trade_date": latest_date, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
