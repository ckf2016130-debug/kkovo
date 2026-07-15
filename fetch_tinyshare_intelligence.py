import json
import os
from pathlib import Path

import tinyshare as ts

from fetch_tinyshare_week import OUT, fetch, fetch_active_concepts, fetch_focus_etf_components


def main():
    OUT.mkdir(exist_ok=True)
    ts.set_token(os.environ["TINYSHARE_TOKEN"])
    pro = ts.pro_api()
    pro.timeout = 20

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
    results.extend(fetch_active_concepts(pro, latest_date, history_start, latest_date))
    results.append(fetch_focus_etf_components(pro, latest_date))

    path = Path("data/intelligence_manifest.json")
    path.write_text(json.dumps({"trade_date": latest_date, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
