import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).parent
OUT = ROOT / "output" / "market_dashboard"
SOURCE_VENDOR = ROOT / "vendor"


def records(df):
    return json.loads(df.to_json(orient="records", force_ascii=False))


def load_etfs():
    basic_path = ROOT / "data" / "etf_basic.csv"
    if not basic_path.exists():
        return []
    try:
        basic = pd.read_csv(basic_path)
        paths = sorted((ROOT / "data").glob("etf_daily_*.csv"))
        rows = []
        for path in paths:
            if path.stat().st_size <= 20:
                continue
            frame = pd.read_csv(path)
            if not frame.empty:
                rows.append(frame)
        if not rows:
            return []
        daily = pd.concat(rows, ignore_index=True)
        daily["trade_date"] = daily["trade_date"].astype(str)
        daily["close"] = pd.to_numeric(daily["close"], errors="coerce")
        daily = daily.dropna(subset=["ts_code", "trade_date", "close"]).sort_values(["ts_code", "trade_date"])
        first = daily.groupby("ts_code", as_index=False).first()[["ts_code", "trade_date", "close"]].rename(columns={"trade_date":"start_date", "close":"start_close"})
        last = daily.groupby("ts_code", as_index=False).last()[["ts_code", "trade_date", "close", "amount"]].rename(columns={"trade_date":"trade_date", "close":"close", "amount":"amount"})
        out = basic.merge(last, on="ts_code", how="inner").merge(first, on="ts_code", how="left")
        out["week_ret"] = (out["close"] / out["start_close"] - 1) * 100
        out["amount_yi"] = pd.to_numeric(out.get("amount"), errors="coerce") / 100000000
        out = out.sort_values("amount_yi", ascending=False)
        return records(out[[c for c in ["ts_code", "name", "fund_type", "trade_date", "close", "week_ret", "amount_yi"] if c in out]])
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, KeyError):
        return []


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
    etfs = load_etfs()

    sector_cols = ["rank", "industry", "state", "constituents", "week_ret", "median_ret", "net_mf_yi", "fri_flow_yi", "breadth", "turnover_yi", "flow_ratio", "limit_up", "broken", "limit_down", "strength"]
    stock_cols = ["industry", "name", "ts_code", "market", "week_ret", "fri_ret", "net_mf_5d_yi", "net_mf_fri_yi", "circ_mv_yi", "turnover_yi", "turnover_rate", "pe", "pb", "U", "Z", "D", "leader_score", "core_score", "elastic_score", "ann_date", "eps", "bps", "fcfe_ps", "roe", "grossprofit_margin", "netprofit_margin", "debt_to_assets", "current_ratio", "q_ocf_to_sales", "q_sales_yoy", "netprofit_yoy", "ocf_yoy", "quality_score", "growth_score", "cash_score", "leverage_score", "valuation_score", "fundamental_coverage", "fundamental_score"]
    return (
        records(sectors[sector_cols]),
        records(stocks[stock_cols]),
        records(sector_flow[["industry", "trade_date", "net_mf_yi"]]),
        records(price) if not price.empty else [],
        etfs,
    )


def build():
    sectors, stocks, flows, prices, etfs = load_data()
    news_path = ROOT / "data" / "news" / "news_scored.csv"
    news = records(pd.read_csv(news_path)) if news_path.exists() else []
    numeric_stocks = pd.to_numeric(pd.Series([x.get("week_ret") for x in stocks]), errors="coerce").dropna()
    generated_at = pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds")
    pe = pd.to_numeric(pd.Series([x.get("pe") for x in stocks]), errors="coerce")
    pe = pe[(pe > 0) & (pe < 300)].dropna()
    median_pe = float(pe.median()) if not pe.empty else None
    pe_history = []
    for path in (ROOT / "data").glob("daily_basic_*.csv"):
        try:
            frame = pd.read_csv(path)
            values = pd.to_numeric(frame.get("pe"), errors="coerce")
            values = values[(values > 0) & (values < 300)].dropna()
            if not values.empty:
                pe_history.append(float(values.median()))
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
            continue
    pe_percentile = float(sum(v <= median_pe for v in pe_history) / len(pe_history) * 100) if median_pe is not None and pe_history else None
    breadth = float((numeric_stocks > 0).mean() * 100) if not numeric_stocks.empty else None
    flow = pd.to_numeric(pd.Series([x.get("net_mf_5d_yi") for x in stocks]), errors="coerce")
    positive_flow = float((flow > 0).mean() * 100) if not flow.dropna().empty else None
    broken = sum(float(x.get("Z") or 0) for x in stocks)
    limit_up = sum(float(x.get("U") or 0) for x in stocks)
    sentiment = min(100, max(0, (breadth or 0) * 0.55 + (positive_flow or 0) * 0.25 + min(limit_up, 100) * 0.15 - min(broken, 100) * 0.10))
    money_effect = min(100, max(0, (breadth or 0) * 0.65 + (positive_flow or 0) * 0.20 + min(limit_up, 100) * 0.15))
    mean_ret = float(numeric_stocks.mean()) if not numeric_stocks.empty else 0
    total_stock_flow = float(flow.sum(min_count=1)) if not flow.dropna().empty else None
    if breadth is not None and breadth >= 65 and mean_ret > 0 and (positive_flow or 0) >= 55 and (total_stock_flow or 0) > 0:
        market_state, strategy = "增量上涨", "顺势跟随主线，但只做有资金和龙头验证的方向"
    elif breadth is not None and breadth >= 55 and mean_ret > 0 and (total_stock_flow or 0) <= 0:
        market_state, strategy = "缩量上涨", "控制追高，优先观察低位承接和回流确认"
    elif breadth is not None and breadth <= 35 and mean_ret < 0 and (total_stock_flow or 0) < 0:
        market_state, strategy = "放量下跌", "降低仓位，等待风险释放后再看修复"
    elif breadth is not None and breadth <= 35 and mean_ret < 0:
        market_state, strategy = "情绪退潮", "以防守和等待为主，不接力弱势反弹"
    elif mean_ret > 0 and (positive_flow or 0) < 50:
        market_state, strategy = "存量轮动", "低吸有资金承接的板块，避免追逐已经加速的方向"
    elif breadth is not None and breadth >= 65 and mean_ret > 0:
        market_state, strategy = "普涨修复", "可跟踪修复主线，但需要成交额和资金继续确认"
    else:
        market_state, strategy = "高位分歧", "控制总仓位，等待强弱方向和资金流向重新收敛"
    stocks_frame = pd.DataFrame(stocks)
    for col in ["circ_mv_yi", "turnover_rate", "net_mf_5d_yi", "U", "Z", "week_ret"]:
        stocks_frame[col] = pd.to_numeric(stocks_frame.get(col), errors="coerce")
    cap_mid = stocks_frame["circ_mv_yi"].median()
    turn_mid = stocks_frame["turnover_rate"].median()
    proxy_specs = [
        ("国家队代理", (stocks_frame["circ_mv_yi"] >= cap_mid) & (stocks_frame["net_mf_5d_yi"] > 0), "大市值与权重股资金承接"),
        ("机构代理", (stocks_frame["circ_mv_yi"] >= cap_mid) & (stocks_frame["turnover_rate"] <= turn_mid) & (stocks_frame["net_mf_5d_yi"] > 0), "大市值、低换手、持续净流入"),
        ("游资代理", (stocks_frame["U"].fillna(0) + stocks_frame["Z"].fillna(0) > 0) & (stocks_frame["turnover_rate"] >= turn_mid), "涨停/炸板与高换手行为"),
        ("散户代理", (stocks_frame["circ_mv_yi"] < cap_mid) & (stocks_frame["turnover_rate"] >= turn_mid), "小市值与高换手行为"),
    ]
    proxy_funds = []
    for name, mask, basis in proxy_specs:
        sample = stocks_frame.loc[mask]
        value = float(sample["net_mf_5d_yi"].sum()) if not sample.empty else None
        proxy_funds.append({"name": name, "value": value, "direction": "净流入" if value is not None and value > 0 else "净流出" if value is not None and value < 0 else "暂无数据", "coverage": int(len(sample)), "basis": basis, "confidence": "中" if len(sample) >= 30 else "低"})
    top_in = sorted(sectors, key=lambda x: float(x.get("net_mf_yi") or 0), reverse=True)[:3]
    top_out = sorted(sectors, key=lambda x: float(x.get("net_mf_yi") or 0))[:3]
    proxy_links = [{"source": p["name"], "target": s.get("industry"), "value": abs(float(p["value"] or 0)) / max(len(top_in), 1)} for p in proxy_funds for s in top_in if (p["value"] or 0) > 0]
    rotation_paths = [{"from": s.get("industry"), "to": t.get("industry"), "value": round(min(abs(float(s.get("net_mf_yi") or 0)), abs(float(t.get("net_mf_yi") or 0))), 2), "confidence": "中"} for s, t in zip(top_out, top_in)]
    strongest = max(sectors, key=lambda x: float(x.get("strength") or 0), default={})
    rotation_candidates = [x for x in sectors if x.get("state") == "潜在轮入"]
    trade_sector = max(rotation_candidates or sectors, key=lambda x: float(x.get("breadth") or 0) + max(float(x.get("net_mf_yi") or 0), 0) / 100, default={})
    if mean_ret > 0 and (positive_flow or 0) < 50:
        main_conflict = "指数和个股表现改善，但资金广度不足，仍是存量轮动而非全面增量"
    elif (breadth or 0) < 40 and (positive_flow or 0) < 45:
        main_conflict = "上涨家数与资金承接同步偏弱，主要矛盾是风险偏好收缩"
    else:
        main_conflict = "上涨宽度、资金流和涨停结构共同决定下一步是延续还是分歧"
    lead_in = top_in[0].get("industry") if top_in else None
    lead_out = top_out[0].get("industry") if top_out else None
    conclusion = f"市场处于{market_state}：{lead_out or '暂无明确流出方向'}资金流出，{lead_in or '暂无明确承接方向'}承接；指数与个股的同步性仍需下一交易日确认。"
    reason_blocks = {
        "primary": f"主因：上涨宽度 {breadth:.1f}%、平均涨跌 {mean_ret:.2f}% 与资金广度 {positive_flow:.1f}% 共同指向{market_state}。" if breadth is not None and positive_flow is not None else "主因：关键市场宽度或资金数据缺失，暂不能确认。",
        "secondary": f"次因：资金最强方向为 {lead_in or '—'}，相对流出方向为 {lead_out or '—'}。",
        "buffer": f"缓冲因素：涨停 {limit_up:.0f} 家、炸板 {broken:.0f} 家，局部风险偏好仍有支撑。",
        "reverse": "反向因素：若资金广度继续下降、强势板块由流入转流出，当前判断失效。",
    }
    news_top = sorted(news, key=lambda x: float(x.get("value_score") or 0), reverse=True)[:3]
    news_briefs = [{"title": x.get("title"), "time": x.get("time"), "industry": x.get("industry"), "name": x.get("name"), "direction": "偏利好" if float(x.get("direction_score") or 0) > 10 else "偏利空" if float(x.get("direction_score") or 0) < -10 else "中性", "value_score": x.get("value_score"), "trust_score": x.get("trust_score"), "reason": x.get("reasons")} for x in news_top]
    sector_map = {x.get("industry"): x for x in sectors}
    flow_frame = pd.DataFrame(flows)
    market_flow_series = []
    rotation_timeline = []
    if not flow_frame.empty:
        flow_frame["net_mf_yi"] = pd.to_numeric(flow_frame["net_mf_yi"], errors="coerce")
        for date, group in flow_frame.groupby(flow_frame["trade_date"].astype(str), sort=True):
            group = group.dropna(subset=["net_mf_yi"])
            if group.empty:
                continue
            total = float(group["net_mf_yi"].sum())
            top_day_in = group.sort_values("net_mf_yi", ascending=False).iloc[0]
            top_day_out = group.sort_values("net_mf_yi", ascending=True).iloc[0]
            lead = sector_map.get(top_day_in["industry"], {})
            lead_ret = float(lead.get("week_ret") or 0)
            lead_flow = float(top_day_in["net_mf_yi"])
            stage = "启动" if lead_flow > 0 and lead_ret <= 2 else "加速" if lead_flow > 0 and lead_ret > 2 else "分歧" if lead_flow < 0 and lead_ret > 0 else "退潮" if lead_flow < 0 and lead_ret < 0 else "观察"
            market_flow_series.append({"trade_date": date, "net_mf_yi": round(total, 2)})
            rotation_timeline.append({"trade_date": date, "stage": stage, "inflow_sector": top_day_in["industry"], "inflow_yi": round(lead_flow, 2), "outflow_sector": top_day_out["industry"], "outflow_yi": round(float(top_day_out["net_mf_yi"]), 2), "confidence": "中" if len(group) >= 20 else "低"})
    summary = {
        "stock_count": len(stocks),
        "sector_count": len(sectors),
        "mean_ret": mean_ret if not numeric_stocks.empty else None,
        "breadth": float((numeric_stocks > 0).mean() * 100) if not numeric_stocks.empty else None,
        "total_flow": float(pd.to_numeric(pd.Series([x.get("net_mf_5d_yi") for x in stocks]), errors="coerce").sum(min_count=1)),
        "price_dates": sorted({str(x["trade_date"]) for x in prices}),
        "generated_at": generated_at,
        "source": "TinyShare授权接口 + 本地消息快照",
        "freshness": "按最近成功抓取批次生成；非实时",
        "estimated": True,
        "sentiment_score": sentiment,
        "money_effect_score": money_effect,
        "valuation_median_pe": median_pe,
        "valuation_percentile": pe_percentile,
        "valuation_coverage": len(pe),
        "etf_count": len(etfs),
        "etf_window": sorted({str(x.get("trade_date")) for x in etfs if x.get("trade_date")}),
        "market_state": market_state,
        "conclusion": conclusion,
        "strategy": strategy,
        "avoid_strategy": "不追逐高位无资金承接的涨幅，不把估算身份当作真实账户归属",
        "main_conflict": main_conflict,
        "primary_reason": "上涨宽度、五日主力资金、涨停/炸板结构与板块相对强弱的规则合成",
        "reason_blocks": reason_blocks,
        "strongest_sector": strongest.get("industry"),
        "strongest_sector_score": strongest.get("strength"),
        "strongest_sector_reason": "涨幅、资金、上涨家数和持续性综合得分最高",
        "trade_sector": trade_sector.get("industry"),
        "trade_sector_reason": "优先观察有资金承接且上涨宽度较好的方向，仍需下一交易日验证",
        "position": round(min(80, max(20, money_effect * 0.7)), 0),
        "confidence": "中",
        "validation": f"验证点：观察 {lead_in or '最强承接方向'} 次日是否继续净流入，并确认龙头、中军与板块同步。",
        "invalidation": "失效条件：资金广度转负、最强板块跌破前一交易日低点，或利好方向出现放量冲高回落。",
        "news_briefs": news_briefs,
        "market_flow_series": market_flow_series,
        "rotation_timeline": rotation_timeline,
        "flow_periods": [{"period": "5分钟", "available": False, "reason": "当前授权接口未提供分时资金明细"}, {"period": "15分钟", "available": False, "reason": "当前授权接口未提供分时资金明细"}, {"period": "30分钟", "available": False, "reason": "当前授权接口未提供分时资金明细"}, {"period": "当日", "available": bool(market_flow_series), "reason": "按板块日级主力净流合计"}, {"period": "3日", "available": len(market_flow_series) >= 3, "reason": "按最近可用交易日合计"}, {"period": "5日", "available": len(market_flow_series) >= 5, "reason": "按最近可用交易日合计"}, {"period": "20日", "available": False, "reason": "当前快照不足20个交易日资金明细"}],
        "proxy_funds": proxy_funds,
        "proxy_links": proxy_links,
        "rotation_paths": rotation_paths,
    }
    template = (ROOT / "market_dashboard_template.html").read_text(encoding="utf-8")
    replacements = {
        "__SECTORS__": json.dumps(sectors, ensure_ascii=False, separators=(",", ":")),
        "__STOCKS__": json.dumps(stocks, ensure_ascii=False, separators=(",", ":")),
        "__FLOWS__": json.dumps(flows, ensure_ascii=False, separators=(",", ":")),
        "__PRICES__": json.dumps(prices, ensure_ascii=False, separators=(",", ":")),
        "__SUMMARY__": json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        "__NEWS__": json.dumps(news, ensure_ascii=False, separators=(",", ":")),
        "__ETFS__": json.dumps(etfs, ensure_ascii=False, separators=(",", ":")),
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
