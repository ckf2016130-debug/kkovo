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
        # Keep ETFs with actual market reference value; debt, currency and tiny inactive samples are noise here.
        name_text = out.get("name", pd.Series("", index=out.index)).fillna("").astype(str)
        excluded = name_text.str.contains("债|货币|同业存单|短融|国债|政金|美元|日元", regex=True)
        liquid = out[~excluded & (out["amount_yi"] >= 1)].copy()
        if liquid.empty:
            liquid = out[~excluded].copy()
        out = liquid
        out = out.sort_values("amount_yi", ascending=False)
        nav_rows = []
        for nav_path in sorted((ROOT / "data").glob("fund_nav_*.csv")):
            try:
                nav = pd.read_csv(nav_path)
                if {"ts_code", "unit_nav"}.issubset(nav.columns):
                    nav["unit_nav"] = pd.to_numeric(nav["unit_nav"], errors="coerce")
                    if "nav_date" not in nav:
                        nav["nav_date"] = nav_path.stem.split("_")[-1]
                    nav["nav_date"] = nav["nav_date"].astype(str)
                    nav_rows.append(nav[[c for c in ["ts_code", "nav_date", "unit_nav", "accum_nav", "net_asset"] if c in nav.columns]])
            except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
                continue
        if nav_rows:
            nav = pd.concat(nav_rows, ignore_index=True).sort_values(["ts_code", "nav_date"]).drop_duplicates("ts_code", keep="last")
            out = out.merge(nav, on="ts_code", how="left")
            out["premium_discount"] = (out["close"] / out["unit_nav"] - 1) * 100
        component_rows = []
        for component_path in sorted((ROOT / "data").glob("etf_*_cons_*.csv")):
            try:
                component = pd.read_csv(component_path)
                if {"ts_code", "con_code"}.issubset(component.columns):
                    component["cpr"] = pd.to_numeric(component.get("cpr"), errors="coerce")
                    component_rows.append(component[[c for c in ["ts_code", "con_code", "cpr"] if c in component]])
            except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
                continue
        if component_rows:
            component = pd.concat(component_rows, ignore_index=True)
            component_summary = component.groupby("ts_code", as_index=False).agg(component_count=("con_code", "nunique"), cpr_mean=("cpr", "mean"))
            out = out.merge(component_summary, on="ts_code", how="left")
        def classify_etf(row):
            text = f"{row.get('name') or ''} {row.get('benchmark') or ''} {row.get('invest_type') or ''}"
            if any(k in text for k in ["纳斯达克", "标普", "日经", "恒生", "台湾", "韩国", "海外", "QDII"]):
                return "海外联动"
            if any(k in text for k in ["沪深300", "中证500", "中证1000", "中证A500", "上证50", "科创50", "创业板", "国证2000"]):
                return "宽基工具"
            if any(k in text for k in ["半导体", "芯片", "人工智能", "计算机", "通信", "医药", "新能源", "军工", "证券", "银行", "红利", "消费"]):
                return "行业/风格工具"
            return "主题待确认"
        out["tool_role"] = out.apply(classify_etf, axis=1)
        out["tool_relevance_score"] = (pd.to_numeric(out["amount_yi"], errors="coerce").clip(lower=0, upper=20) / 20 * 50 + out["benchmark"].fillna("").astype(str).str.len().clip(upper=20) / 20 * 20).round(1)
        out["selection_reason"] = out.apply(lambda r: f"{r['tool_role']}；成交额 {float(r['amount_yi']):.2f}亿" if pd.notna(r.get("amount_yi")) else f"{r['tool_role']}；成交额缺失", axis=1)
        return records(out[[c for c in ["ts_code", "name", "fund_type", "benchmark", "invest_type", "issue_amount", "trade_date", "close", "week_ret", "amount_yi", "nav_date", "unit_nav", "accum_nav", "net_asset", "premium_discount", "component_count", "cpr_mean", "tool_role", "tool_relevance_score", "selection_reason"] if c in out]])
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, KeyError):
        return []



def load_overseas():
    path = ROOT / "data" / "overseas_daily.csv"
    if not path.exists():
        return []
    try:
        frame = pd.read_csv(path)
        frame["trade_date"] = frame["trade_date"].astype(str)
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(subset=["asset", "trade_date", "close"]).sort_values(["asset", "trade_date"])
        rows = []
        for (asset, group), part in frame.groupby(["asset", "group"], sort=False):
            part = part.tail(60).copy()
            close = part["close"]
            row = {"asset": asset, "group": group, "trade_date": part["trade_date"].iloc[-1], "close": float(close.iloc[-1])}
            for days, key in [(5, "ret_5d"), (20, "ret_20d"), (60, "ret_60d")]:
                row[key] = float((close.iloc[-1] / close.iloc[-min(days, len(close))] - 1) * 100) if len(close) > 1 else None
            rows.append(row)
        return rows
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
    # The weekly source can contain one row per joined snapshot. Keep one canonical row per security.
    stocks = stocks.sort_values(["ts_code", "trade_date"] if "trade_date" in stocks.columns else ["ts_code"]).drop_duplicates("ts_code", keep="last")
    for c in ["ann_date", "eps", "bps", "fcfe_ps", "roe", "grossprofit_margin", "netprofit_margin", "debt_to_assets", "current_ratio", "q_ocf_to_sales", "q_sales_yoy", "netprofit_yoy", "ocf_yoy"]:
        if c not in stocks:
            # Missing fundamentals stay missing. Zero would be interpreted as a real
            # observation and would distort peer ranks and valuation assumptions.
            stocks[c] = pd.NA
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
    if etfs:
        # ETF holdings make the table useful for selection: concentration and industry exposure
        # are derived only when the component snapshot contains real weights.
        component_rows = []
        for component_path in sorted((ROOT / "data").glob("etf_*_cons_*.csv")):
            try:
                component = pd.read_csv(component_path)
                if {"ts_code", "con_code"}.issubset(component.columns):
                    keep = [c for c in ["ts_code", "con_code", "name", "weight"] if c in component.columns]
                    component_rows.append(component[keep].copy())
            except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
                continue
        if component_rows:
            component = pd.concat(component_rows, ignore_index=True)
            component["weight"] = pd.to_numeric(component.get("weight"), errors="coerce")
            stock_lookup = stocks[[c for c in ["ts_code", "name", "industry"] if c in stocks.columns]].drop_duplicates("ts_code")
            component = component.merge(stock_lookup, left_on="con_code", right_on="ts_code", how="left", suffixes=("", "_stock"))
            exposure = []
            for code, group in component.groupby("ts_code", sort=False):
                weights = group["weight"].dropna()
                total = float(weights[weights > 0].sum()) if not weights.empty else None
                ranked = group.sort_values("weight", ascending=False).dropna(subset=["weight"])
                top10 = float(ranked.head(10)["weight"].sum()) if not ranked.empty else None
                industry_exposure = []
                if total and total > 0:
                    by_industry = ranked[ranked["industry"].notna()].groupby("industry")["weight"].sum().sort_values(ascending=False).head(5)
                    industry_exposure = [{"industry": str(k), "weight": round(float(v / total * 100), 2)} for k, v in by_industry.items()]
                top_holdings = [{"name": str(row.get("name") or row.get("name_stock") or row.get("con_code")), "code": str(row.get("con_code")), "weight": round(float(row["weight"]), 2)} for _, row in ranked.head(10).iterrows()]
                exposure.append({"ts_code": code, "component_count": int(group["con_code"].nunique()), "top10_weight": round(top10, 2) if top10 is not None else None, "weight_coverage": round(total, 2) if total is not None else None, "industry_exposure": industry_exposure, "top_holdings": top_holdings})
            etf_frame = pd.DataFrame(etfs).merge(pd.DataFrame(exposure), on="ts_code", how="left", suffixes=("", "_derived"))
            for column in ["component_count", "top10_weight", "weight_coverage", "industry_exposure", "top_holdings"]:
                derived = f"{column}_derived"
                if derived in etf_frame:
                    etf_frame[column] = etf_frame[derived].where(etf_frame[derived].notna(), etf_frame.get(column))
            etfs = records(etf_frame.drop(columns=[c for c in etf_frame.columns if c.endswith("_derived")], errors="ignore"))
    overseas = load_overseas()
    if etfs and overseas:
        overseas_terms = {"纳斯达克": "纳斯达克", "NASDAQ": "纳斯达克", "标普": "标普500", "S&P": "标普500", "日经": "日经225", "台湾": "中国台湾加权", "韩国": "韩国综合", "半导体": "费城半导体"}
        overseas_map = {x.get("asset"): x for x in overseas}
        for etf in etfs:
            text = f"{etf.get('name') or ''} {etf.get('benchmark') or ''}".upper()
            matched = next((asset for term, asset in overseas_terms.items() if term.upper() in text), None)
            linked = overseas_map.get(matched) if matched else None
            etf["overseas_asset"] = matched
            etf["overseas_ret_5d"] = linked.get("ret_5d") if linked else None
            etf["overseas_ret_20d"] = linked.get("ret_20d") if linked else None
            etf["overseas_link_note"] = "仅作同类资产联动观察，不代表因果" if linked else None
    stock_flows = []
    for path in sorted((ROOT / "data").glob("moneyflow_*.csv")):
        try:
            frame = pd.read_csv(path)
            if {"ts_code", "trade_date", "net_mf_amount"}.issubset(frame.columns):
                frame = frame[["ts_code", "trade_date", "net_mf_amount"]].copy()
                frame["net_mf_yi"] = pd.to_numeric(frame["net_mf_amount"], errors="coerce") / 100000
                stock_flows.append(frame[["ts_code", "trade_date", "net_mf_yi"]])
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
            continue
    stock_flows = pd.concat(stock_flows, ignore_index=True) if stock_flows else pd.DataFrame(columns=["ts_code", "trade_date", "net_mf_yi"])

    sector_cols = ["rank", "industry", "state", "constituents", "week_ret", "median_ret", "net_mf_yi", "fri_flow_yi", "breadth", "turnover_yi", "flow_ratio", "limit_up", "broken", "limit_down", "strength"]
    stock_cols = ["industry", "name", "ts_code", "market", "week_ret", "fri_ret", "net_mf_5d_yi", "net_mf_fri_yi", "circ_mv_yi", "turnover_yi", "turnover_rate", "pe", "pb", "U", "Z", "D", "leader_score", "core_score", "elastic_score", "ann_date", "eps", "bps", "fcfe_ps", "roe", "grossprofit_margin", "netprofit_margin", "debt_to_assets", "current_ratio", "q_ocf_to_sales", "q_sales_yoy", "netprofit_yoy", "ocf_yoy", "quality_score", "growth_score", "cash_score", "leverage_score", "valuation_score", "fundamental_coverage", "fundamental_score"]
    return (
        records(sectors[sector_cols]),
        records(stocks[stock_cols]),
        records(sector_flow[["industry", "trade_date", "net_mf_yi"]]),
        records(price) if not price.empty else [],
        etfs,
        overseas,
        records(stock_flows),
    )


def build():
    sectors, stocks, flows, prices, etfs, overseas, stock_flows = load_data()
    index_rows = []
    for path in sorted((ROOT / "data").glob("index_daily_*.csv")):
        try:
            frame = pd.read_csv(path)
            if {"trade_date", "close"}.issubset(frame.columns):
                if "ts_code" not in frame:
                    frame["ts_code"] = path.stem.replace("index_daily_", "").replace("_", ".")
                index_rows.append(frame[[c for c in ["ts_code", "trade_date", "close", "pct_chg", "amount"] if c in frame]])
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
            continue
    index_frame = pd.concat(index_rows, ignore_index=True) if index_rows else pd.DataFrame()
    news_path = ROOT / "data" / "news" / "news_scored.csv"
    news = records(pd.read_csv(news_path)) if news_path.exists() else []
    numeric_stocks = pd.to_numeric(pd.Series([x.get("week_ret") for x in stocks]), errors="coerce").dropna()
    generated_at = pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds")
    history_path = ROOT / "data" / "decision_history.json"
    try:
        decision_history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
        decision_history = decision_history if isinstance(decision_history, list) else []
    except (OSError, json.JSONDecodeError):
        decision_history = []
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
    market_amount_ratio = None
    if prices:
        price_frame = pd.DataFrame(prices)
        price_frame["amount"] = pd.to_numeric(price_frame.get("amount"), errors="coerce")
        daily_amount = price_frame.groupby(price_frame["trade_date"].astype(str))["amount"].sum().sort_index().dropna()
        if len(daily_amount) >= 3 and daily_amount.iloc[:-1].median() > 0:
            market_amount_ratio = float(daily_amount.iloc[-1] / daily_amount.iloc[:-1].median())
    index_week_ret = None
    index_day_ret = None
    index_evidence = []
    if not index_frame.empty:
        index_frame["close"] = pd.to_numeric(index_frame["close"], errors="coerce")
        index_frame["pct_chg"] = pd.to_numeric(index_frame.get("pct_chg"), errors="coerce")
        for code, group in index_frame.dropna(subset=["close"]).groupby("ts_code"):
            group = group.sort_values("trade_date")
            if len(group) >= 2:
                index_week_ret = float((group["close"].iloc[-1] / group["close"].iloc[max(0, len(group)-5)] - 1) * 100) if index_week_ret is None else index_week_ret
                index_evidence.append(f"{code}近5日 {index_week_ret:.2f}%")
                if index_day_ret is None and pd.notna(group["pct_chg"].iloc[-1]):
                    index_day_ret = float(group["pct_chg"].iloc[-1])
    defensive_terms = ("银行", "保险", "公用事业", "煤炭", "石油", "医药", "食品饮料")
    growth_terms = ("半导体", "电子", "计算机", "通信", "军工", "新能源", "软件")
    def style_flow(terms):
        rows = [x for x in sectors if any(term in str(x.get("industry") or "") for term in terms)]
        return sum(float(x.get("net_mf_yi") or 0) for x in rows), sum(float(x.get("week_ret") or 0) for x in rows), len(rows)
    defensive_flow, defensive_ret, defensive_count = style_flow(defensive_terms)
    growth_flow, growth_ret, growth_count = style_flow(growth_terms)
    state_evidence = []
    if index_week_ret is not None:
        state_evidence.append(f"指数近5日代表值 {index_week_ret:.2f}%")
    if market_amount_ratio is not None:
        state_evidence.append(f"最新市场成交额/前期中位 {market_amount_ratio:.2f}倍")
    if index_week_ret is not None and breadth is not None and index_week_ret - mean_ret >= 1.0 and breadth < 50:
        market_state, strategy = "指数强个股弱", "指数权重不代表个股机会，降低追涨，等待个股宽度和资金同步改善"
        state_evidence.append("指数涨幅明显高于个股等权涨幅且上涨宽度低于50%")
    elif index_week_ret is not None and breadth is not None and mean_ret - index_week_ret >= 1.0 and breadth >= 50:
        market_state, strategy = "指数弱个股强", "只做有宽度和资金承接的局部方向，避免把指数弱误读为全面风险"
        state_evidence.append("个股等权涨幅明显高于代表指数且上涨宽度不弱")
    elif defensive_count and defensive_flow > max(growth_flow, 0) * 1.25 and defensive_ret >= growth_ret:
        market_state, strategy = "防御占优", "优先观察低波动和现金流方向，成长板块等待资金重新确认"
        state_evidence.append(f"防御候选板块资金 {defensive_flow:.2f}亿，高于成长候选 {growth_flow:.2f}亿")
    elif growth_count and growth_flow > max(defensive_flow, 0) * 1.25 and growth_ret >= defensive_ret:
        market_state, strategy = "成长占优", "只跟踪有业绩或订单验证的成长方向，警惕高估值拥挤"
        state_evidence.append(f"成长候选板块资金 {growth_flow:.2f}亿，高于防御候选 {defensive_flow:.2f}亿")
    elif breadth is not None and breadth <= 20 and mean_ret < -2 and (total_stock_flow or 0) < 0:
        market_state, strategy = "恐慌释放", "降低仓位，等待跌停/炸板结构和资金广度止跌后再观察修复"
        state_evidence.append("上涨宽度极低、平均跌幅超过2%且资金流出")
    elif breadth is not None and breadth >= 65 and mean_ret > 0 and (positive_flow or 0) >= 55 and (total_stock_flow or 0) > 0:
        market_state, strategy = "增量上涨", "顺势跟随主线，但只做有资金和龙头验证的方向"
    elif breadth is not None and breadth >= 55 and mean_ret > 0 and (total_stock_flow or 0) <= 0:
        market_state, strategy = "缩量上涨", "控制追高，优先观察低位承接和回流确认"
    elif breadth is not None and breadth <= 35 and mean_ret < 0 and (total_stock_flow or 0) < 0:
        if market_amount_ratio is not None and market_amount_ratio >= 1.15:
            market_state, strategy = "放量下跌", "降低仓位，等待风险释放后再看修复"
        elif market_amount_ratio is not None and market_amount_ratio < 0.85:
            market_state, strategy = "缩量下跌", "不急于抄底，等待成交额和上涨宽度同时止跌"
        else:
            market_state, strategy = "情绪退潮", "以防守和等待为主，不接力弱势反弹"
    elif breadth is not None and breadth <= 35 and mean_ret < 0:
        market_state, strategy = "情绪退潮", "以防守和等待为主，不接力弱势反弹"
    elif mean_ret > 0 and (positive_flow or 0) < 50:
        market_state, strategy = "存量轮动", "低吸有资金承接的板块，避免追逐已经加速的方向"
    elif breadth is not None and breadth >= 65 and mean_ret > 0:
        market_state, strategy = "普涨修复", "可跟踪修复主线，但需要成交额和资金继续确认"
    else:
        market_state, strategy = "高位分歧", "控制总仓位，等待强弱方向和资金流向重新收敛"
    if not state_evidence:
        state_evidence.append("暂无足够指数或成交额基准，状态仅由个股宽度、资金和涨跌结构合成")
    stocks_frame = pd.DataFrame(stocks)
    for col in ["circ_mv_yi", "turnover_rate", "net_mf_5d_yi", "U", "Z", "week_ret"]:
        stocks_frame[col] = pd.to_numeric(stocks_frame.get(col), errors="coerce")
    cap_mid = stocks_frame["circ_mv_yi"].median()
    turn_mid = stocks_frame["turnover_rate"].median()
    proxy_specs = [
        ("国家队代理", stocks_frame["circ_mv_yi"] >= cap_mid, "大市值与权重股的资金方向"),
        ("机构代理", (stocks_frame["circ_mv_yi"] >= cap_mid) & (stocks_frame["turnover_rate"] <= turn_mid), "大市值、低换手、持续交易方向"),
        ("游资代理", (stocks_frame["U"].fillna(0) + stocks_frame["Z"].fillna(0) > 0) & (stocks_frame["turnover_rate"] >= turn_mid), "涨停/炸板与高换手行为"),
        ("散户代理", (stocks_frame["circ_mv_yi"] < cap_mid) & (stocks_frame["turnover_rate"] >= turn_mid), "小市值与高换手行为"),
    ]
    proxy_funds = []
    for name, mask, basis in proxy_specs:
        sample = stocks_frame.loc[mask]
        value = float(sample["net_mf_5d_yi"].sum()) if not sample.empty else None
        proxy_funds.append({"name": name, "value": value, "direction": "净流入" if value is not None and value > 0 else "净流出" if value is not None and value < 0 else "暂无数据", "coverage": int(len(sample)), "basis": basis, "confidence": "中" if len(sample) >= 30 else "低"})
    for sector in sectors:
        sector_rows = stocks_frame[stocks_frame["industry"] == sector.get("industry")]
        sector["agent_flows"] = []
        for name, mask, basis in proxy_specs:
            sample = stocks_frame.loc[mask & stocks_frame["industry"].eq(sector.get("industry"))]
            value = float(sample["net_mf_5d_yi"].sum()) if not sample.empty else None
            sector["agent_flows"].append({"name": name, "value": value, "direction": "净流入" if value is not None and value > 0 else "净流出" if value is not None and value < 0 else "暂无数据", "coverage": int(len(sample)), "basis": basis})
    agent_series = []
    sector_agent_series = {}
    stock_class = stocks_frame[["ts_code", "circ_mv_yi", "turnover_rate", "U", "Z"]].copy()
    cap_mid = stocks_frame["circ_mv_yi"].median()
    turn_mid = stocks_frame["turnover_rate"].median()
    for path in sorted((ROOT / "data").glob("moneyflow_*.csv")):
        try:
            mf = pd.read_csv(path)
            mf["net_mf_amount"] = pd.to_numeric(mf["net_mf_amount"], errors="coerce")
            merged = mf.merge(stock_class, on="ts_code", how="inner").dropna(subset=["net_mf_amount"])
            net = merged["net_mf_amount"]
            masks = {
                "国家队代理": merged["circ_mv_yi"] >= cap_mid,
                "机构代理": (merged["circ_mv_yi"] >= cap_mid) & (merged["turnover_rate"] <= turn_mid),
                "游资代理": (merged["U"].fillna(0) + merged["Z"].fillna(0) > 0) & (merged["turnover_rate"] >= turn_mid),
                "散户代理": (merged["circ_mv_yi"] < cap_mid) & (merged["turnover_rate"] >= turn_mid),
            }
            date = str(mf["trade_date"].iloc[0]) if not mf.empty else path.stem.split("_")[-1]
            for name, mask in masks.items():
                value = float(net.loc[mask].sum()) / 100000 if mask.any() else None
                agent_series.append({"trade_date": date, "name": name, "value": round(value, 2) if value is not None else None})
                if mask.any():
                    grouped = merged.loc[mask].groupby("industry")["net_mf_amount"].sum() / 100000
                    for industry, sector_value in grouped.items():
                        sector_agent_series.setdefault(str(industry), []).append({"trade_date": date, "name": name, "value": round(float(sector_value), 2)})
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, KeyError):
            continue
    for sector in sectors:
        sector["agent_series"] = sector_agent_series.get(str(sector.get("industry")), [])
    flow_values = pd.to_numeric(pd.Series([x.get("net_mf_yi") for x in sectors]), errors="coerce")
    flow_rank = flow_values.rank(pct=True).fillna(0.5) * 100
    for sector, rank in zip(sectors, flow_rank.tolist()):
        breadth_score = float(sector.get("breadth") or 0)
        ret = float(sector.get("week_ret") or 0)
        crowding_penalty = min(35, max(0, ret) * 4)
        sector["trade_value_score"] = round(max(0, min(100, breadth_score * 0.45 + rank * 0.35 + (100 - crowding_penalty) * 0.20)), 1)
        sector["trade_value_reason"] = "资金与上涨宽度同步，且短期涨幅未明显拥挤" if crowding_penalty < 20 else "资金仍在，但短期涨幅较高，追高风险上升"
    top_in = sorted(sectors, key=lambda x: float(x.get("net_mf_yi") or 0), reverse=True)[:3]
    top_out = sorted(sectors, key=lambda x: float(x.get("net_mf_yi") or 0))[:3]
    proxy_links = []
    for sector in sorted(sectors, key=lambda x: float(x.get("net_mf_yi") or 0), reverse=True)[:12]:
        for agent in sector.get("agent_flows", []):
            value = float(agent.get("value") or 0)
            if value > 0:
                proxy_links.append({"source": agent["name"], "target": sector.get("industry"), "value": round(value, 2)})
    proxy_links = sorted(proxy_links, key=lambda x: x["value"], reverse=True)[:16]
    rotation_paths = [{"from": s.get("industry"), "to": t.get("industry"), "value": round(min(abs(float(s.get("net_mf_yi") or 0)), abs(float(t.get("net_mf_yi") or 0))), 2), "confidence": "中"} for s, t in zip(top_out, top_in)]
    strongest = max(sectors, key=lambda x: float(x.get("strength") or 0), default={})
    rotation_candidates = [x for x in sectors if x.get("state") == "潜在轮入"]
    trade_sector = max(rotation_candidates or sectors, key=lambda x: float(x.get("trade_value_score") or 0), default={})
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
    sector_map = {x.get("industry"): x for x in sectors}
    news_top = sorted(news, key=lambda x: float(x.get("value_score") or 0), reverse=True)[:3]
    news_briefs = []
    for item in news_top:
        direction_score = float(item.get("direction_score") or 0)
        industry = item.get("industry") or "未映射"
        sector = sector_map.get(industry, {})
        sector_ret = float(sector.get("week_ret") or 0) if sector else None
        sector_flow = float(sector.get("net_mf_yi") or 0) if sector else None
        direction = "偏利好" if direction_score > 10 else "偏利空" if direction_score < -10 else "中性"
        direct = bool(item.get("ts_code") or item.get("name"))
        if sector and ((direction == "偏利好" and sector_ret > 0 and sector_flow > 0) or (direction == "偏利空" and sector_ret < 0 and sector_flow < 0)):
            acceptance = "价格与资金初步认可"
        elif sector and direction != "中性" and (sector_ret < 0 or sector_flow < 0):
            acceptance = "利好未被充分认可或可能已提前消化"
        elif sector:
            acceptance = "相关但尚不能确认市场认可"
        else:
            acceptance = "缺少对应板块价格与资金证据"
        affected_stock = item.get("name") or None
        if not affected_stock and industry in stocks_frame.get("industry", pd.Series(dtype=str)).values:
            candidates = stocks_frame[stocks_frame["industry"] == industry].sort_values("leader_score", ascending=False)
            affected_stock = candidates.iloc[0].get("name") if not candidates.empty else None
        etf_candidates = [x for x in etfs if industry and (industry in str(x.get("name") or "") or industry in str(x.get("benchmark") or ""))]
        if not etf_candidates and sector:
            etf_candidates = sorted(etfs, key=lambda x: float(x.get("amount_yi") or 0), reverse=True)[:1]
        affected_etf = (etf_candidates[0].get("name") or etf_candidates[0].get("ts_code")) if etf_candidates else None
        scope = "个股+板块+ETF" if direct and affected_etf else "板块+ETF观察" if affected_etf else "个股+所属板块" if direct else "板块观察"
        impact_strength = "高" if float(item.get("value_score") or 0) >= 75 else "中" if float(item.get("value_score") or 0) >= 50 else "低"
        impact = {"impact_type": "直接影响" if direct else "间接映射", "impact_scope": scope, "impact_strength": impact_strength, "duration": "持续性待验证（当前快照不推断时间长度）", "pre_traded": "无法仅凭当前快照确认是否提前交易", "consumption": "当前快照无法确认，需结合消息前后价格、成交额和资金" if sector else "暂无可验证价格窗口", "market_acceptance": acceptance, "sector_ret": sector_ret, "sector_flow": sector_flow, "validation": f"验证：{industry}次日资金、龙头/中军与 ETF 是否同步。" if sector else "验证：先补充可映射的板块或标的。", "affected_stock": affected_stock, "affected_etf": affected_etf}
        impact["impact_chain"] = [{"stage": "消息", "value": item.get("title"), "evidence": f"{item.get('time') or '时间未知'} · 来源 {item.get('source') or '未知'}"}, {"stage": "板块", "value": industry, "evidence": f"影响类型：{impact['impact_type']} · 强度：{impact_strength}"}, {"stage": "资金", "value": industry if sector else "暂无映射", "evidence": f"5日主力净流 {sector_flow:.2f}亿" if sector_flow is not None else "暂无真实资金证据"}, {"stage": "价格", "value": f"{sector_ret:.2f}%" if sector_ret is not None else "暂无价格证据", "evidence": "当前窗口表现，不等于消息因果"}, {"stage": "标的", "value": affected_stock or affected_etf or "暂无映射", "evidence": "个股/ETF仅作观察对象，不代表交易建议"}, {"stage": "验证", "value": "待下一交易日确认", "evidence": impact["validation"]}]
        item.update(impact)
        news_briefs.append({"title": item.get("title"), "url": item.get("url"), "source": item.get("source"), "time": item.get("time"), "industry": industry, "name": affected_stock, "direction": direction, "value_score": item.get("value_score"), "trust_score": item.get("trust_score"), "score_breakdown": item.get("score_breakdown"), "score_formula": item.get("score_formula"), "reason": item.get("reasons"), **impact})
    chain_head = news_briefs[0] if news_briefs else None
    overseas_lead = next((x for x in overseas if x.get("targets")), None)
    logic_chain = [{"label": "国内消息", "value": chain_head.get("title") if chain_head else "暂无高价值消息", "evidence": f"时间 {chain_head.get('time')} · 价值 {chain_head.get('value_score')} · 可信度 {chain_head.get('trust_score')}" if chain_head else "暂无真实消息", "action": "news", "url": chain_head.get("url") if chain_head else None}]
    if overseas_lead:
        logic_chain.append({"label": "海外变量", "value": overseas_lead.get("asset"), "evidence": f"5日 {overseas_lead.get('ret_5d')}% · 映射 { '、'.join(overseas_lead.get('targets') or []) } · {overseas_lead.get('state')}", "action": "overseas"})
    logic_chain.extend([{"label": "影响对象", "value": chain_head.get("industry") if chain_head else "未映射", "evidence": f"{chain_head.get('impact_type')} · {chain_head.get('impact_scope')}" if chain_head else "暂无证据", "action": "sector", "target": chain_head.get("industry") if chain_head else None}, {"label": "资金验证", "value": lead_in or "暂无承接方向", "evidence": f"板块5日净流 {float(sector_map.get(lead_in, {}).get('net_mf_yi') or 0):.2f}亿" if lead_in else "暂无数据", "action": "sector", "target": lead_in}, {"label": "价格验证", "value": market_state, "evidence": f"个股等权 {mean_ret:.2f}% · 上涨宽度 {breadth:.1f}%" if breadth is not None else "暂无数据", "action": "overview"}, {"label": "判断", "value": "相关但尚不能确认主要因果" if not chain_head or chain_head.get("market_acceptance") != "价格与资金初步认可" else "价格与资金初步认可", "evidence": "时间相关性不等于因果，需下一交易日复核", "action": "overview"}])
    overseas_conduction = []
    price_frame = pd.DataFrame(prices)
    if not price_frame.empty and "pct_chg" in price_frame:
        price_frame["pct_chg"] = pd.to_numeric(price_frame["pct_chg"], errors="coerce")
        a_market = price_frame.groupby(price_frame["trade_date"].astype(str))["pct_chg"].mean().dropna()
    else:
        a_market = pd.Series(dtype=float)
    sector_keywords = {
        "费城半导体": ["半导体", "电子设备", "元件"],
        "中国台湾加权": ["半导体", "电子设备", "元件"],
        "韩国综合": ["半导体", "电子设备", "元件"],
        "日经225": ["汽车", "家用电器", "电子设备"],
        "纳斯达克": ["软件服务", "互联网", "半导体", "电子设备"],
        "标普500": [],
        "道琼斯": [],
    }
    for item in overseas:
        asset = item.get("asset")
        targets = [s for s in sectors if any(k in str(s.get("industry")) for k in sector_keywords.get(asset, []))]
        target_ret = float(pd.to_numeric(pd.Series([s.get("week_ret") for s in targets]), errors="coerce").mean()) if targets else None
        same_corr = lead_corr = None
        try:
            hist = pd.read_csv(ROOT / "data" / "overseas_daily.csv")
            hist = hist[hist["asset"] == asset].copy()
            hist["trade_date"] = hist["trade_date"].astype(str)
            hist["close"] = pd.to_numeric(hist["close"], errors="coerce")
            hist_ret = hist.sort_values("trade_date").set_index("trade_date")["close"].pct_change()
            joined = pd.concat([hist_ret.rename("overseas"), a_market.rename("a_market")], axis=1).dropna().tail(60)
            if len(joined) >= 5:
                same_corr = float(joined["overseas"].corr(joined["a_market"]))
                lead_corr = float(joined["overseas"].shift(1).corr(joined["a_market"]))
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, KeyError):
            pass
        ret5 = item.get("ret_5d")
        if target_ret is None:
            state = "仅有海外价格，暂无对应A股板块验证"
        elif ret5 is not None and ret5 < 0 and target_ret < 0:
            state = "海外变量正常传导"
        elif ret5 is not None and ret5 < 0 and target_ret >= 0:
            state = "A股暂时吸收或走独立行情"
        elif ret5 is not None and ret5 > 0 and target_ret <= 0:
            state = "海外利好暂未获A股认可"
        else:
            state = "海外与A股方向暂时一致"
        overseas_conduction.append({"asset": asset, "group": item.get("group"), "ret_5d": ret5, "ret_20d": item.get("ret_20d"), "ret_60d": item.get("ret_60d"), "targets": [s.get("industry") for s in targets[:5]], "target_ret": target_ret, "same_corr_60": same_corr, "lead_corr_60": lead_corr, "state": state, "confidence": "中" if targets and same_corr is not None else "低"})
    flow_frame = pd.DataFrame(flows)
    market_flow_series = []
    rotation_timeline = []
    changes = []
    if not flow_frame.empty:
        flow_frame["net_mf_yi"] = pd.to_numeric(flow_frame["net_mf_yi"], errors="coerce")
        dates_available = sorted(flow_frame["trade_date"].astype(str).unique())
        if len(dates_available) >= 2:
            latest_date, previous_date = dates_available[-1], dates_available[-2]
            latest = flow_frame[flow_frame["trade_date"].astype(str) == latest_date].set_index("industry")["net_mf_yi"]
            previous = flow_frame[flow_frame["trade_date"].astype(str) == previous_date].set_index("industry")["net_mf_yi"]
            for industry in set(latest.index) | set(previous.index):
                before = float(previous.get(industry, 0) or 0)
                after = float(latest.get(industry, 0) or 0)
                if (before < 0 <= after) or (before > 0 >= after) or abs(after - before) >= 30:
                    changes.append({
                        "time": latest_date,
                        "title": f"{industry}资金由{'流出转为流入' if before < 0 <= after else '流入转为流出' if before > 0 >= after else '快速变化'}",
                        "before": round(before, 2),
                        "after": round(after, 2),
                        "meaning": "关注回流是否由龙头和中军共同确认" if after > before else "警惕冲高兑现和板块内部扩散变弱",
                        "confidence": "中" if abs(after - before) >= 60 else "低",
                        "validation": "下一交易日观察资金方向与板块涨跌是否同步",
                    })
            changes = sorted(changes, key=lambda x: abs(x["after"] - x["before"]), reverse=True)[:6]
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
    daily_sector_returns = {}
    price_review = pd.DataFrame(prices)
    if not price_review.empty and {"trade_date", "ts_code", "pct_chg"}.issubset(price_review.columns):
        latest_price_date = str(price_review["trade_date"].astype(str).max())
        latest_prices = price_review[price_review["trade_date"].astype(str) == latest_price_date].copy()
        stock_industry = stocks_frame[["ts_code", "industry"]].drop_duplicates("ts_code")
        latest_prices = latest_prices.merge(stock_industry, on="ts_code", how="left")
        latest_prices["pct_chg"] = pd.to_numeric(latest_prices["pct_chg"], errors="coerce")
        daily_sector_returns = latest_prices.dropna(subset=["industry", "pct_chg"]).groupby("industry")["pct_chg"].mean().round(3).to_dict()
    latest_flow_date = max([str(x.get("trade_date")) for x in flows], default="")
    latest_sector_flow = {str(x.get("industry")): float(x.get("net_mf_yi") or 0) for x in flows if str(x.get("trade_date")) == latest_flow_date}
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
        "market_state_evidence": state_evidence,
        "index_week_ret": index_week_ret,
        "index_day_ret": index_day_ret,
        "market_amount_ratio": market_amount_ratio,
        "style_flow": {"防御候选": round(defensive_flow, 2), "成长候选": round(growth_flow, 2)},
        "conclusion": conclusion,
        "strategy": strategy,
        "avoid_strategy": "不追逐高位无资金承接的涨幅，不把估算身份当作真实账户归属",
        "main_conflict": main_conflict,
        "primary_reason": "上涨宽度、五日主力资金、涨停/炸板结构与板块相对强弱的规则合成",
        "reason_blocks": reason_blocks,
        "strongest_sector": strongest.get("industry"),
        "strongest_sector_score": strongest.get("strength"),
        "strongest_sector_reason": f"强度 {float(strongest.get('strength') or 0):.1f}/100：涨幅、资金、上涨家数和持续性综合得分最高",
        "trade_sector": trade_sector.get("industry"),
        "trade_sector_score": trade_sector.get("trade_value_score"),
        "trade_sector_reason": f"交易价值 {float(trade_sector.get('trade_value_score') or 0):.1f}/100：{trade_sector.get('trade_value_reason', '优先观察有资金承接且上涨宽度较好的方向')}；仍需下一交易日验证",
        "position": round(min(80, max(20, money_effect * 0.7)), 0),
        "confidence": "中",
        "validation": f"验证点：观察 {lead_in or '最强承接方向'} 次日是否继续净流入，并确认龙头、中军与板块同步。",
        "invalidation": "失效条件：资金广度转负、最强板块跌破前一交易日低点，或利好方向出现放量冲高回落。",
        "news_briefs": news_briefs,
        "logic_chain": logic_chain,
        "market_flow_series": market_flow_series,
        "rotation_timeline": rotation_timeline,
        "changes": changes,
        "overseas_conduction": overseas_conduction,
        "flow_periods": [{"period": "5分钟", "available": False, "reason": "当前授权接口未提供分时资金明细"}, {"period": "15分钟", "available": False, "reason": "当前授权接口未提供分时资金明细"}, {"period": "30分钟", "available": False, "reason": "当前授权接口未提供分时资金明细"}, {"period": "当日", "available": bool(market_flow_series), "reason": "按板块日级主力净流合计"}, {"period": "3日", "available": len(market_flow_series) >= 3, "reason": "按最近可用交易日合计"}, {"period": "5日", "available": len(market_flow_series) >= 5, "reason": "按最近可用交易日合计"}, {"period": "20日", "available": False, "reason": "当前快照不足20个交易日资金明细"}],
        "proxy_funds": proxy_funds,
        "agent_series": agent_series,
        "proxy_links": proxy_links,
        "rotation_paths": rotation_paths,
    }
    previous = decision_history[-1] if decision_history else None
    latest_trade_date = max([str(x.get("trade_date")) for x in prices if x.get("trade_date")], default=None)
    accuracy_records = [x for x in decision_history if x.get("outcome_hit") is not None]
    outcome = None
    if previous and previous.get("trade_date") and latest_trade_date and previous.get("trade_date") != latest_trade_date and previous.get("trade_sector"):
        realized_ret = daily_sector_returns.get(previous.get("trade_sector"))
        realized_flow = latest_sector_flow.get(previous.get("trade_sector"))
        if realized_ret is not None:
            outcome = bool(realized_ret > 0 and (realized_flow is None or realized_flow > 0))
            previous["outcome_hit"] = outcome
            previous["outcome_ret"] = realized_ret
            previous["outcome_flow"] = realized_flow
            accuracy_records = [x for x in decision_history if x.get("outcome_hit") is not None]
    accuracy_hits = sum(1 for x in accuracy_records if x.get("outcome_hit") is True)
    accuracy_rate = round(accuracy_hits / len(accuracy_records) * 100, 1) if accuracy_records else None
    if previous:
        state_changed = previous.get("market_state") != market_state
        sector_continued = previous.get("trade_sector") == trade_sector.get("industry") and bool(trade_sector.get("industry"))
        flow_direction_now = "正" if (total_stock_flow or 0) > 0 else "负" if (total_stock_flow or 0) < 0 else "未知"
        flow_direction_before = previous.get("flow_direction")
        review_status = "判断变化，需重新确认" if state_changed else "方向延续，等待下一交易日验证"
        if sector_continued and flow_direction_before == flow_direction_now:
            review_status = "初步验证成立，方向与资金状态延续"
        review = {"status": review_status, "previous_time": previous.get("generated_at"), "previous_trade_date": previous.get("trade_date"), "previous_state": previous.get("market_state"), "current_state": market_state, "state_changed": state_changed, "trade_sector_continued": sector_continued, "flow_direction_before": flow_direction_before, "flow_direction_now": flow_direction_now, "previous_breadth": previous.get("breadth"), "current_breadth": breadth, "validation": "下一次更新继续观察交易方向、资金方向和上涨宽度是否同步；仅作复盘记录。"}
    else:
        review = {"status": "等待下一次数据后复核", "previous_time": None, "previous_trade_date": None, "previous_state": None, "current_state": market_state, "state_changed": None, "trade_sector_continued": None, "flow_direction_before": None, "flow_direction_now": "正" if (total_stock_flow or 0) > 0 else "负" if (total_stock_flow or 0) < 0 else "未知", "previous_breadth": None, "current_breadth": breadth, "validation": "当前为第一份记录，下一次成功更新后才会产生复核结果。"}
    review.update({"outcome": outcome, "accuracy_total": len(accuracy_records), "accuracy_hits": accuracy_hits, "accuracy_rate": accuracy_rate, "outcome_note": "方向验证=下一交易日板块日涨跌为正且资金未转负；样本不足时不显示准确率。"})
    summary["review"] = review
    decision_history.append({"generated_at": generated_at, "trade_date": latest_trade_date, "market_state": market_state, "strongest_sector": strongest.get("industry"), "trade_sector": trade_sector.get("industry"), "flow_direction": review.get("flow_direction_now"), "breadth": breadth})
    decision_history[-1].update({"trade_sector_ret": daily_sector_returns.get(trade_sector.get("industry")), "trade_sector_flow": latest_sector_flow.get(trade_sector.get("industry"))})
    if len(decision_history) >= 2 and decision_history[-1].get("trade_date") == latest_trade_date and decision_history[-2].get("trade_date") == latest_trade_date:
        merged_record = dict(decision_history[-2])
        merged_record.update(decision_history[-1])
        decision_history = decision_history[:-2] + [merged_record]
    try:
        history_path.write_text(json.dumps(decision_history[-30:], ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    template = (ROOT / "market_dashboard_template.html").read_text(encoding="utf-8")
    replacements = {
        "__SECTORS__": json.dumps(sectors, ensure_ascii=False, separators=(",", ":")),
        "__STOCKS__": json.dumps(stocks, ensure_ascii=False, separators=(",", ":")),
        "__FLOWS__": json.dumps(flows, ensure_ascii=False, separators=(",", ":")),
        "__PRICES__": json.dumps(prices, ensure_ascii=False, separators=(",", ":")),
        "__SUMMARY__": json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        "__NEWS__": json.dumps(news, ensure_ascii=False, separators=(",", ":")),
        "__ETFS__": json.dumps(etfs, ensure_ascii=False, separators=(",", ":")),
        "__STOCK_FLOWS__": json.dumps(stock_flows, ensure_ascii=False, separators=(",", ":")),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    OUT.mkdir(parents=True, exist_ok=True)
    context = {
        "generated_at": generated_at,
        "source": summary["source"],
        "freshness": summary["freshness"],
        "summary": summary,
        "top_sectors": sorted(sectors, key=lambda x: float(x.get("strength") or 0), reverse=True)[:10],
        "trade_value_sectors": sorted(sectors, key=lambda x: float(x.get("trade_value_score") or 0), reverse=True)[:10],
        "messages": news_briefs,
        "overseas_conduction": overseas_conduction,
        "validation_note": "结构化上下文供解释层读取；相关性不等于因果，所有结论必须回到验证点和失效条件。",
    }
    (OUT / "market_context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    vendor = OUT / "vendor"
    vendor.mkdir(exist_ok=True)
    for name in ["echarts.min.js", "tabulator.min.js", "tabulator_midnight.min.css"]:
        (vendor / name).write_bytes((SOURCE_VENDOR / name).read_bytes())
    (OUT / "index.html").write_text(template, encoding="utf-8")
    print(OUT / "index.html")


if __name__ == "__main__":
    build()
