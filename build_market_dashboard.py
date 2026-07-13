import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).parent
OUT = ROOT / "output" / "market_dashboard"
SOURCE_VENDOR = ROOT / "vendor"


def records(df):
    return json.loads(df.to_json(orient="records", force_ascii=False))


def load_data():
    sectors = pd.read_csv(ROOT / "output" / "sector_rotation.csv")
    stocks = pd.read_csv(ROOT / "output" / "stock_week_metrics.csv")
    sector_flow = pd.read_csv(ROOT / "output" / "sector_daily_flow.csv")

    fina_path = ROOT / "data" / "fundamentals" / "fina_indicator_vip_20260331.csv"
    if fina_path.exists():
        fina = pd.read_csv(fina_path).sort_values("ann_date").drop_duplicates("ts_code", keep="last")
        fields = ["ts_code", "ann_date", "eps", "bps", "fcfe_ps", "roe", "grossprofit_margin", "netprofit_margin", "debt_to_assets", "current_ratio", "q_ocf_to_sales", "q_sales_yoy", "netprofit_yoy", "ocf_yoy"]
        stocks = stocks.merge(fina[[c for c in fields if c in fina]], on="ts_code", how="left")
    for c in ["ann_date", "eps", "bps", "fcfe_ps", "roe", "grossprofit_margin", "netprofit_margin", "debt_to_assets", "current_ratio", "q_ocf_to_sales", "q_sales_yoy", "netprofit_yoy", "ocf_yoy"]:
        if c not in stocks:
            stocks[c] = 0
        stocks[c] = pd.to_numeric(stocks[c], errors="coerce")

    def industry_rank(column, ascending=True):
        values = stocks[column].replace([float("inf"), float("-inf")], pd.NA)
        return values.groupby(stocks["industry"]).rank(pct=True, ascending=ascending)

    pe_valid = stocks["pe"].where((stocks["pe"] > 0) & (stocks["pe"] < 300))
    stocks["quality_score"] = (industry_rank("roe") * 18 + industry_rank("grossprofit_margin") * 7)
    stocks["growth_score"] = (industry_rank("q_sales_yoy") * 13 + industry_rank("netprofit_yoy") * 12)
    stocks["cash_score"] = industry_rank("q_ocf_to_sales") * 20
    stocks["leverage_score"] = industry_rank("debt_to_assets", ascending=False) * 15
    stocks["valuation_score"] = pe_valid.groupby(stocks["industry"]).rank(pct=True, ascending=False) * 15
    score_cols = ["quality_score", "growth_score", "cash_score", "leverage_score", "valuation_score"]
    stocks["fundamental_coverage"] = stocks[score_cols].notna().mean(axis=1) * 100
    stocks["fundamental_score"] = stocks[score_cols].sum(axis=1, min_count=1)

    daily_files = {}
    for path in list((ROOT / "data").glob("daily_*.csv")) + list((ROOT / "data" / "history").glob("daily_*.csv")):
        date = path.stem.split("_")[-1]
        if date.isdigit() and len(date) == 8:
            daily_files[date] = path
    prices = []
    for date, path in sorted(daily_files.items()):
        try:
            df = pd.read_csv(path)
            keep = [c for c in ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount", "pct_chg"] if c in df]
            prices.append(df[keep])
        except Exception:
            pass
    price = pd.concat(prices, ignore_index=True).drop_duplicates(["ts_code", "trade_date"]) if prices else pd.DataFrame()

    sector_cols = ["rank", "industry", "state", "constituents", "week_ret", "median_ret", "net_mf_yi", "fri_flow_yi", "breadth", "turnover_yi", "flow_ratio", "limit_up", "broken", "limit_down", "strength"]
    stock_cols = ["industry", "name", "ts_code", "market", "week_ret", "fri_ret", "net_mf_5d_yi", "net_mf_fri_yi", "circ_mv_yi", "turnover_yi", "turnover_rate", "pe", "pb", "U", "Z", "D", "leader_score", "core_score", "elastic_score", "ann_date", "eps", "bps", "fcfe_ps", "roe", "grossprofit_margin", "netprofit_margin", "debt_to_assets", "current_ratio", "q_ocf_to_sales", "q_sales_yoy", "netprofit_yoy", "ocf_yoy", "quality_score", "growth_score", "cash_score", "leverage_score", "valuation_score", "fundamental_coverage", "fundamental_score"]
    return (
        records(sectors[sector_cols]),
        records(stocks[stock_cols]),
        records(sector_flow[["industry", "trade_date", "net_mf_yi"]]),
        records(price) if not price.empty else [],
    )


def build():
    sectors, stocks, flows, prices = load_data()
    news_path = ROOT / "data" / "news" / "news_scored.csv"
    news = records(pd.read_csv(news_path)) if news_path.exists() else []
    numeric_stocks = pd.to_numeric(pd.Series([x.get("week_ret") for x in stocks]), errors="coerce").dropna()
    generated_at = pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds")
    summary = {
        "stock_count": len(stocks),
        "sector_count": len(sectors),
        "mean_ret": float(numeric_stocks.mean()) if not numeric_stocks.empty else None,
        "breadth": float((numeric_stocks > 0).mean() * 100) if not numeric_stocks.empty else None,
        "total_flow": float(pd.to_numeric(pd.Series([x.get("net_mf_5d_yi") for x in stocks]), errors="coerce").sum(min_count=1)),
        "price_dates": sorted({str(x["trade_date"]) for x in prices}),
        "generated_at": generated_at,
        "source": "TinyShare授权接口 + 本地消息快照",
        "freshness": "按最近成功抓取批次生成；非实时",
        "estimated": True,
    }
    template = (ROOT / "market_dashboard_template.html").read_text(encoding="utf-8")
    replacements = {
        "__SECTORS__": json.dumps(sectors, ensure_ascii=False, separators=(",", ":")),
        "__STOCKS__": json.dumps(stocks, ensure_ascii=False, separators=(",", ":")),
        "__FLOWS__": json.dumps(flows, ensure_ascii=False, separators=(",", ":")),
        "__PRICES__": json.dumps(prices, ensure_ascii=False, separators=(",", ":")),
        "__SUMMARY__": json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        "__NEWS__": json.dumps(news, ensure_ascii=False, separators=(",", ":")),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    OUT.mkdir(parents=True, exist_ok=True)
    vendor = OUT / "vendor"
    vendor.mkdir(exist_ok=True)
    for name in ["echarts.min.js", "tabulator.min.js", "tabulator_midnight.min.css"]:
        (vendor / name).write_bytes((SOURCE_VENDOR / name).read_bytes())
    (OUT / "index.html").write_text(template, encoding="utf-8")
    print(OUT / "index.html")


if __name__ == "__main__":
    build()
