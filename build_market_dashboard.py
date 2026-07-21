import hashlib
import json
import math
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
        adj_rows = []
        for adj_path in sorted((ROOT / "data").glob("fund_adj_*.csv")):
            try:
                adj = pd.read_csv(adj_path)
                if {"ts_code", "adj_factor"}.issubset(adj.columns):
                    if "trade_date" not in adj:
                        adj["trade_date"] = adj_path.stem.split("_")[-1]
                    adj["trade_date"] = adj["trade_date"].astype(str)
                    adj["adj_factor"] = pd.to_numeric(adj["adj_factor"], errors="coerce")
                    adj_rows.append(adj[["ts_code", "trade_date", "adj_factor"]])
            except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
                continue
        if adj_rows:
            adjustment = pd.concat(adj_rows, ignore_index=True).drop_duplicates(["ts_code", "trade_date"], keep="last")
            daily = daily.merge(adjustment, on=["ts_code", "trade_date"], how="left")
            daily["adjusted_close"] = daily["close"] * daily["adj_factor"]
        else:
            daily["adj_factor"] = pd.NA
            daily["adjusted_close"] = daily["close"]
        daily = daily.dropna(subset=["ts_code", "trade_date", "close"]).sort_values(["ts_code", "trade_date"])
        window_counts = daily.groupby("ts_code")["trade_date"].nunique().rename("window_days")
        first = daily.groupby("ts_code", as_index=False).first()[["ts_code", "trade_date", "close", "adjusted_close", "adj_factor"]].rename(columns={"trade_date":"start_date", "close":"start_close", "adjusted_close": "start_adjusted_close", "adj_factor": "start_adj_factor"})
        last = daily.groupby("ts_code", as_index=False).last()[["ts_code", "trade_date", "close", "adjusted_close", "adj_factor", "amount"]].rename(columns={"amount":"amount", "adjusted_close": "latest_adjusted_close", "adj_factor": "latest_adj_factor"})
        out = basic.merge(last, on="ts_code", how="inner").merge(first, on="ts_code", how="left").merge(window_counts, on="ts_code", how="left")
        has_adjustment = out["start_adjusted_close"].notna() & out["latest_adjusted_close"].notna()
        out["week_ret"] = (out["close"] / out["start_close"] - 1) * 100
        out.loc[has_adjustment, "week_ret"] = (out.loc[has_adjustment, "latest_adjusted_close"] / out.loc[has_adjustment, "start_adjusted_close"] - 1) * 100
        out["return_basis"] = has_adjustment.map({True: "基金复权因子调整收盘价", False: "未复权收盘价"})
        out.loc[out["trade_date"].astype(str) == out["start_date"].astype(str), "week_ret"] = pd.NA
        # TinyShare/Tushare fund_daily amount is in thousand yuan, same as stock daily amount.
        out["amount_yi"] = pd.to_numeric(out.get("amount"), errors="coerce") / 100000
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
        share_rows = []
        for share_path in sorted((ROOT / "data").glob("fund_share_*.csv")):
            try:
                share = pd.read_csv(share_path)
                share_col = "fd_share" if "fd_share" in share.columns else "share" if "share" in share.columns else None
                if share_col and "ts_code" in share.columns:
                    share["share_value"] = pd.to_numeric(share[share_col], errors="coerce")
                    share["share_date"] = share.get("trade_date", share_path.stem.split("_")[-1]).astype(str)
                    share_rows.append(share[["ts_code", "share_date", "share_value"]])
            except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
                continue
        if share_rows:
            shares = pd.concat(share_rows, ignore_index=True).dropna(subset=["share_value"]).sort_values(["ts_code", "share_date"])
            share_date_count = shares.groupby("ts_code")["share_date"].nunique()
            share_first = shares.groupby("ts_code", as_index=False).first()[["ts_code", "share_value"]].rename(columns={"share_value": "share_start"})
            share_last = shares.groupby("ts_code", as_index=False).last()[["ts_code", "share_date", "share_value"]].rename(columns={"share_value": "share_latest"})
            out = out.merge(share_last, on="ts_code", how="left").merge(share_first, on="ts_code", how="left")
            out["share_change"] = out["share_latest"] - out["share_start"]
            out["share_change_pct"] = (out["share_latest"] / out["share_start"] - 1) * 100
            insufficient_share_history = out["ts_code"].map(share_date_count).fillna(0) < 2
            out.loc[insufficient_share_history, ["share_change", "share_change_pct"]] = pd.NA
        out["return_reliable"] = True
        out["data_quality_note"] = ""
        abnormal_return = out["week_ret"].abs() > 25
        if "share_change_pct" in out:
            suspected_split = abnormal_return & (pd.to_numeric(out["share_change_pct"], errors="coerce").abs() > 30)
        else:
            suspected_split = abnormal_return & (pd.to_numeric(out["window_days"], errors="coerce") <= 5)
        suspected_split = suspected_split & ~has_adjustment
        out.loc[suspected_split, "return_reliable"] = False
        out.loc[suspected_split, "data_quality_note"] = "价格与份额同时异常跳变，疑似份额折算/拆分；缺少复权依据，窗口收益不采用"
        out.loc[suspected_split, "week_ret"] = pd.NA
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
        return records(out[[c for c in ["ts_code", "name", "fund_type", "benchmark", "invest_type", "issue_amount", "trade_date", "close", "week_ret", "window_days", "return_basis", "start_adj_factor", "latest_adj_factor", "return_reliable", "data_quality_note", "amount_yi", "nav_date", "unit_nav", "accum_nav", "net_asset", "premium_discount", "share_date", "share_latest", "share_change", "share_change_pct", "component_count", "cpr_mean", "tool_role", "tool_relevance_score", "selection_reason"] if c in out]])
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


def load_active_concepts():
    flow_paths = sorted((ROOT / "data").glob("ths_moneyflow_*.csv"))
    if not flow_paths:
        return [], {}, {"available": False, "reason": "同花顺概念资金接口尚未返回可用快照"}
    try:
        flow = pd.read_csv(flow_paths[-1])
        if not {"ts_code", "name"}.issubset(flow.columns):
            raise KeyError("missing concept code/name")
        for column in ["pct_change", "net_buy_amount", "net_sell_amount", "net_amount", "company_num", "pct_change_stock"]:
            flow[column] = pd.to_numeric(flow.get(column), errors="coerce")
        generic = flow["name"].fillna("").astype(str).str.contains("融资融券|沪股通|深股通|标普道琼斯|MSCI|富时罗素|同花顺漂亮|同花顺出海", regex=True)
        company_scale = flow["company_num"].clip(lower=1).pow(0.5)
        flow["activity_score"] = flow["net_amount"].abs().fillna(0) / company_scale + flow["pct_change"].abs().fillna(0) * 10
        flow = flow[~generic].sort_values("activity_score", ascending=False).drop_duplicates("ts_code").head(24)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, KeyError):
        return [], {}, {"available": False, "reason": "概念资金快照字段不完整"}

    member_paths = sorted((ROOT / "data").glob("ths_active_members_*.csv"))
    members = pd.DataFrame()
    if member_paths:
        try:
            members = pd.read_csv(member_paths[-1])
            stock_column = "con_code" if "con_code" in members.columns else None
            if not stock_column or "concept_code" not in members.columns:
                members = pd.DataFrame()
            else:
                members[stock_column] = members[stock_column].astype(str)
                members["concept_code"] = members["concept_code"].astype(str)
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
            members = pd.DataFrame()

    series_map = {}
    history_paths = sorted((ROOT / "data").glob("ths_active_daily_*.csv"))
    if history_paths:
        try:
            history = pd.read_csv(history_paths[-1])
            history["trade_date"] = history["trade_date"].astype(str)
            history["close"] = pd.to_numeric(history.get("close"), errors="coerce")
            for code, group in history.dropna(subset=["ts_code", "trade_date", "close"]).groupby("ts_code"):
                series_map[str(code)] = [[str(row.trade_date), round(float(row.close), 4)] for row in group.sort_values("trade_date").itertuples()]
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, KeyError):
            series_map = {}

    member_count = members.groupby("concept_code")["con_code"].nunique().to_dict() if not members.empty else {}
    active = []
    for row in flow.to_dict("records"):
        code = str(row.get("ts_code") or "")
        active.append({
            "ts_code": code,
            "name": row.get("name"),
            "trade_date": str(row.get("trade_date") or ""),
            "pct_change": row.get("pct_change"),
            "net_amount": row.get("net_amount"),
            "net_buy_amount": row.get("net_buy_amount"),
            "net_sell_amount": row.get("net_sell_amount"),
            "lead_stock": row.get("lead_stock"),
            "lead_stock_ret": row.get("pct_change_stock"),
            "reported_company_num": row.get("company_num"),
            "mapped_member_count": int(member_count.get(code, 0)),
            "price_series": series_map.get(code, []),
        })

    stock_concepts = {}
    if not members.empty:
        rank_map = {str(row.get("ts_code")): index for index, row in enumerate(flow.to_dict("records"))}
        name_map = {str(row.get("ts_code")): str(row.get("name") or "") for row in flow.to_dict("records")}
        for stock_code, group in members.groupby("con_code", sort=False):
            codes = sorted(set(group["concept_code"].astype(str)), key=lambda code: rank_map.get(code, 9999))[:3]
            stock_concepts[str(stock_code)] = [{"code": code, "name": name_map.get(code) or code} for code in codes]
    meta = {
        "available": bool(active),
        "trade_date": active[0].get("trade_date") if active else None,
        "active_count": len(active),
        "mapped_stock_count": len(stock_concepts),
        "coverage_note": "概念行情按单位覆盖规模资金强度与涨跌活跃度排序，并排除融资融券、沪深股通等通用标签；成分映射仅覆盖排名前16个概念，不代表全市场概念全集。",
    }
    return active, stock_concepts, meta


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
    concepts, stock_concepts, concept_meta = load_active_concepts()
    stocks["concepts"] = stocks["ts_code"].astype(str).map(lambda code: stock_concepts.get(code, []))
    stocks["primary_concept"] = stocks["concepts"].map(lambda values: values[0]["name"] if values else None)
    stocks["primary_concept_code"] = stocks["concepts"].map(lambda values: values[0]["code"] if values else None)
    if etfs:
        # ETF holdings make the table useful for selection: concentration and industry exposure
        # are derived only when the component snapshot contains real weights.
        component_rows = []
        for component_path in sorted((ROOT / "data").glob("etf_*_cons_*.csv")):
            try:
                component = pd.read_csv(component_path)
                if {"ts_code", "con_code"}.issubset(component.columns):
                    keep = [c for c in ["ts_code", "con_code", "con_name", "name", "weight", "qty", "cpr", "rdr", "sca", "exchange"] if c in component.columns]
                    part = component[keep].copy()
                    part["source_file"] = component_path.name
                    component_rows.append(part)
            except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
                continue
        if component_rows:
            component = pd.concat(component_rows, ignore_index=True).drop_duplicates(["ts_code", "con_code"], keep="last")
            component["weight"] = pd.to_numeric(component.get("weight"), errors="coerce")
            component["qty"] = pd.to_numeric(component.get("qty"), errors="coerce")
            latest_close = price.copy()
            if not latest_close.empty and {"ts_code", "trade_date", "close"}.issubset(latest_close.columns):
                latest_close["trade_date"] = latest_close["trade_date"].astype(str)
                latest_close["close"] = pd.to_numeric(latest_close["close"], errors="coerce")
                latest_close = latest_close.sort_values("trade_date").drop_duplicates("ts_code", keep="last")[["ts_code", "close"]]
                component = component.merge(latest_close, left_on="con_code", right_on="ts_code", how="left", suffixes=("", "_stock_price"))
                component["basket_market_value"] = component["qty"] * component["close"]
                estimated_total = component.groupby("ts_code")["basket_market_value"].transform("sum")
                estimated_weight = component["basket_market_value"] / estimated_total * 100
                component["weight"] = component["weight"].where(component["weight"].notna(), estimated_weight)
            stock_lookup = stocks[[c for c in ["ts_code", "name", "industry", "week_ret"] if c in stocks.columns]].drop_duplicates("ts_code")
            component = component.merge(stock_lookup, left_on="con_code", right_on="ts_code", how="left", suffixes=("", "_stock"))
            exposure = []
            for code, group in component.groupby("ts_code", sort=False):
                weights = group["weight"].dropna()
                total = float(weights[weights > 0].sum()) if not weights.empty else None
                ranked = group.sort_values("weight", ascending=False).dropna(subset=["weight"])
                top10 = float(ranked.head(10)["weight"].sum()) if not ranked.empty else None
                component_returns = pd.to_numeric(group.get("week_ret"), errors="coerce")
                component_weights = pd.to_numeric(group.get("weight"), errors="coerce")
                return_mask = component_returns.notna() & component_weights.notna() & (component_weights > 0)
                return_weight = float(component_weights[return_mask].sum()) if return_mask.any() else 0
                basket_week_ret = float((component_returns[return_mask] * component_weights[return_mask]).sum() / return_weight) if return_weight > 0 else None
                basket_return_coverage = float(return_weight / total * 100) if total and return_weight > 0 else None
                industry_exposure = []
                if total and total > 0:
                    by_industry = ranked[ranked["industry"].notna()].groupby("industry")["weight"].sum().sort_values(ascending=False).head(5)
                    industry_exposure = [{"industry": str(k), "weight": round(float(v / total * 100), 2)} for k, v in by_industry.items()]
                top_holdings = [{"name": str(row.get("con_name") or row.get("name") or row.get("name_stock") or row.get("con_code")), "code": str(row.get("con_code")), "weight": round(float(row["weight"]), 2)} for _, row in ranked.head(10).iterrows()]
                basis = "PCF篮子数量×A股最新收盘价估算" if group["qty"].notna().any() else "接口直接提供权重"
                exposure.append({"ts_code": code, "component_count": int(group["con_code"].nunique()), "top10_weight": round(top10, 2) if top10 is not None else None, "weight_coverage": round(total, 2) if total is not None else None, "component_weight_basis": basis, "basket_week_ret": round(basket_week_ret, 3) if basket_week_ret is not None else None, "basket_return_coverage": round(basket_return_coverage, 1) if basket_return_coverage is not None else None, "industry_exposure": industry_exposure, "top_holdings": top_holdings})
            etf_frame = pd.DataFrame(etfs).merge(pd.DataFrame(exposure), on="ts_code", how="left", suffixes=("", "_derived"))
            for column in ["component_count", "top10_weight", "weight_coverage", "component_weight_basis", "basket_week_ret", "basket_return_coverage", "industry_exposure", "top_holdings"]:
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
    stock_cols = ["industry", "name", "ts_code", "market", "week_ret", "fri_ret", "net_mf_5d_yi", "net_mf_fri_yi", "circ_mv_yi", "turnover_yi", "turnover_rate", "pe", "pb", "U", "Z", "D", "leader_score", "core_score", "elastic_score", "ann_date", "eps", "bps", "fcfe_ps", "roe", "grossprofit_margin", "netprofit_margin", "debt_to_assets", "current_ratio", "q_ocf_to_sales", "q_sales_yoy", "netprofit_yoy", "ocf_yoy", "quality_score", "growth_score", "cash_score", "leverage_score", "valuation_score", "fundamental_coverage", "fundamental_score", "concepts", "primary_concept", "primary_concept_code"]
    return (
        records(sectors[sector_cols]),
        records(stocks[stock_cols]),
        records(sector_flow[["industry", "trade_date", "net_mf_yi"]]),
        records(price) if not price.empty else [],
        etfs,
        overseas,
        records(stock_flows),
        concepts,
        concept_meta,
    )


def build():
    sectors, stocks, flows, prices, etfs, overseas, stock_flows, concepts, concept_meta = load_data()
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
    if news_path.exists():
        news_frame = pd.read_csv(news_path)
        news_frame["value_score"] = pd.to_numeric(news_frame.get("value_score"), errors="coerce")
        news_frame["time"] = news_frame.get("time", "").fillna("").astype(str)
        news_frame = news_frame.sort_values(["value_score", "time"], ascending=[False, False]).head(400)
        news_frame = news_frame.drop(columns=["content", "retrieved_at", "score_breakdown", "score_formula"], errors="ignore")
        news = records(news_frame)
    else:
        news = []
    numeric_stocks = pd.to_numeric(pd.Series([x.get("week_ret") for x in stocks]), errors="coerce").dropna()
    generated_at = pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds")
    intraday_manifest_path = ROOT / "data" / "intraday" / "manifest.json"
    try:
        intraday = json.loads(intraday_manifest_path.read_text(encoding="utf-8")) if intraday_manifest_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        intraday = {}
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
    cap_q75 = stocks_frame["circ_mv_yi"].quantile(0.75)
    turn_q75 = stocks_frame["turnover_rate"].quantile(0.75)
    # The four groups are behavior proxies, not account ownership. Keep the
    # stock universes disjoint so the same net flow is not counted repeatedly.
    state_mask = stocks_frame["circ_mv_yi"] >= cap_q75
    hot_mask = (~state_mask) & (stocks_frame["turnover_rate"] >= turn_q75) & (stocks_frame["U"].fillna(0) + stocks_frame["Z"].fillna(0) > 0)
    institution_mask = (~state_mask) & (~hot_mask) & (stocks_frame["circ_mv_yi"] >= cap_mid) & (stocks_frame["turnover_rate"] <= turn_mid)
    retail_mask = ~(state_mask | hot_mask | institution_mask)
    proxy_specs = [
        ("国家队代理", state_mask, "流通市值前25%权重股的超大单/主力方向"),
        ("机构代理", institution_mask, "非权重样本中大市值、低换手股票的大单/主力方向"),
        ("游资代理", hot_mask, "非权重样本中涨停/炸板且高换手股票的大单方向"),
        ("散户代理", retail_mask, "其余股票的小单与中单方向；仅作行为代理"),
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
    stock_agent_series = {}
    stock_class = stocks_frame[["ts_code", "industry", "circ_mv_yi", "turnover_rate", "U", "Z"]].copy()
    for path in sorted((ROOT / "data").glob("moneyflow_*.csv")):
        try:
            mf = pd.read_csv(path)
            mf["net_mf_amount"] = pd.to_numeric(mf["net_mf_amount"], errors="coerce")
            merged = mf.merge(stock_class, on="ts_code", how="inner").dropna(subset=["net_mf_amount"])
            for col in ["buy_sm_amount", "sell_sm_amount", "buy_md_amount", "sell_md_amount", "buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount"]:
                merged[col] = pd.to_numeric(merged.get(col), errors="coerce").fillna(0)
            merged["small_mid_net"] = merged["buy_sm_amount"] - merged["sell_sm_amount"] + merged["buy_md_amount"] - merged["sell_md_amount"]
            merged["large_net"] = merged["buy_lg_amount"] - merged["sell_lg_amount"]
            merged["extra_large_net"] = merged["buy_elg_amount"] - merged["sell_elg_amount"]
            state_day = merged["circ_mv_yi"] >= cap_q75
            hot_day = (~state_day) & (merged["turnover_rate"] >= turn_q75) & (merged["U"].fillna(0) + merged["Z"].fillna(0) > 0)
            institution_day = (~state_day) & (~hot_day) & (merged["circ_mv_yi"] >= cap_mid) & (merged["turnover_rate"] <= turn_mid)
            retail_day = ~(state_day | hot_day | institution_day)
            masks = {
                "国家队代理": (state_day, "extra_large_net"),
                "机构代理": (institution_day, "extra_large_net"),
                "游资代理": (hot_day, "large_net"),
                "散户代理": (retail_day, "small_mid_net"),
            }
            date = str(mf["trade_date"].iloc[0]) if not mf.empty else path.stem.split("_")[-1]
            for name, (mask, value_col) in masks.items():
                value = float(merged.loc[mask, value_col].sum()) / 100000 if mask.any() else None
                agent_series.append({"trade_date": date, "name": name, "value": round(value, 2) if value is not None else None})
                if mask.any():
                    grouped = merged.loc[mask].groupby("industry")[value_col].sum() / 100000
                    for industry, sector_value in grouped.items():
                        sector_agent_series.setdefault(str(industry), []).append({"trade_date": date, "name": name, "value": round(float(sector_value), 2)})
                    stock_grouped = merged.loc[mask].groupby("ts_code")[value_col].sum() / 100000
                    for code, stock_value in stock_grouped.items():
                        stock_agent_series.setdefault(str(code), []).append({"trade_date": date, "name": name, "value": round(float(stock_value), 2)})
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, KeyError):
            continue
    for sector in sectors:
        sector["agent_series"] = sector_agent_series.get(str(sector.get("industry")), [])
    for stock in stocks:
        stock["agent_series"] = stock_agent_series.get(str(stock.get("ts_code")), [])
    proxy_basis = {name: basis for name, _, basis in proxy_specs}
    for proxy in proxy_funds:
        values = [row.get("value") for row in agent_series if row.get("name") == proxy.get("name") and row.get("value") is not None]
        value = float(sum(values)) if values else None
        proxy.update({
            "value": round(value, 2) if value is not None else None,
            "direction": "净流入" if value is not None and value > 0 else "净流出" if value is not None and value < 0 else "暂无数据",
            "basis": proxy_basis.get(proxy.get("name"), proxy.get("basis")),
            "window": f"最近{len(values)}个可用交易日",
        })
    for sector in sectors:
        rows = sector.get("agent_series", [])
        coverage_map = {row.get("name"): row.get("coverage") for row in sector.get("agent_flows", [])}
        rebuilt = []
        for name in proxy_basis:
            values = [row.get("value") for row in rows if row.get("name") == name and row.get("value") is not None]
            value = float(sum(values)) if values else None
            rebuilt.append({
                "name": name,
                "value": round(value, 2) if value is not None else None,
                "direction": "净流入" if value is not None and value > 0 else "净流出" if value is not None and value < 0 else "暂无数据",
                "coverage": coverage_map.get(name, 0),
                "basis": proxy_basis[name],
                "window": f"最近{len(values)}个可用交易日",
            })
        sector["agent_flows"] = rebuilt
    market_price_series = []
    price_frame = pd.DataFrame(prices)
    if not price_frame.empty:
        price_frame["trade_date"] = price_frame["trade_date"].astype(str)
        price_frame["close"] = pd.to_numeric(price_frame["close"], errors="coerce")
        latest_prices = price_frame.dropna(subset=["close"]).sort_values(["ts_code", "trade_date"]).drop_duplicates("ts_code", keep="last").set_index("ts_code")["close"].to_dict()
        for stock in stocks:
            stock["latest_price"] = latest_prices.get(stock.get("ts_code"))
        industry_lookup = stocks_frame[["ts_code", "industry"]].drop_duplicates("ts_code")
        sector_prices = price_frame.merge(industry_lookup, on="ts_code", how="left").dropna(subset=["industry", "close"])
        sector_prices = sector_prices.groupby(["industry", "trade_date"], as_index=False)["close"].median()
        market_returns = price_frame.sort_values(["ts_code", "trade_date"]).copy()
        market_returns["daily_ret"] = market_returns.groupby("ts_code")["close"].pct_change()
        market_returns = market_returns.groupby("trade_date")["daily_ret"].mean().dropna().sort_index()
        market_index = (1 + market_returns).cumprod() * 100
        market_price_series = [[str(date), round(float(value), 4)] for date, value in market_index.items()]
        sector_price_map = {
            str(industry): [[str(row.trade_date), round(float(row.close), 4)] for row in group.itertuples()]
            for industry, group in sector_prices.sort_values("trade_date").groupby("industry", sort=False)
        }
        price_by_code = {
            str(code): [[str(row.trade_date), round(float(row.close), 4)] for row in group.sort_values("trade_date").itertuples()]
            for code, group in price_frame.dropna(subset=["ts_code", "close"]).groupby("ts_code", sort=False)
        }
        leader_by_industry = {}
        for industry, group in stocks_frame.groupby("industry", sort=False):
            ranked = group.sort_values(["leader_score", "core_score"], ascending=False)
            if not ranked.empty:
                row = ranked.iloc[0]
                leader_by_industry[str(industry)] = {"code": str(row.get("ts_code")), "name": row.get("name")}
        for sector in sectors:
            industry = str(sector.get("industry"))
            sector["price_series"] = sector_price_map.get(industry, [])
            leader = leader_by_industry.get(industry, {})
            sector["leader_code"] = leader.get("code")
            sector["leader_name"] = leader.get("name")
            sector["leader_price_series"] = price_by_code.get(leader.get("code"), [])
        for stock in stocks:
            leader = leader_by_industry.get(str(stock.get("industry")), {})
            stock["industry_leader_code"] = leader.get("code")
            stock["industry_leader_name"] = leader.get("name")
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
    rotation_paths = [{"from": s.get("industry"), "to": t.get("industry"), "value": round(min(abs(float(s.get("net_mf_yi") or 0)), abs(float(t.get("net_mf_yi") or 0))), 2), "from_flow": round(float(s.get("net_mf_yi") or 0), 2), "to_flow": round(float(t.get("net_mf_yi") or 0), 2), "confidence": "中" if float(s.get("net_mf_yi") or 0) < 0 and float(t.get("net_mf_yi") or 0) > 0 else "低", "basis": "同一窗口板块净流出与净流入按规模配对，仅为迁移推测，不代表账户资金逐笔转移"} for s, t in zip(top_out, top_in)]
    strongest = max(sectors, key=lambda x: float(x.get("strength") or 0), default={})
    rotation_candidates = [x for x in sectors if x.get("state") == "潜在轮动方向"]
    trade_sector = max(rotation_candidates or sectors, key=lambda x: float(x.get("trade_value_score") or 0), default={})
    latest_price_map = {}
    if prices:
        price_frame_for_plan = pd.DataFrame(prices)
        if not price_frame_for_plan.empty and {"ts_code", "trade_date", "close"}.issubset(price_frame_for_plan.columns):
            price_frame_for_plan = price_frame_for_plan.sort_values("trade_date").drop_duplicates("ts_code", keep="last")
            latest_price_map = dict(zip(price_frame_for_plan["ts_code"].astype(str), pd.to_numeric(price_frame_for_plan["close"], errors="coerce")))
    trade_plan = []
    trade_industry = trade_sector.get("industry")
    if trade_industry:
        candidates = stocks_frame[stocks_frame["industry"].eq(trade_industry)].copy()
        candidates["role_score"] = pd.to_numeric(candidates.get("leader_score"), errors="coerce").fillna(0) * 0.45 + pd.to_numeric(candidates.get("core_score"), errors="coerce").fillna(0) * 0.35 + pd.to_numeric(candidates.get("elastic_score"), errors="coerce").fillna(0) * 0.20
        for _, candidate in candidates.sort_values("role_score", ascending=False).head(3).iterrows():
            code = str(candidate.get("ts_code"))
            role = "龙头候选" if float(candidate.get("leader_score") or 0) >= max(float(candidate.get("core_score") or 0), float(candidate.get("elastic_score") or 0)) else "中军/趋势候选"
            trade_plan.append({"kind": "个股", "name": candidate.get("name"), "ts_code": code, "industry": trade_industry, "role": role, "price": float(latest_price_map[code]) if code in latest_price_map and pd.notna(latest_price_map[code]) else None, "evidence": f"周涨跌 {float(candidate.get('week_ret') or 0):.2f}% · 5日主力净流 {float(candidate.get('net_mf_5d_yi') or 0):.2f}亿 · 基本面覆盖 {float(candidate.get('fundamental_coverage') or 0):.0f}%", "continue_if": f"{trade_industry}资金继续为正，且该股相对板块不转弱", "drop_if": "板块资金转负、个股跌破支撑或龙头/中军同步走弱"})
    for etf in sorted([x for x in etfs if x.get("tool_role") in {"宽基工具", "行业/风格工具", "海外联动"} and x.get("return_reliable") is not False], key=lambda x: float(x.get("tool_relevance_score") or 0), reverse=True)[:2]:
        trade_plan.append({"kind": "ETF", "name": etf.get("name"), "ts_code": etf.get("ts_code"), "industry": etf.get("benchmark") or etf.get("tool_role"), "role": etf.get("tool_role"), "price": etf.get("close"), "evidence": etf.get("selection_reason") or "工具类型与成交额可用", "continue_if": "跟踪基准与对应板块方向同步，成交额和溢价/折价没有异常扩大", "drop_if": "跟踪基准脱钩、溢价/折价异常或成交流动性明显下降"})
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
    ranked_news = sorted(news, key=lambda x: float(x.get("value_score") or 0), reverse=True)
    macro_top = [x for x in ranked_news if x.get("is_macro") is True][:2]
    company_top = next((x for x in ranked_news if x.get("is_macro") is not True), None)
    news_top = sorted(([company_top] if company_top else []) + macro_top, key=lambda x: float(x.get("value_score") or 0), reverse=True)[:3]
    macro_profiles = {"inflation": ("周期/资源、金融", "高估值成长、消费"), "overseas_inflation": ("价值、防御", "海外敏感成长"), "liquidity": ("宽基、金融、地产链", "高杠杆与高估值方向"), "monetary_policy": ("利率敏感资产", "利率上行敏感方向"), "overseas_rate": ("美元/利率受益方向", "高估值成长、外资敏感方向")}
    price_history_by_code = {}
    for row in prices:
        code = str(row.get("ts_code") or "")
        if code:
            price_history_by_code.setdefault(code, []).append(row)
    for rows in price_history_by_code.values():
        rows.sort(key=lambda x: str(x.get("trade_date") or ""))

    def finite_number(value):
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    def stock_reference(row, relation, tier):
        if row is None:
            return None
        return {
            "name": row.get("name"),
            "ts_code": row.get("ts_code"),
            "relation": relation,
            "tier": tier,
            "week_ret": finite_number(row.get("week_ret")),
            "net_mf_5d_yi": finite_number(row.get("net_mf_5d_yi")),
            "pe": finite_number(row.get("pe")),
            "pb": finite_number(row.get("pb")),
            "revenue_yoy": finite_number(row.get("q_sales_yoy")),
            "profit_yoy": finite_number(row.get("netprofit_yoy")),
            "fundamental_coverage": finite_number(row.get("fundamental_coverage")),
        }

    def sector_roles(industry):
        if not industry or stocks_frame.empty or industry not in stocks_frame.get("industry", pd.Series(dtype=str)).values:
            return []
        frame = stocks_frame[stocks_frame["industry"] == industry].copy()
        roles = []
        used = set()
        for label, score in (("龙头", "leader_score"), ("中军", "core_score"), ("跟随/弹性", "elastic_score")):
            if score not in frame:
                continue
            ranked = frame[~frame["ts_code"].astype(str).isin(used)].sort_values(score, ascending=False)
            if ranked.empty:
                continue
            row = ranked.iloc[0]
            used.add(str(row.get("ts_code")))
            ref = stock_reference(row, f"同属{industry}，按{label}规则排序", label)
            if ref:
                roles.append(ref)
        return roles

    def matching_etfs(industry):
        if not industry or industry in {"未映射", "宏观政策"}:
            return []
        matched = []
        for etf in etfs:
            text = f"{etf.get('name') or ''}|{etf.get('benchmark') or ''}"
            exposures = [str(x.get("industry") or "") for x in (etf.get("industry_exposure") or [])]
            if industry not in text and not any(industry == x or industry in x or x in industry for x in exposures if x):
                continue
            role_priority = 3 if etf.get("tool_role") == "行业/风格工具" else 2 if etf.get("tool_role") == "宽基工具" else 1 if etf.get("tool_role") == "海外联动" else 0
            score = (role_priority, 2 if industry in text else 1, finite_number(etf.get("amount_yi")) or 0)
            matched.append((score, etf))
        return [x[1] for x in sorted(matched, key=lambda x: x[0], reverse=True)]

    def infer_operating_path(item, direction):
        text = f"{item.get('title') or ''} {item.get('content') or ''}"
        category = item.get("macro_category")
        if category == "liquidity":
            return "流动性→折现率与风险偏好", "操作量需扣除到期量；未取得净投放前不判断宽松强度", "盈利不直接改变，先影响估值折现率和配置偏好"
        if category in {"inflation", "overseas_inflation"}:
            return "价格→收入/成本→利润率", "需比较公布值、前值和一致预期，并区分上游与下游", "只有价格传导到收入或成本后才改变盈利预期"
        if category in {"monetary_policy", "overseas_rate"}:
            return "利率→融资成本/折现率→估值", "利率方向与预期差决定传导方向", "盈利和估值可能反向变化，需分别验证"
        if any(word in text for word in ("终止", "取消", "解除")) and any(word in text for word in ("合同", "中标", "订单")):
            return "订单减少→收入预期承压", "公告确认合同/订单终止，但未从标题取得收入占比", "利润影响取决于合同金额、毛利率和原计划确认节奏"
        if any(word in text for word in ("中标", "签署合同", "签订合同", "订单")):
            return "订单增加→收入确认", "公告主体获得合同/订单；金额占比和确认周期仍需核对原文", "只有订单转为收入并形成毛利后才能确认利润增量"
        if any(word in text for word in ("业绩预增", "扭亏", "净利润增长")):
            return "利润预期上修", "公告直接涉及盈利变化，仍需剔除非经常性损益", "利润影响较直接，但持续性需下一报告期验证"
        if any(word in text for word in ("业绩预减", "预亏", "净利润下降", "亏损")):
            return "利润预期下修", "公告直接涉及盈利下降，仍需拆分经营与一次性因素", "利润影响较直接，现金流是否同步仍需验证"
        if any(word in text for word in ("涨价", "提价")):
            return "产品价格上升→收入/毛利率", "需验证销量、客户接受度与原材料成本是否抵消", "量价和成本三者共同决定利润弹性"
        if any(word in text for word in ("降价", "价格下调")):
            return "产品价格下降→收入/竞争格局", "需验证销量补偿和成本下降幅度", "利润率可能承压，但不能仅凭降价确认利润下降"
        if any(word in text for word in ("回购", "增持")):
            return "流通供给/信号→估值与情绪", "不直接增加主营收入和利润", "盈利不变，主要验证估值和资金承接"
        if any(word in text for word in ("减持", "解禁")):
            return "流通供给增加→估值与情绪", "不直接改变主营盈利，但可能增加交易供给", "盈利不变，主要验证折价压力和资金承接"
        return "经营传导机制尚未识别", "当前结构化消息未提供可验证的供需、成本、收入或订单路径", "不据此上调或下调盈利预期"

    def event_price_window(item):
        code = str(item.get("ts_code") or "")
        rows = price_history_by_code.get(code, [])
        event_date = "".join(ch for ch in str(item.get("time") or "") if ch.isdigit())[:8]
        if not rows or not event_date:
            return {"pre_5d_ret": None, "post_ret": None, "event_ret": None, "evidence": "缺少公告主体消息前后价格窗口"}
        before = [x for x in rows if str(x.get("trade_date") or "") < event_date]
        after = [x for x in rows if str(x.get("trade_date") or "") >= event_date]
        pre_ret = None
        if len(before) >= 6:
            start, end = finite_number(before[-6].get("close")), finite_number(before[-1].get("close"))
            pre_ret = (end / start - 1) * 100 if start and end else None
        baseline = finite_number(before[-1].get("close")) if before else None
        latest = finite_number(after[-1].get("close")) if after else None
        post_ret = (latest / baseline - 1) * 100 if baseline and latest else None
        event_ret = finite_number(after[0].get("pct_chg")) if after else None
        return {"pre_5d_ret": pre_ret, "post_ret": post_ret, "event_ret": event_ret, "evidence": f"公告前可用{len(before)}日、公告后可用{len(after)}日真实行情"}

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
        if item.get("is_macro") is True:
            acceptance = item.get("market_acceptance") or acceptance
        affected_stock = item.get("name") or None
        etf_candidates = matching_etfs(industry)
        affected_etf = (etf_candidates[0].get("name") or etf_candidates[0].get("ts_code")) if etf_candidates else None
        direct_rows = stocks_frame[(stocks_frame["ts_code"].astype(str) == str(item.get("ts_code") or "")) | (stocks_frame["name"].astype(str) == str(item.get("name") or ""))] if not stocks_frame.empty else pd.DataFrame()
        direct_row = direct_rows.iloc[0] if not direct_rows.empty else None
        direct_ref = stock_reference(direct_row, "公告/消息主体", "直接影响") if direct_row is not None else None
        role_refs = sector_roles(industry)
        related_refs = [x for x in role_refs if not direct_ref or x.get("ts_code") != direct_ref.get("ts_code")]
        true_impact = [direct_ref] if direct_ref else []
        business_related = related_refs[:3]
        concept_only = []
        beneficiary_refs = true_impact if direction == "偏利好" else []
        harmed_refs = true_impact if direction == "偏利空" else []
        beneficiary_objects = "、".join(f"{x.get('name')} {x.get('ts_code')}（直接影响）" for x in beneficiary_refs) or "未识别可验证的直接受益标的"
        harmed_objects = "、".join(f"{x.get('name')} {x.get('ts_code')}（直接影响）" for x in harmed_refs) or "未识别可验证的直接受损标的"
        scope = "个股+板块+ETF" if direct and affected_etf else "板块+ETF观察" if affected_etf else "个股+所属板块" if direct else "板块观察"
        impact_strength = "高" if float(item.get("value_score") or 0) >= 75 else "中" if float(item.get("value_score") or 0) >= 50 else "低"
        operating_path, operating_evidence, profit_path = infer_operating_path(item, direction)
        price_window = event_price_window(item)
        pre_ret = price_window.get("pre_5d_ret")
        post_ret = price_window.get("post_ret")
        if pre_ret is None:
            pre_traded = "缺少消息前5日完整价格窗口，无法判断"
        elif (direction == "偏利好" and pre_ret >= 5) or (direction == "偏利空" and pre_ret <= -5):
            pre_traded = f"消息前5日已同向变动 {pre_ret:.2f}%，存在提前交易可能"
        else:
            pre_traded = f"消息前5日变动 {pre_ret:.2f}%，未达到5%的提前交易观察阈值"
        digestion_score = None
        digestion_factors = []
        if pre_ret is not None and direction != "中性":
            digestion_score = 50 if abs(pre_ret) >= 5 else 20
            digestion_factors.append(f"消息前5日同向幅度 {abs(pre_ret):.2f}%")
        if post_ret is not None and direction != "中性":
            recognized_after = (direction == "偏利好" and post_ret > 0) or (direction == "偏利空" and post_ret < 0)
            digestion_score = max(0, min(100, (digestion_score or 20) - 10 if recognized_after else (digestion_score or 20) + 25))
            digestion_factors.append(f"消息后至今 {post_ret:.2f}%")
        consumption = f"规则估算 {digestion_score}/100（{'；'.join(digestion_factors)}）" if digestion_score is not None else "缺少消息前后价格窗口，不计算消化程度"
        leader_ref = next((x for x in role_refs if x.get("tier") == "龙头"), None)
        core_ref = next((x for x in role_refs if x.get("tier") == "中军"), None)
        checks = []
        if direct_ref:
            direct_ret = direct_ref.get("week_ret")
            direct_flow = direct_ref.get("net_mf_5d_yi")
            checks.extend([
                {"label": "公告主体价格", "value": f"{direct_ref.get('name')} {direct_ret:.2f}%" if direct_ret is not None else f"{direct_ref.get('name')} 未提供", "match": None if direct_ret is None or direction == "中性" else (direct_ret > 0 if direction == "偏利好" else direct_ret < 0), "source": "公告主体窗口涨跌"},
                {"label": "公告主体资金", "value": f"{direct_flow:.2f}亿" if direct_flow is not None else "未提供", "match": None if direct_flow is None or direction == "中性" else (direct_flow > 0 if direction == "偏利好" else direct_flow < 0), "source": "公告主体5日主力净流"},
            ])
        sector_breadth = finite_number(sector.get("breadth")) if sector else None
        sector_turnover = finite_number(sector.get("turnover_yi")) if sector else None
        checks.extend([
            {"label": "板块价格", "value": f"{sector_ret:.2f}%" if sector_ret is not None else "未提供", "match": None if sector_ret is None or direction == "中性" else (sector_ret > 0 if direction == "偏利好" else sector_ret < 0), "source": "板块窗口涨跌"},
            {"label": "板块资金", "value": f"{sector_flow:.2f}亿" if sector_flow is not None else "未提供", "match": None if sector_flow is None or direction == "中性" else (sector_flow > 0 if direction == "偏利好" else sector_flow < 0), "source": "板块5日主力净流"},
            {"label": "板块上涨宽度", "value": f"{sector_breadth:.1f}%" if sector_breadth is not None else "未提供", "match": None if sector_breadth is None or direction == "中性" else (sector_breadth >= 50 if direction == "偏利好" else sector_breadth < 50), "source": "上涨成分股数/有效成分股数"},
            {"label": "板块成交额", "value": f"{sector_turnover:.2f}亿" if sector_turnover is not None else "未提供", "match": None, "source": "当前窗口成交额；缺少消息前基准，不判断放量/缩量"},
            {"label": "龙头", "value": f"{leader_ref.get('name')} {leader_ref.get('week_ret'):.2f}%" if leader_ref and leader_ref.get("week_ret") is not None else "未提供", "match": None if not leader_ref or leader_ref.get("week_ret") is None or direction == "中性" else (leader_ref.get("week_ret") > 0 if direction == "偏利好" else leader_ref.get("week_ret") < 0), "source": "板块龙头规则与窗口涨跌"},
            {"label": "中军", "value": f"{core_ref.get('name')} {core_ref.get('week_ret'):.2f}%" if core_ref and core_ref.get("week_ret") is not None else "未提供", "match": None if not core_ref or core_ref.get("week_ret") is None or direction == "中性" else (core_ref.get("week_ret") > 0 if direction == "偏利好" else core_ref.get("week_ret") < 0), "source": "板块中军规则与窗口涨跌"},
        ])
        primary_etf = etf_candidates[0] if etf_candidates else None
        if primary_etf:
            etf_ret = finite_number(primary_etf.get("week_ret"))
            checks.append({"label": "匹配ETF", "value": f"{primary_etf.get('name') or primary_etf.get('ts_code')} {etf_ret:.2f}%" if etf_ret is not None else f"{primary_etf.get('name') or primary_etf.get('ts_code')} 收益未提供", "match": None if etf_ret is None or direction == "中性" else (etf_ret > 0 if direction == "偏利好" else etf_ret < 0), "source": "明确行业/基准匹配ETF"})
            etf_share_change = finite_number(primary_etf.get("share_change_pct"))
            checks.append({"label": "ETF份额变化", "value": f"{etf_share_change:.2f}%" if etf_share_change is not None else "未提供", "match": None if etf_share_change is None or direction == "中性" else (etf_share_change > 0 if direction == "偏利好" else etf_share_change < 0), "source": "基金份额快照变化；不等于纯申赎金额"})
        available_checks = [x for x in checks if x.get("match") is not None]
        matched_checks = sum(1 for x in available_checks if x.get("match") is True)
        recognition_score = round(matched_checks / len(available_checks) * 100) if available_checks else None
        if recognition_score is not None:
            acceptance = f"{matched_checks}/{len(available_checks)}项价格资金证据同向（{recognition_score}/100）；仅表示市场表现一致，不证明因果"
        valuation_evidence = "未映射公告主体估值数据"
        if direct_ref:
            valuation_evidence = f"公告主体 PE {direct_ref.get('pe') if direct_ref.get('pe') is not None else '未提供'}、PB {direct_ref.get('pb') if direct_ref.get('pb') is not None else '未提供'}；消息不直接生成目标价"
        institution_state = f"板块5日主力净流 {sector_flow:.2f}亿" if sector_flow is not None else "板块资金未提供"
        if core_ref and core_ref.get("net_mf_5d_yi") is not None:
            institution_state += f"；中军代理资金 {core_ref.get('net_mf_5d_yi'):.2f}亿"
        etf_state = "未找到名称、基准或行业暴露明确匹配的ETF"
        if primary_etf:
            share_text = f"{primary_etf.get('share_change_pct'):.2f}%" if finite_number(primary_etf.get("share_change_pct")) is not None else "未提供"
            premium_text = f"{primary_etf.get('premium_discount'):.2f}%" if finite_number(primary_etf.get("premium_discount")) is not None else "未提供"
            etf_state = f"{primary_etf.get('name') or primary_etf.get('ts_code')}：份额变化 {share_text}，溢折价 {premium_text}"
        impact = {"impact_type": item.get("impact_type") if item.get("is_macro") is True else "直接影响" if direct else "间接映射", "impact_scope": item.get("impact_scope") if item.get("is_macro") is True else scope, "impact_strength": impact_strength, "duration": "持续性待验证（当前快照不推断时间长度）", "pre_traded": pre_traded, "consumption": consumption, "digestion_score": digestion_score, "market_acceptance": acceptance, "recognition_score": recognition_score, "sector_ret": sector_ret, "sector_flow": sector_flow, "validation": item.get("validation") if item.get("is_macro") is True else f"验证：{industry}次日资金、龙头/中军与明确匹配 ETF 是否同步。" if sector else "验证：先补充可映射的板块或标的。", "affected_stock": affected_stock, "affected_etf": affected_etf, "operating_path": operating_path, "operating_evidence": operating_evidence, "profit_path": profit_path, "valuation_evidence": valuation_evidence, "institution_proxy": institution_state, "etf_fund_evidence": etf_state, "asset_tiers": {"direct": true_impact, "business_related": business_related, "concept_only": concept_only}, "role_targets": role_refs, "verification_checks": checks, "price_window": price_window}
        direct_label = direct_ref.get("name") if direct_ref else "无可验证直接标的"
        related_label = "、".join(x.get("name") for x in business_related) or "无可验证名单"
        impact["impact_chain_v2"] = [
            {"stage": "1 消息判断", "headline": f"{direction} · {impact_strength}强度", "evidence": [f"{item.get('time') or '时间未知'} · {item.get('source') or '来源未知'}", f"直接/间接：{impact['impact_type']} · 范围：{impact['impact_scope']}", pre_traded]},
            {"stage": "2 经营与盈利", "headline": operating_path, "evidence": [operating_evidence, profit_path]},
            {"stage": "3 估值与配置", "headline": "估值、机构代理与ETF资金", "evidence": [valuation_evidence, institution_state, etf_state]},
            {"stage": "4 标的分层", "headline": f"直接：{direct_label}", "evidence": [f"业务相关但业绩未验证：{related_label}", "纯概念映射：未形成可验证名单时不展示", f"受益：{beneficiary_objects}", f"受损：{harmed_objects}"]},
            {"stage": "5 市场验证", "headline": acceptance, "evidence": [f"{x['label']}：{x['value']}（{'同向' if x.get('match') is True else '背离' if x.get('match') is False else '待验证'}）" for x in checks] + [consumption, impact["validation"]]},
        ]
        impact["impact_chain"] = [{"stage": x["stage"], "value": x["headline"], "evidence": "；".join(x["evidence"][:2])} for x in impact["impact_chain_v2"]]
        item.update(impact)
        news_briefs.append({"title": item.get("title"), "url": item.get("url"), "source": item.get("source"), "time": item.get("time"), "industry": industry, "name": affected_stock, "direction": direction, "value_score": item.get("value_score"), "trust_score": item.get("trust_score"), "score_breakdown": item.get("score_breakdown"), "score_formula": item.get("score_formula"), "reason": item.get("reasons"), "beneficiary_objects": beneficiary_objects, "harmed_objects": harmed_objects, **impact})
    for brief, raw in zip(news_briefs, news_top):
        if raw.get("is_macro") is True:
            category = raw.get("macro_category")
            benefit, risk = macro_profiles.get(category, ("需结合数据方向确认", "需结合数据方向确认"))
            broad_etfs = sorted([x for x in etfs if x.get("tool_role") == "宽基工具"], key=lambda x: finite_number(x.get("amount_yi")) or 0, reverse=True)
            broad_etf = broad_etfs[0] if broad_etfs else None
            broad_etf_name = (broad_etf.get("name") or broad_etf.get("ts_code")) if broad_etf else None
            broad_etf_evidence = "未找到可验证宽基ETF"
            if broad_etf:
                broad_return_text = f"{broad_etf.get('week_ret'):.2f}%" if finite_number(broad_etf.get("week_ret")) is not None else "未提供"
                broad_share_text = f"{broad_etf.get('share_change_pct'):.2f}%" if finite_number(broad_etf.get("share_change_pct")) is not None else "未提供"
                broad_premium_text = f"{broad_etf.get('premium_discount'):.2f}%" if finite_number(broad_etf.get("premium_discount")) is not None else "未提供"
                broad_etf_evidence = f"{broad_etf_name}：窗口涨跌 {broad_return_text}，份额变化 {broad_share_text}，溢折价 {broad_premium_text}"
            macro_checks = [
                {"label": "市场等权价格", "value": f"{mean_ret:.2f}%", "match": None, "source": "全市场个股窗口等权涨跌"},
                {"label": "全市场资金", "value": f"{total_stock_flow:.2f}亿" if total_stock_flow is not None else "未提供", "match": None, "source": "个股5日主力净流汇总"},
                {"label": "上涨宽度", "value": f"{breadth:.1f}%" if breadth is not None else "未提供", "match": None, "source": "上涨个股数/有效个股数"},
                {"label": "宽基ETF", "value": broad_etf_evidence, "match": None, "source": "成交活跃宽基ETF观察"},
            ]
            validation = raw.get("validation") or "比较前值与一致预期，再观察指数宽度、利率、相关板块和 ETF 是否同步"
            brief.update({"is_macro": True, "macro_category": category, "industry": "宏观政策", "impact_type": raw.get("impact_type") or "间接影响（宏观情景映射）", "impact_scope": raw.get("impact_scope") or "指数+风格+板块+ETF（情景映射）", "affected_stock": None, "affected_etf": broad_etf_name, "macro_benefit_scenarios": raw.get("macro_benefit_scenarios") or benefit, "macro_risk_scenarios": raw.get("macro_risk_scenarios") or risk, "beneficiary_objects": f"受益情景：{benefit}；没有预期差证据前不生成确定受益名单", "harmed_objects": f"风险情景：{risk}；没有预期差证据前不生成确定受损名单", "etf_fund_evidence": broad_etf_evidence, "verification_checks": macro_checks, "validation": validation})
            brief["impact_chain_v2"] = [
                {"stage": "1 消息判断", "headline": f"宏观情景 · {brief.get('impact_strength') or '强度待定'}", "evidence": [f"{raw.get('time') or '时间未知'} · {raw.get('source') or '来源未知'}", f"范围：{brief.get('impact_scope')}", "必须结合前值、一致预期和净投放/实际值判断方向"]},
                {"stage": "2 经营与盈利", "headline": brief.get("operating_path") or "宏观变量传导", "evidence": [brief.get("operating_evidence") or "传导机制待验证", brief.get("profit_path") or "盈利影响待验证"]},
                {"stage": "3 估值与配置", "headline": "折现率、风险偏好与宽基ETF", "evidence": [f"受益情景：{benefit}", f"风险情景：{risk}", broad_etf_evidence]},
                {"stage": "4 标的分层", "headline": "不生成确定个股受益名单", "evidence": ["宏观数据先映射指数、风格和ETF", "板块与个股必须等待实际价格、资金和盈利预期验证", f"宽基观察：{broad_etf_name or '未映射'}"]},
                {"stage": "5 市场验证", "headline": brief.get("market_acceptance") or "等待市场验证", "evidence": [f"{x['label']}：{x['value']}" for x in macro_checks] + [validation]},
            ]
            brief["impact_chain"] = [{"stage": x["stage"], "value": x["headline"], "evidence": "；".join(x["evidence"][:2])} for x in brief["impact_chain_v2"]]
    for brief in news_briefs:
        if brief.get("is_macro") is True:
            continue
        target = brief.get("industry") or "未映射板块"
        direction = brief.get("direction") or "中性"
        if direction == "偏利好":
            benefit, risk = f"{target}相关龙头、中军和匹配ETF（情景受益）", f"高位拥挤、提前交易或利好兑现（情景风险）"
        elif direction == "偏利空":
            benefit, risk = f"防御或替代方向（情景受益，需重新识别）", f"{target}相关龙头、中军和匹配ETF（情景风险）"
        else:
            benefit, risk = f"{target}相关资产（中性待验证）", "方向不明，暂不把消息当作交易驱动"
        brief.update({"benefit_scenarios": benefit, "risk_scenarios": risk})
    chain_head = news_briefs[0] if news_briefs else None
    overseas_lead = next((x for x in overseas if x.get("targets")), None)
    chain_sector = chain_head.get("industry") if chain_head and chain_head.get("industry") in sector_map else lead_in
    chain_stock = None
    if chain_sector and not stocks_frame.empty:
        candidates = stocks_frame[stocks_frame["industry"] == chain_sector].sort_values("leader_score", ascending=False)
        chain_stock = candidates.iloc[0] if not candidates.empty else None
    chain_etf = next((x for x in etfs if chain_sector and (chain_sector in str(x.get("name") or "") or chain_sector in str(x.get("benchmark") or ""))), None)
    logic_chain = []
    if overseas_lead:
        logic_chain.append({"label": "海外变量", "value": overseas_lead.get("asset"), "evidence": f"5日 {overseas_lead.get('ret_5d')}% · 映射 { '、'.join(overseas_lead.get('targets') or []) } · {overseas_lead.get('state')}", "confidence": "中", "action": "overseas"})
    else:
        logic_chain.append({"label": "海外变量", "value": "暂无可验证海外变量", "evidence": "当前快照没有同时满足价格、映射和相关性条件的海外证据；不把缺失信息当作利多或利空", "confidence": "低", "action": "overseas"})
    news_confidence = "高" if chain_head and float(chain_head.get("trust_score") or 0) >= 80 else "中" if chain_head and float(chain_head.get("trust_score") or 0) >= 60 else "低"
    logic_chain.append({"label": "国内消息", "value": chain_head.get("title") if chain_head else "暂无高价值消息", "evidence": f"时间 {chain_head.get('time')} · 价值 {chain_head.get('value_score')} · 可信度 {chain_head.get('trust_score')}" if chain_head else "暂无真实消息", "confidence": news_confidence, "edge_evidence": "海外与国内消息只按发布时间和产业映射建立候选关系，尚不能确认因果", "action": "news", "url": chain_head.get("url") if chain_head else None})
    fund_sector = chain_sector if chain_sector in sector_map else lead_in
    fund_raw = sector_map.get(fund_sector, {}).get("net_mf_yi") if fund_sector else None
    fund_value = float(fund_raw) if fund_raw is not None and pd.notna(fund_raw) else None
    fund_relation = "与消息映射板块一致" if fund_sector and fund_sector == chain_sector else "与消息映射板块不同，当前仅作市场资金方向参考"
    logic_chain.extend([
        {"label": "影响板块", "value": chain_sector or "未映射", "evidence": f"{chain_head.get('impact_type')} · {chain_head.get('impact_scope')}" if chain_head else "暂无消息映射证据", "confidence": news_confidence if chain_sector else "低", "edge_evidence": "按消息关键词、行业映射和直接/间接影响形成候选板块，不代表业绩已经兑现", "action": "sector", "target": chain_sector},
        {"label": "资金验证", "value": fund_sector or "暂无承接方向", "evidence": f"板块5日净流 {fund_value:.2f}亿 · {fund_relation}" if fund_value is not None else "暂无数据", "confidence": "中" if fund_value is not None and fund_sector == chain_sector else "低", "edge_evidence": f"用{fund_sector or '对应板块'}5日主力净流验证消息映射；{fund_relation}", "action": "sector", "target": fund_sector},
        {"label": "个股/ETF价格", "value": (chain_stock.get("name") if chain_stock is not None else chain_etf.get("name") if chain_etf else "暂无可映射标的"), "evidence": f"个股等权 {mean_ret:.2f}% · 上涨宽度 {breadth:.1f}%" if breadth is not None else "暂无价格证据", "confidence": "中" if chain_stock is not None and breadth is not None else "低", "edge_evidence": f"检查{chain_sector or '对应板块'}龙头/中军、上涨宽度和ETF是否与资金同向", "action": "stock" if chain_stock is not None else "overview", "target": chain_stock.get("ts_code") if chain_stock is not None else None},
        {"label": "验证", "value": "价格与资金初步认可" if chain_head and chain_head.get("market_acceptance") == "价格与资金初步认可" else "尚不能确认因果", "evidence": "下一交易日复核板块资金、龙头/中军与ETF是否同步；时间相关性不等于因果", "confidence": "中" if chain_head and chain_head.get("market_acceptance") == "价格与资金初步认可" else "低", "edge_evidence": "价格、资金、上涨宽度和核心标的至少三项同向才提高置信度", "action": "overview"},
    ])
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
        "恒生科技": ["软件服务", "互联网", "半导体", "电子设备"],
        "纳斯达克中国金龙": ["互联网", "软件服务", "传媒娱乐"],
        "美元指数": ["半导体", "软件服务", "互联网"],
        "离岸人民币": ["半导体", "软件服务", "互联网"],
        "美国国债10年": ["半导体", "软件服务", "互联网"],
        "COMEX黄金": ["黄金"],
        "WTI原油": ["石油", "化工"],
        "COMEX铜": ["铜", "有色"],
        "英伟达": ["半导体", "电子设备", "元件", "通信设备"],
        "博通": ["半导体", "电子设备", "元件", "通信设备"],
        "美光科技": ["半导体", "电子设备", "元件"],
        "特斯拉": ["汽车", "汽车配件", "电气设备"],
        "标普500": [],
        "道琼斯": [],
        "富时中国A50": [],
    }
    transmission_profiles = {
        "费城半导体": ("产业链传导", 1), "中国台湾加权": ("产业链传导", 1), "韩国综合": ("产业链传导", 1),
        "英伟达": ("产业链传导", 1), "博通": ("产业链传导", 1), "美光科技": ("产业链传导", 1), "特斯拉": ("产业链传导", 1),
        "COMEX黄金": ("商品价格传导", 1), "WTI原油": ("商品价格传导", 1), "COMEX铜": ("商品价格传导", 1),
        "美元指数": ("汇率与流动性传导", -1), "离岸人民币": ("汇率传导", -1), "美国国债10年": ("折现率与流动性传导", -1),
        "纳斯达克": ("风险偏好传导", 1), "恒生科技": ("中国资产风险偏好", 1), "纳斯达克中国金龙": ("中国资产风险偏好", 1),
        "富时中国A50": ("A股盘前价格映射", 1), "标普500": ("全球风险偏好", 1), "道琼斯": ("全球风险偏好", 1), "日经225": ("区域产业与风险偏好", 1),
    }
    corr_by_asset = {}
    for item in overseas:
        asset = item.get("asset")
        targets = [s for s in sectors if any(k in str(s.get("industry")) for k in sector_keywords.get(asset, []))]
        target_ret = float(pd.to_numeric(pd.Series([s.get("week_ret") for s in targets]), errors="coerce").mean()) if targets else None
        same_corr = lead_corr = None
        corr_windows = {}
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
            for window in (5, 20, 60):
                part = joined.tail(window)
                if len(part) >= max(5, window // 2):
                    corr_windows[f"same_corr_{window}"] = float(part["overseas"].corr(part["a_market"]))
                    corr_windows[f"lead_corr_{window}"] = float(part["overseas"].shift(1).corr(part["a_market"]))
            corr_by_asset[asset] = corr_windows
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, KeyError):
            pass
        ret5 = item.get("ret_5d")
        channel, relation_sign = transmission_profiles.get(asset, ("相关资产映射", 1))
        broad_target = asset in {"标普500", "道琼斯", "富时中国A50"}
        if target_ret is None and broad_target:
            target_ret = mean_ret
        expected_effect = finite_number(ret5) * relation_sign if finite_number(ret5) is not None else None
        if target_ret is None or expected_effect is None:
            state = "仅有海外价格，暂无对应A股板块验证"
        elif expected_effect < 0 and target_ret < 0:
            state = "海外利空正常传导"
        elif expected_effect < 0 and target_ret >= 0:
            state = "海外利空被A股吸收"
        elif expected_effect > 0 and target_ret <= 0:
            state = "海外利好暂未获A股认可"
        elif abs(finite_number(lead_corr) or 0) < 0.2 and abs(finite_number(same_corr) or 0) < 0.2:
            state = "方向一致但相关性低，暂按A股独立行情观察"
        else:
            state = "海外利好与A股方向一致"
        target_names = [s.get("industry") for s in targets[:5]]
        role_targets = []
        for target_name in target_names[:2]:
            role_targets.extend(sector_roles(target_name)[:2])
        deduped_roles = []
        seen_role_codes = set()
        for role in role_targets:
            if role.get("ts_code") in seen_role_codes:
                continue
            seen_role_codes.add(role.get("ts_code"))
            deduped_roles.append(role)
        matched_etf = None
        for target_name in target_names:
            candidates = matching_etfs(target_name)
            if candidates:
                matched_etf = candidates[0]
                break
        if not matched_etf:
            asset_terms = {"纳斯达克": ["纳指", "纳斯达克"], "恒生科技": ["恒生科技"], "纳斯达克中国金龙": ["中概", "中国互联网"], "富时中国A50": ["A50"], "COMEX黄金": ["黄金"], "WTI原油": ["油气", "石油"], "COMEX铜": ["有色", "铜"]}.get(asset, [])
            matched_etf = next((x for x in etfs if any(term in f"{x.get('name') or ''}|{x.get('benchmark') or ''}" for term in asset_terms)), None)
        correlation_values = [abs(x) for x in [same_corr, lead_corr] if x is not None and math.isfinite(x)]
        confidence = "中" if (target_names or broad_target) and correlation_values else "低"
        if correlation_values and max(correlation_values) >= 0.5 and (target_names or broad_target):
            confidence = "中高"
        overseas_conduction.append({"asset": asset, "group": item.get("group"), "trade_date": item.get("trade_date"), "ret_5d": ret5, "ret_20d": item.get("ret_20d"), "ret_60d": item.get("ret_60d"), "channel": channel, "relation_sign": relation_sign, "expected_effect": expected_effect, "targets": target_names, "target_scope": "A股全市场等权" if broad_target and not target_names else "、".join(target_names) if target_names else "暂无可验证A股映射", "target_ret": target_ret, "same_corr_60": same_corr, "lead_corr_60": lead_corr, "state": state, "confidence": confidence, "role_targets": deduped_roles[:4], "matched_etf": {"name": matched_etf.get("name"), "ts_code": matched_etf.get("ts_code"), "week_ret": matched_etf.get("week_ret"), "share_change_pct": matched_etf.get("share_change_pct"), "premium_discount": matched_etf.get("premium_discount")} if matched_etf else None, "evidence_boundary": "海外与A股按交易日价格、映射板块和滚动相关比较；方向一致不等于因果。", "validation": "下一A股交易日复核映射板块、龙头/中军、匹配ETF和人民币/利率是否继续同向。"})
    for row in overseas_conduction:
        row.update(corr_by_asset.get(row.get("asset"), {}))
    high_low_switch = {
        "available": False,
        "definition": "每日先用此前5个交易日板块等权收益排序，前25%为高位组、后25%为低位组；再观察当日收益和资金，不使用当日数据分组。",
        "series": [],
        "windows": [],
    }
    if not price_frame.empty and {"ts_code", "trade_date", "pct_chg"}.issubset(price_frame.columns):
        switch_prices = price_frame[["ts_code", "trade_date", "pct_chg"]].copy()
        switch_prices["trade_date"] = switch_prices["trade_date"].astype(str)
        switch_prices["pct_chg"] = pd.to_numeric(switch_prices["pct_chg"], errors="coerce")
        switch_prices = switch_prices.merge(stocks_frame[["ts_code", "industry"]].drop_duplicates("ts_code"), on="ts_code", how="left")
        sector_return = switch_prices.dropna(subset=["industry", "pct_chg"]).groupby(["trade_date", "industry"])["pct_chg"].mean().unstack()
        switch_flow = pd.DataFrame(flows)
        if not switch_flow.empty:
            switch_flow["trade_date"] = switch_flow["trade_date"].astype(str)
            switch_flow["net_mf_yi"] = pd.to_numeric(switch_flow["net_mf_yi"], errors="coerce")
            sector_flow_pivot = switch_flow.pivot_table(index="trade_date", columns="industry", values="net_mf_yi", aggfunc="sum")
        else:
            sector_flow_pivot = pd.DataFrame()
        switch_rows = []
        latest_high = []
        latest_low = []
        sector_return = sector_return.sort_index()
        for pos in range(5, len(sector_return)):
            date = str(sector_return.index[pos])
            prior_momentum = sector_return.iloc[pos - 5:pos].sum(min_count=3).dropna().sort_values()
            if len(prior_momentum) < 8:
                continue
            group_size = max(2, len(prior_momentum) // 4)
            low_names = prior_momentum.head(group_size).index.tolist()
            high_names = prior_momentum.tail(group_size).index.tolist()
            current_ret = sector_return.iloc[pos]
            high_ret = pd.to_numeric(current_ret.reindex(high_names), errors="coerce").mean()
            low_ret = pd.to_numeric(current_ret.reindex(low_names), errors="coerce").mean()
            flow_row = sector_flow_pivot.loc[date] if date in sector_flow_pivot.index else pd.Series(dtype=float)
            high_flow = pd.to_numeric(flow_row.reindex(high_names), errors="coerce").sum(min_count=1)
            low_flow = pd.to_numeric(flow_row.reindex(low_names), errors="coerce").sum(min_count=1)
            switch_rows.append({
                "trade_date": date,
                "high_ret": round(float(high_ret), 4) if pd.notna(high_ret) else None,
                "low_ret": round(float(low_ret), 4) if pd.notna(low_ret) else None,
                "ret_spread": round(float(low_ret - high_ret), 4) if pd.notna(high_ret) and pd.notna(low_ret) else None,
                "high_flow": round(float(high_flow), 2) if pd.notna(high_flow) else None,
                "low_flow": round(float(low_flow), 2) if pd.notna(low_flow) else None,
            })
            latest_high, latest_low = high_names, low_names
        switch_frame = pd.DataFrame(switch_rows)
        if not switch_frame.empty:
            def safe_corr(left, right):
                pair = pd.concat([left, right], axis=1).dropna()
                return float(pair.iloc[:, 0].corr(pair.iloc[:, 1])) if len(pair) >= 5 and pair.iloc[:, 0].nunique() > 1 and pair.iloc[:, 1].nunique() > 1 else None
            windows = []
            for window in (5, 20, 60):
                part = switch_frame.tail(window)
                enough = len(part) >= window
                windows.append({
                    "window": window,
                    "available": enough,
                    "samples": int(len(part)),
                    "return_corr": round(safe_corr(part["high_ret"], part["low_ret"]), 3) if enough and safe_corr(part["high_ret"], part["low_ret"]) is not None else None,
                    "fund_corr": round(safe_corr(part["high_flow"], part["low_flow"]), 3) if enough and safe_corr(part["high_flow"], part["low_flow"]) is not None else None,
                    "high_leads_low": round(safe_corr(part["high_ret"].shift(1), part["low_ret"]), 3) if enough and safe_corr(part["high_ret"].shift(1), part["low_ret"]) is not None else None,
                    "low_leads_high": round(safe_corr(part["low_ret"].shift(1), part["high_ret"]), 3) if enough and safe_corr(part["low_ret"].shift(1), part["high_ret"]) is not None else None,
                })
            signs = switch_frame["ret_spread"].dropna().map(lambda value: 1 if value > 0 else -1 if value < 0 else 0).tolist()
            duration = 0
            if signs and signs[-1] != 0:
                for sign in reversed(signs):
                    if sign == signs[-1]:
                        duration += 1
                    else:
                        break
            latest = switch_rows[-1]
            state = "低位组占优" if (latest.get("ret_spread") or 0) > 0 else "高位组占优" if (latest.get("ret_spread") or 0) < 0 else "高低位均衡"
            high_low_switch = {
                "available": True,
                "definition": high_low_switch["definition"],
                "series": switch_rows,
                "windows": windows,
                "state": state,
                "duration": duration,
                "latest_date": latest.get("trade_date"),
                "high_group": latest_high,
                "low_group": latest_low,
                "confidence": "中" if len(switch_rows) >= 20 else "低",
                "validation": "下一交易日继续用前一日已知分组，检查收益差和资金差是否同向延续；相关性不代表资金逐笔迁移。",
            }
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
            latest_rank = latest.rank(method="min", ascending=False)
            previous_rank = previous.rank(method="min", ascending=False)
            for industry in set(latest.index) | set(previous.index):
                before = float(previous.get(industry, 0) or 0)
                after = float(latest.get(industry, 0) or 0)
                if (before < 0 <= after) or (before > 0 >= after) or abs(after - before) >= 30:
                    changes.append({
                        "time": latest_date,
                        "category": "资金方向变化",
                        "title": f"{industry}资金由{'流出转为流入' if before < 0 <= after else '流入转为流出' if before > 0 >= after else '快速变化'}",
                        "before": round(before, 2),
                        "after": round(after, 2),
                        "before_text": f"前日 {before:+.2f}亿",
                        "after_text": f"当日 {after:+.2f}亿",
                        "meaning": "关注回流是否由龙头和中军共同确认" if after > before else "警惕冲高兑现和板块内部扩散变弱",
                        "confidence": "中" if abs(after - before) >= 60 else "低",
                        "validation": "下一交易日观察资金方向与板块涨跌是否同步",
                    })
                old_rank = previous_rank.get(industry)
                new_rank = latest_rank.get(industry)
                if pd.notna(old_rank) and pd.notna(new_rank) and float(old_rank) - float(new_rank) >= 10:
                    changes.append({
                        "time": latest_date,
                        "category": "板块资金排名快速上升",
                        "title": f"{industry}资金排名由第{int(old_rank)}升至第{int(new_rank)}",
                        "before": float(old_rank),
                        "after": float(new_rank),
                        "before_text": f"前一交易日第 {int(old_rank)} 名",
                        "after_text": f"当前第 {int(new_rank)} 名",
                        "meaning": "资金关注度短期明显提升，但排名改善不等于趋势已经形成，需检查价格、成交额和核心标的是否同步。",
                        "confidence": "中" if after > 0 else "低",
                        "validation": "下一交易日继续位于资金前列，且板块价格、上涨宽度、龙头和中军至少三项同向。",
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
    if not price_review.empty and {"trade_date", "ts_code", "pct_chg"}.issubset(price_review.columns):
        latest_price_date = str(price_review["trade_date"].astype(str).max())
        latest_snapshot = price_review[price_review["trade_date"].astype(str) == latest_price_date].copy()
        latest_snapshot["pct_chg"] = pd.to_numeric(latest_snapshot["pct_chg"], errors="coerce")
        latest_snapshot = latest_snapshot.merge(stocks_frame[["ts_code", "industry"]].drop_duplicates("ts_code"), on="ts_code", how="left")
        latest_breadth = float((latest_snapshot["pct_chg"].dropna() > 0).mean() * 100) if latest_snapshot["pct_chg"].notna().any() else None
        if index_day_ret is not None and latest_breadth is not None and ((index_day_ret > 0.5 and latest_breadth < 45) or (index_day_ret < -0.5 and latest_breadth > 55)):
            changes.append({
                "time": latest_price_date,
                "category": "指数与个股背离",
                "title": "代表指数与个股上涨宽度方向背离",
                "before": index_day_ret,
                "after": latest_breadth,
                "before_text": f"指数 {index_day_ret:+.2f}%",
                "after_text": f"上涨宽度 {latest_breadth:.1f}%",
                "meaning": "指数上涨但多数个股未跟随时偏权重护盘；指数下跌而多数个股上涨时说明局部风险偏好更强。",
                "confidence": "中",
                "validation": "检查主要宽基指数、成交额和上涨中位数是否连续两个交易日保持背离。",
            })
        by_code_ret = latest_snapshot.set_index("ts_code")["pct_chg"]
        for sector in sectors:
            industry = str(sector.get("industry"))
            sector_ret = daily_sector_returns.get(industry)
            sector_flow = latest_sector_flow.get(industry)
            if sector_ret is not None and sector_flow is not None and ((sector_ret >= 1 and sector_flow < 0) or (sector_ret <= -1 and sector_flow > 0)):
                changes.append({
                    "time": latest_price_date,
                    "category": "价格与资金背离",
                    "title": f"{industry}价格与资金方向相反",
                    "before": sector_ret,
                    "after": sector_flow,
                    "before_text": f"板块 {sector_ret:+.2f}%",
                    "after_text": f"资金 {sector_flow:+.2f}亿",
                    "meaning": "上涨流出需警惕冲高兑现；下跌流入可能是承接，也可能是抄底尚未被价格确认。",
                    "confidence": "中",
                    "validation": "下一交易日观察价格与资金是否重新同向，并检查龙头和中军是否确认。",
                })
            leader_code = sector.get("leader_code")
            leader_ret = by_code_ret.get(leader_code) if leader_code in by_code_ret.index else None
            if sector_ret is not None and leader_ret is not None and pd.notna(leader_ret) and abs(float(leader_ret) - float(sector_ret)) >= 3:
                changes.append({
                    "time": latest_price_date,
                    "category": "龙头与板块背离",
                    "title": f"{industry}龙头与板块强弱背离",
                    "before": float(leader_ret),
                    "after": float(sector_ret),
                    "before_text": f"{sector.get('leader_name') or '龙头候选'} {float(leader_ret):+.2f}%",
                    "after_text": f"板块 {float(sector_ret):+.2f}%",
                    "meaning": "龙头明显弱于板块可能表示核心地位松动；龙头独强则需警惕板块跟随不足。",
                    "confidence": "中",
                    "validation": "比较下一交易日龙头、中军和板块上涨宽度是否重新同步。",
                })
        if agent_series:
            agent_latest_date = max(str(x.get("trade_date")) for x in agent_series)
            agent_latest = {x.get("name"): x.get("value") for x in agent_series if str(x.get("trade_date")) == agent_latest_date}
            institution = agent_latest.get("机构代理")
            retail = agent_latest.get("散户代理")
            if institution is not None and retail is not None and float(institution) * float(retail) < 0:
                changes.append({
                    "time": agent_latest_date,
                    "category": "机构与散户方向相反",
                    "title": "机构代理与散户代理方向相反",
                    "before": float(institution),
                    "after": float(retail),
                    "before_text": f"机构代理 {float(institution):+.2f}亿",
                    "after_text": f"散户代理 {float(retail):+.2f}亿",
                    "meaning": "不同订单规模和股票特征的资金方向分化，说明市场参与者并未形成一致预期。",
                    "confidence": "低",
                    "validation": "连续观察至少三个交易日，并结合大市值/小市值相对收益确认；代理口径不等于真实账户。",
                })
        for brief in news_briefs:
            industry = brief.get("industry")
            if brief.get("direction") != "偏利好" or not industry or industry == "宏观政策":
                continue
            sector_ret = daily_sector_returns.get(industry)
            sector_flow = latest_sector_flow.get(industry)
            if (sector_ret is not None and sector_ret <= 0) or (sector_flow is not None and sector_flow < 0):
                changes.append({
                    "time": brief.get("time") or latest_price_date,
                    "category": "利好与价格背离",
                    "title": f"{industry}利好尚未获得价格和资金共同确认",
                    "before": float(sector_ret) if sector_ret is not None else None,
                    "after": float(sector_flow) if sector_flow is not None else None,
                    "before_text": f"板块 {float(sector_ret):+.2f}%" if sector_ret is not None else "板块价格缺失",
                    "after_text": f"资金 {float(sector_flow):+.2f}亿" if sector_flow is not None else "板块资金缺失",
                    "meaning": "利好可能已提前交易、映射错误或不足以改变原有趋势，不能仅凭标题追涨。",
                    "confidence": "中" if sector_ret is not None and sector_flow is not None else "低",
                    "validation": "打开消息原文，并观察下一交易日板块、龙头/中军与对应ETF是否共同转强。",
                })
    news_history_path = ROOT / "data" / "news_validation_history.json"
    try:
        news_history = json.loads(news_history_path.read_text(encoding="utf-8")) if news_history_path.exists() else {}
        news_history = news_history if isinstance(news_history, dict) else {}
    except (OSError, json.JSONDecodeError):
        news_history = {}
    validation_trade_date = max([str(x.get("trade_date")) for x in prices if x.get("trade_date")], default=latest_flow_date)
    for brief in news_briefs:
        event_key = f"{brief.get('time') or ''}|{brief.get('title') or ''}"
        event_id = hashlib.sha1(event_key.encode("utf-8")).hexdigest()[:14]
        industry = brief.get("industry")
        sector_ret = daily_sector_returns.get(industry)
        sector_flow = latest_sector_flow.get(industry)
        linked_etf = next((item for item in etfs if item.get("name") == brief.get("affected_etf") or item.get("ts_code") == brief.get("affected_etf")), None)
        validation_scope = industry
        if brief.get("is_macro") is True or industry == "宏观政策":
            valid_market_returns = [float(value) for value in daily_sector_returns.values() if value is not None]
            sector_ret = sum(valid_market_returns) / len(valid_market_returns) if valid_market_returns else None
            sector_flow = sum(float(value) for value in latest_sector_flow.values()) if latest_sector_flow else None
            linked_etf = linked_etf or next((item for item in etfs if item.get("tool_role") == "宽基工具"), None)
            validation_scope = "市场等权与宽基ETF"
        direction = brief.get("direction")
        if direction == "偏利好" and sector_ret is not None and sector_flow is not None:
            validation_status = "价格与资金初步认可" if sector_ret > 0 and sector_flow > 0 else "利好未获共同认可" if sector_ret <= 0 or sector_flow < 0 else "待验证"
        elif direction == "偏利空" and sector_ret is not None and sector_flow is not None:
            validation_status = "利空已正常传导" if sector_ret < 0 and sector_flow < 0 else "利空被吸收/尚未传导" if sector_ret >= 0 else "待验证"
        else:
            validation_status = "证据不足，继续观察"
        observation = {
            "trade_date": validation_trade_date,
            "industry": industry,
            "scope": validation_scope,
            "sector_ret": round(float(sector_ret), 3) if sector_ret is not None else None,
            "sector_flow": round(float(sector_flow), 3) if sector_flow is not None else None,
            "etf": linked_etf.get("name") if linked_etf else None,
            "etf_ret": linked_etf.get("week_ret") if linked_etf else None,
            "etf_premium": linked_etf.get("premium_discount") if linked_etf else None,
            "status": validation_status,
            "confidence": "中" if sector_ret is not None and sector_flow is not None else "低",
        }
        record = news_history.get(event_id, {"id": event_id, "title": brief.get("title"), "time": brief.get("time"), "observations": []})
        observations = record.get("observations") if isinstance(record.get("observations"), list) else []
        observations = [item for item in observations if str(item.get("trade_date")) != str(validation_trade_date)]
        observations.append(observation)
        record.update({"title": brief.get("title"), "time": brief.get("time"), "last_status": validation_status, "observations": observations[-20:]})
        news_history[event_id] = record
        brief.update({"event_id": event_id, "validation_status": validation_status, "validation_history": observations[-10:]})
    try:
        ordered_news_history = dict(sorted(news_history.items(), key=lambda item: str(item[1].get("time") or ""), reverse=True)[:300])
        news_history_path.write_text(json.dumps(ordered_news_history, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    for etf in etfs:
        etf_return = etf.get("week_ret")
        basket_return = etf.get("basket_week_ret")
        coverage = float(etf.get("basket_return_coverage") or 0)
        if etf.get("return_reliable") is False or etf_return is None or basket_return is None or coverage < 60:
            continue
        divergence = float(etf_return) - float(basket_return)
        etf["component_divergence"] = round(divergence, 3)
        if abs(divergence) < 1.5:
            continue
        changes.append({
            "time": etf.get("trade_date") or latest_flow_date,
            "category": "ETF与成分股背离",
            "title": f"{etf.get('name') or etf.get('ts_code')}与A股篮子收益背离",
            "before": float(basket_return),
            "after": float(etf_return),
            "before_text": f"A股篮子估算 {float(basket_return):+.2f}%",
            "after_text": f"ETF复权收益 {float(etf_return):+.2f}%",
            "meaning": "ETF价格与可计算A股篮子表现不同步，可能来自海外/港股成分、现金替代、净值时差、申赎供需或跟踪误差，不能直接解释为套利空间。",
            "confidence": "中" if coverage >= 80 else "低",
            "validation": f"成分收益权重覆盖 {coverage:.1f}%；核对净值日期、溢折价、非A股成分和下一交易日价差是否收敛。",
        })
    changes = sorted(changes, key=lambda x: abs(float(x.get("after") or 0) - float(x.get("before") or 0)), reverse=True)[:12]
    change_history_path = ROOT / "data" / "change_history.json"
    try:
        change_history = json.loads(change_history_path.read_text(encoding="utf-8")) if change_history_path.exists() else []
        change_history = change_history if isinstance(change_history, list) else []
    except (OSError, json.JSONDecodeError):
        change_history = []
    latest_change_date = max([str(x.get("time") or "")[:8] for x in changes], default="")
    history_by_id = {str(item.get("id")): item for item in change_history if item.get("id")}
    active_change_ids = set()
    for change in changes:
        identity = f"{change.get('category') or ''}|{change.get('title') or ''}"
        change_id = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
        active_change_ids.add(change_id)
        prior = history_by_id.get(change_id, {})
        same_observation = str(prior.get("last_seen") or "") == str(change.get("time") or "")
        record = {
            "id": change_id,
            "category": change.get("category"),
            "title": change.get("title"),
            "first_seen": prior.get("first_seen") or change.get("time") or generated_at,
            "last_seen": change.get("time") or generated_at,
            "occurrences": int(prior.get("occurrences") or 0) + (0 if same_observation else 1),
            "status": "当前存在",
            "latest": change,
        }
        history_by_id[change_id] = record
        change.update({"id": change_id, "first_seen": record["first_seen"], "occurrences": record["occurrences"], "history_status": record["status"]})
    for change_id, record in history_by_id.items():
        if change_id not in active_change_ids and latest_change_date and str(record.get("last_seen") or "")[:8] < latest_change_date:
            record["status"] = "已解除/未再触发"
    change_history = sorted(history_by_id.values(), key=lambda item: str(item.get("last_seen") or ""), reverse=True)[:120]
    try:
        change_history_path.write_text(json.dumps(change_history, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
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
        "intraday": intraday,
        "estimated": True,
        "sentiment_score": sentiment,
        "money_effect_score": money_effect,
        "valuation_median_pe": median_pe,
        "valuation_percentile": pe_percentile,
        "valuation_coverage": len(pe),
        "etf_count": len(etfs),
        "etf_quality_excluded": sum(1 for x in etfs if x.get("return_reliable") is False),
        "etf_window": sorted({str(x.get("trade_date")) for x in etfs if x.get("trade_date")}),
        "active_concepts": concepts,
        "concept_meta": concept_meta,
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
        "trade_plan": trade_plan,
        "position": round(min(80, max(20, money_effect * 0.7)), 0),
        "confidence": "中",
        "validation": f"验证点：观察 {lead_in or '最强承接方向'} 次日是否继续净流入，并确认龙头、中军与板块同步。",
        "invalidation": "失效条件：资金广度转负、最强板块跌破前一交易日低点，或利好方向出现放量冲高回落。",
        "news_briefs": news_briefs,
        "logic_chain": logic_chain,
        "market_flow_series": market_flow_series,
        "rotation_timeline": rotation_timeline,
        "high_low_switch": high_low_switch,
        "market_price_series": market_price_series,
        "changes": changes,
        "change_history": change_history,
        "overseas_conduction": overseas_conduction,
        "flow_periods": [{"period": "5分钟", "available": False, "reason": "当前授权接口未提供分时资金明细"}, {"period": "15分钟", "available": False, "reason": "当前授权接口未提供分时资金明细"}, {"period": "30分钟", "available": False, "reason": "当前授权接口未提供分时资金明细"}, {"period": "当日", "available": bool(market_flow_series), "reason": "按板块日级主力净流合计"}, {"period": "3日", "available": len(market_flow_series) >= 3, "reason": "按最近可用交易日合计"}, {"period": "5日", "available": len(market_flow_series) >= 5, "reason": "按最近可用交易日合计"}, {"period": "20日", "available": len(market_flow_series) >= 20, "reason": "按最近20个可用交易日合计" if len(market_flow_series) >= 20 else f"当前仅有{len(market_flow_series)}个交易日资金明细"}],
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
    template = template.replace("slice(0,2)||'other'", "slice(0,4)||'other'")
    template = template.replace(".stock-side{padding:14px}", ".stock-side{padding:14px;height:calc(100% - 39px);overflow:auto}")
    template = template.replace("强势延续", "强势板块延续").replace("潜在轮入", "潜在轮动方向").replace("上涨分歧", "上涨但资金背离")
    template = template.replace("前十大权重", "前十大估算权重").replace("行业暴露前五", "A股篮子行业暴露")
    template = template.replace("${escHtml(x.basis||'')} · 覆盖 ${fmt(x.coverage,0)} 只", "${escHtml(x.basis||'')} · ${escHtml(x.window||'可用窗口')} · 覆盖 ${fmt(x.coverage,0)} 只")
    stock_history = {}
    for row in prices:
        code = str(row.get("ts_code") or "")
        if not code:
            continue
        item = stock_history.setdefault(code, {"p": [], "f": [], "a": []})
        item["p"].append([row.get("trade_date"), row.get("open"), row.get("high"), row.get("low"), row.get("close"), row.get("vol"), row.get("amount"), row.get("pct_chg")])
    for row in stock_flows:
        code = str(row.get("ts_code") or "")
        if not code:
            continue
        stock_history.setdefault(code, {"p": [], "f": [], "a": []})["f"].append([row.get("trade_date"), row.get("net_mf_yi")])
    for stock in stocks:
        code = str(stock.get("ts_code") or "")
        item = stock_history.setdefault(code, {"p": [], "f": [], "a": []})
        item["a"] = [[row.get("trade_date"), row.get("name"), row.get("value")] for row in stock.get("agent_series", [])]
        stock.pop("agent_series", None)
    history_shards = {}
    for code, item in stock_history.items():
        digits = "".join(ch for ch in code if ch.isdigit())
        shard = digits[:4] or "other"
        history_shards.setdefault(shard, {})[code] = item
    display_etfs = [x for x in etfs if x.get("tool_role") != "主题待确认" and x.get("return_reliable") is not False and float(x.get("tool_relevance_score") or 0) >= 45][:24]
    replacements = {
        "__SECTORS__": json.dumps(sectors, ensure_ascii=False, separators=(",", ":")),
        "__STOCKS__": json.dumps(stocks, ensure_ascii=False, separators=(",", ":")),
        "__FLOWS__": json.dumps(flows, ensure_ascii=False, separators=(",", ":")),
        "__PRICES__": "[]",
        "__SUMMARY__": json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        "__NEWS__": json.dumps(news, ensure_ascii=False, separators=(",", ":")),
        "__ETFS__": json.dumps(display_etfs, ensure_ascii=False, separators=(",", ":")),
        "__STOCK_FLOWS__": "[]",
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    stock_agent_ui = r'''const stockAgentAnchor=document.querySelector('#stockView .stock-evidence');if(stockAgentAnchor&&!document.querySelector('#stockAgentChart')){const panel=document.createElement('section');panel.className='panel stock-agent-evidence';panel.innerHTML='<div class="head">个股四类代理资金 <small>按订单规模与股票特征回算；估算，不代表账户归属</small></div><div id="stockAgentChart" class="chart"></div>';stockAgentAnchor.parentNode.insertBefore(panel,stockAgentAnchor.nextSibling);const chart=echarts.init(document.querySelector('#stockAgentChart'));const names=['国家队代理','机构代理','游资代理','散户代理'],colors=[C.gold,C.cyan,C.up,C.down];const render=()=>{const rows=selectedStock?.agent_series||[],dates=[...new Set(rows.map(x=>String(x.trade_date)))].sort();chart.setOption({animation:false,tooltip:{...tooltip,trigger:'axis'},legend:{data:names,textStyle:{color:C.muted}},grid:{left:58,right:18,top:35,bottom:32},xAxis:{type:'category',data:dates.map(shortDate),axisLabel:{color:C.muted}},yAxis:{name:'亿元',axisLabel:{color:C.muted},splitLine:{lineStyle:{color:'#24303a'}}},series:names.map((name,i)=>({name,type:'line',showSymbol:false,smooth:true,data:dates.map(d=>{const x=rows.find(v=>String(v.trade_date)===d&&v.name===name);return x?x.value:null}),lineStyle:{color:colors[i]},itemStyle:{color:colors[i]}}))},true)};const prior=renderStock;renderStock=function(){prior();render()};render()}const stockAgentStyle=document.createElement('style');stockAgentStyle.textContent='.stock-agent-evidence{height:300px;margin-top:7px}.stock-agent-evidence .chart{height:calc(100% - 39px)}';document.head.appendChild(stockAgentStyle)
'''
    valuation_ui = r'''const priorValuationRange=typeof updateValuationRange==='function'?updateValuationRange:null;if(priorValuationRange){updateValuationRange=function(){priorValuationRange();const box=document.querySelector('#valuationOverview');if(!box)return;let note=box.querySelector('.valuation-assumption-note');if(!note){note=document.createElement('div');note.className='valuation-assumption-note source';box.appendChild(note)}const names=typeof valuationModels==='function'?valuationModels().map(x=>x.name).join('、'):'';note.textContent=`估值解释：仅使用真实正值输入形成 ${names||'暂无可用'} 模型；中性参考价、悲观和乐观边界分别取可用模型对应情景的中位数，不使用固定回退倍数，也不让单一极端模型直接决定区间。当前假设：PE ${document.querySelector('#vPeBase')?.value||'—'} 倍、PB ${document.querySelector('#vPbBase')?.value||'—'} 倍、FCFE增长 ${document.querySelector('#vGrowth')?.value||'—'}%、折现率 ${document.querySelector('#vDiscount')?.value||'—'}%、永续增长 ${document.querySelector('#vTerminal')?.value||'—'}%。财务覆盖率 ${fmt(selectedStock?.fundamental_coverage,0)}%；结果只作区间观察，不是目标价。`};updateValuationRange();document.querySelectorAll('.valuation-form input').forEach(i=>i.addEventListener('input',()=>setTimeout(updateValuationRange,0)))}const valuationStyle=document.createElement('style');valuationStyle.textContent='.valuation-assumption-note{grid-column:1/-1;padding:8px;background:var(--panel2);line-height:1.6}';document.head.appendChild(valuationStyle)
'''
    template = template.replace("const sectorAgentBox", stock_agent_ui + "const sectorAgentBox")
    etf_share_ui = r'''const etfShareBox=document.querySelector('#etfBoard');if(etfShareBox&&!document.querySelector('#etfShareEvidence')){const rows=etfs.filter(x=>x.share_change_pct!==null&&x.share_change_pct!==undefined).slice(0,8),excluded=Number(summary.etf_quality_excluded||0);etfShareBox.insertAdjacentHTML('beforeend',`<div id="etfShareEvidence" class="source etf-share-evidence"><b>基金份额变化</b>：${rows.length?rows.map(x=>`${escHtml(x.name||x.ts_code)} ${fmt(x.share_change_pct,2)}%`).join(' · '):'接口未返回基金份额变化，无法估算申赎方向'}。该指标是基金份额快照变化，不等于纯申购赎回金额；需结合净值、价格和成交额判断。${excluded?` 已剔除 ${excluded} 只疑似份额折算/拆分且缺少复权依据的异常样本。`:''}</div>`);const style=document.createElement('style');style.textContent='.etf-share-evidence{padding:8px;background:var(--panel2);line-height:1.6}.etf-share-evidence b{color:var(--gold)}';document.head.appendChild(style)}
'''
    news_ui = r'''const impactMapSection=document.querySelector('.impact-map'),newsView=document.querySelector('#newsView');if(impactMapSection&&newsView&&impactMapSection.parentElement!==newsView)newsView.appendChild(impactMapSection);const impactMap=document.querySelector('#newsImpactMap');if(impactMap&&!document.querySelector('#impactNewsSwitcher')){const sw=document.createElement('div');sw.id='impactNewsSwitcher';sw.className='impact-news-switcher';const rows=(summary.news_briefs||news).slice().sort((a,b)=>Number(b.value_score||0)-Number(a.value_score||0)).slice(0,12);sw.innerHTML='<button class="btn active" data-impact-index="all">全部消息</button>'+rows.slice(0,3).map((x,i)=>`<button class="btn" data-impact-index="${i}">消息${i+1}</button>`).join('');impactMap.parentNode.insertBefore(sw,impactMap);const apply=i=>{impactMap.querySelectorAll('.impact-item').forEach((el,n)=>el.style.display=i===null||n===i?'':'none');sw.querySelectorAll('button').forEach((b,n)=>b.classList.toggle('active',(i===null&&n===0)||(i!==null&&n===i+1)))};sw.querySelectorAll('button').forEach(b=>b.onclick=()=>apply(b.dataset.impactIndex==='all'?null:Number(b.dataset.impactIndex)))}const impactSwitchStyle=document.createElement('style');impactSwitchStyle.textContent='.impact-news-switcher{display:flex;gap:5px;padding:8px 9px;border:1px solid var(--line);border-bottom:0;background:var(--panel);overflow:auto}.impact-news-switcher .btn{white-space:nowrap}';document.head.appendChild(impactSwitchStyle);
'''
    news_switch_ui = r'''const impactRowsForSwitch=(summary.news_briefs||news).slice().sort((a,b)=>Number(b.value_score||0)-Number(a.value_score||0)).slice(0,12);document.querySelectorAll('#newsImpactMap .impact-item').forEach((el,i)=>{const x=impactRowsForSwitch[i];if(x?.is_macro)el.insertAdjacentHTML('afterbegin',`<small class="macro-badge">宏观类别：${escHtml(x.macro_category||'宏观数据')} · 受益情景：${escHtml(x.macro_benefit_scenarios||'需验证')} · 风险情景：${escHtml(x.macro_risk_scenarios||'需验证')}</small>`)});document.querySelectorAll('#topNewsList .news-brief').forEach((el,i)=>{el.addEventListener('click',e=>{if(e.target.closest('a'))return;showView('newsView');const b=document.querySelector(`#impactNewsSwitcher button[data-impact-index="${i}"]`);b?.click()})});const macroBadgeStyle=document.createElement('style');macroBadgeStyle.textContent='.macro-badge{color:var(--gold)!important;border-left:2px solid var(--gold);padding-left:6px}';document.head.appendChild(macroBadgeStyle)
'''
    overseas_corr_ui = r'''document.querySelectorAll('#overseasList .overseas-item').forEach((el,i)=>{const x=(summary.overseas_conduction||[])[i];if(!x)return;const relation=x.relation_sign===-1?'海外变量上升通常对应A股映射方向承压':'海外变量与A股映射方向按同向观察',etf=x.matched_etf,roles=x.role_targets||[];el.insertAdjacentHTML('beforeend',`<small class="overseas-channel-summary">${escHtml(x.channel||'相关资产映射')} · ${escHtml(x.target_scope||'暂无A股验证对象')}</small><button class="overseas-toggle" type="button">展开传导证据</button><div class="overseas-evidence" hidden><small><b>传导通道</b>${escHtml(x.channel||'相关资产映射')} · ${escHtml(relation)}</small><small><b>A股验证对象</b>${escHtml(x.target_scope||'暂无可验证映射')} · 窗口涨跌 ${fmt(x.target_ret)}%</small><small><b>滚动同步相关</b>5日 ${fmt(x.same_corr_5,2)} · 20日 ${fmt(x.same_corr_20,2)} · 60日 ${fmt(x.same_corr_60,2)}</small><small><b>海外领先1日相关</b>5日 ${fmt(x.lead_corr_5,2)} · 20日 ${fmt(x.lead_corr_20,2)} · 60日 ${fmt(x.lead_corr_60,2)}</small><div class="overseas-targets">${roles.map(v=>`<button data-overseas-stock="${escHtml(v.ts_code||'')}">${escHtml(v.name||v.ts_code)}<span>${escHtml(v.tier||'观察')} · ${fmt(v.week_ret)}%</span></button>`).join('')||'<span>暂无可验证龙头/中军映射</span>'}</div><small><b>匹配ETF</b>${etf?`${escHtml(etf.name||etf.ts_code)} · ${fmt(etf.week_ret)}% · 份额 ${fmt(etf.share_change_pct)}% · 溢折价 ${fmt(etf.premium_discount)}%`:'未找到名称、基准或行业暴露明确匹配的ETF'}</small><small><b>下一步验证</b>${escHtml(x.validation||'等待下一A股交易日验证')}</small><small class="source">${escHtml(x.evidence_boundary||'方向一致不等于因果')}</small></div>`);const toggle=el.querySelector('.overseas-toggle'),evidence=el.querySelector('.overseas-evidence');toggle.onclick=()=>{evidence.hidden=!evidence.hidden;toggle.textContent=evidence.hidden?'展开传导证据':'收起传导证据'};el.querySelectorAll('[data-overseas-stock]').forEach(b=>b.onclick=()=>b.dataset.overseasStock&&selectStock(b.dataset.overseasStock,true))});const overseasEvidenceStyle=document.createElement('style');overseasEvidenceStyle.textContent='.overseas-channel-summary{color:var(--cyan)!important}.overseas-toggle{margin-top:6px;border:1px solid var(--line);background:#0b1218;color:var(--gold);padding:5px 7px;cursor:pointer;font:inherit;font-size:10px}.overseas-evidence{margin-top:7px;padding-top:6px;border-top:1px solid var(--line)}.overseas-evidence[hidden]{display:none}.overseas-evidence small b{display:inline;color:var(--gold);margin-right:5px}.overseas-targets{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:3px;margin:5px 0}.overseas-targets button{border:1px solid var(--line);background:#0b1218;color:var(--text);padding:5px;text-align:left;cursor:pointer}.overseas-targets button span{display:block;color:var(--muted);font-size:9px}.overseas-targets>span{color:var(--muted);font-size:10px}@media(max-width:600px){.overseas-targets{grid-template-columns:1fr}}';document.head.appendChild(overseasEvidenceStyle);
'''
    news_detail_ui = r'''const topNewsRows=(summary.news_briefs||[]);document.querySelectorAll('#topNewsList .news-brief').forEach((el,i)=>{const x=topNewsRows[i];if(!x)return;el.insertAdjacentHTML('beforeend',`<small>影响范围：${escHtml(x.impact_scope||'暂无')} · 市场认可：${escHtml(x.market_acceptance||'待验证')}</small><small>受益情景：${escHtml(x.is_macro?x.macro_benefit_scenarios:x.benefit_scenarios||'待验证')}</small><small>风险情景：${escHtml(x.is_macro?x.macro_risk_scenarios:x.risk_scenarios||'待验证')}</small><small>观察对象：${escHtml(x.affected_stock||'暂无个股')} · ${escHtml(x.affected_etf||'暂无ETF')}</small>`)});
'''
    news_objects_ui = r'''const newsObjectRows=summary.news_briefs||[];document.querySelectorAll('#topNewsList .news-brief').forEach((el,i)=>{const x=newsObjectRows[i];if(!x||x.is_macro)return;el.insertAdjacentHTML('beforeend',`<small>受益对象：${escHtml(x.beneficiary_objects||'暂无可验证映射')}</small><small>受损对象：${escHtml(x.harmed_objects||'暂无可验证映射')} · 仅为情景对象</small>`)});
'''
    news_chain_v2_ui = r'''
const impactChainRowsV2=(summary.news_briefs||news).slice().sort((a,b)=>Number(b.value_score||0)-Number(a.value_score||0)).slice(0,12);
document.querySelectorAll('#newsImpactMap .impact-item').forEach((item,index)=>{
  const x=impactChainRowsV2[index],stages=x?.impact_chain_v2||[];if(!x||!stages.length)return;
  item.querySelector('.impact-chain-detail')?.remove();
  const box=document.createElement('div');box.className='impact-chain-workbench';
  const matchText=v=>v===true?'同向':v===false?'背离':'待验证',matchClass=v=>v===true?'up':v===false?'down':'muted';
  const targets=[...(x.asset_tiers?.direct||[]),...(x.asset_tiers?.business_related||[])];
  const renderStage=stageIndex=>{
    const stage=stages[stageIndex]||stages[0],isAsset=stageIndex===3,isValidation=stageIndex===4;
    box.querySelectorAll('.impact-stage-button').forEach((b,i)=>b.classList.toggle('active',i===stageIndex));
    let extra='';
    if(isAsset){extra=`<div class="impact-target-groups"><div><b>直接影响</b>${(x.asset_tiers?.direct||[]).map(v=>`<button data-chain-stock="${escHtml(v.ts_code||'')}">${escHtml(v.name||v.ts_code)}<small>${escHtml(v.relation||'公告主体')}</small></button>`).join('')||'<span>没有可验证直接标的</span>'}</div><div><b>业务相关，业绩未验证</b>${(x.asset_tiers?.business_related||[]).map(v=>`<button data-chain-stock="${escHtml(v.ts_code||'')}">${escHtml(v.name||v.ts_code)}<small>${escHtml(v.tier||'同行')} · 5日 ${fmt(v.week_ret)}%</small></button>`).join('')||'<span>没有可验证名单</span>'}</div><div><b>纯概念映射</b><span>${(x.asset_tiers?.concept_only||[]).length?'存在映射，仍需核实业务收入':'未形成可验证名单，不展示猜测标的'}</span></div></div>`}
    if(isValidation){extra=`<div class="impact-check-grid">${(x.verification_checks||[]).map(v=>`<div><span>${escHtml(v.label||'验证项')}</span><b class="${matchClass(v.match)}">${escHtml(v.value||'未提供')}</b><em>${matchText(v.match)} · ${escHtml(v.source||'来源未提供')}</em></div>`).join('')||'<span>暂无价格资金验证项</span>'}</div>`}
    box.querySelector('.impact-stage-evidence').innerHTML=`<div class="impact-stage-title"><b>${escHtml(stage.stage)}</b><span>${escHtml(stage.headline||'暂无结论')}</span></div>${(stage.evidence||[]).map(v=>`<p>${escHtml(v)}</p>`).join('')}${extra}<small class="source">${stageIndex===4?'同向只表示价格/资金表现与消息方向一致，不证明消息是唯一原因。':'缺失环节不补数字；情景映射不等于确定受益或受损。'}</small>`;
    box.querySelectorAll('[data-chain-stock]').forEach(b=>b.onclick=()=>b.dataset.chainStock&&selectStock(b.dataset.chainStock,true));
  };
  box.innerHTML=`<div class="impact-chain-kpis"><span>方向<b class="${x.direction==='偏利好'?'up':x.direction==='偏利空'?'down':'gold'}">${escHtml(x.direction||'中性')}</b></span><span>影响强度<b>${escHtml(x.impact_strength||'未知')}</b></span><span>利好消化<b>${x.digestion_score==null?'不可计算':fmt(x.digestion_score,0)+'/100'}</b></span><span>市场同向证据<b>${x.recognition_score==null?'待验证':fmt(x.recognition_score,0)+'/100'}</b></span></div><div class="impact-stage-tabs">${stages.map((v,i)=>`<button class="impact-stage-button${i===0?' active':''}" data-stage-index="${i}"><span>${escHtml(v.stage)}</span><b>${escHtml(v.headline||'暂无')}</b></button>`).join('')}</div><div class="impact-stage-evidence"></div>`;
  item.appendChild(box);box.querySelectorAll('[data-stage-index]').forEach(b=>b.onclick=()=>renderStage(Number(b.dataset.stageIndex)));renderStage(0);
});
const newsChainV2Style=document.createElement('style');newsChainV2Style.textContent='.impact-chain-workbench{margin-top:9px;border-top:1px solid var(--line);padding-top:8px}.impact-chain-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px;margin-bottom:6px}.impact-chain-kpis span{background:var(--panel2);padding:6px;color:var(--muted);font-size:10px}.impact-chain-kpis b{display:block;color:var(--text);font-size:12px;margin-top:2px}.impact-stage-tabs{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:3px}.impact-stage-button{min-height:60px;border:1px solid var(--line);border-top:2px solid transparent;background:var(--panel2);color:var(--text);padding:6px;text-align:left;cursor:pointer;font:inherit;min-width:0}.impact-stage-button span,.impact-stage-button b{display:block;overflow-wrap:anywhere}.impact-stage-button span{color:var(--gold);font-size:10px}.impact-stage-button b{font-size:11px;line-height:1.35;margin-top:3px}.impact-stage-button.active{border-top-color:var(--gold);background:#1a252d}.impact-stage-evidence{background:#0b1218;padding:9px;margin-top:4px;min-height:105px}.impact-stage-title{display:grid;grid-template-columns:110px 1fr;gap:8px;margin-bottom:7px}.impact-stage-title b{color:var(--gold)}.impact-stage-title span{font-weight:700}.impact-stage-evidence p{margin:4px 0;color:var(--muted);font-size:11px;line-height:1.5}.impact-target-groups{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:5px;margin-top:7px}.impact-target-groups>div{background:var(--panel2);padding:6px}.impact-target-groups>div>b,.impact-target-groups span{display:block}.impact-target-groups>div>b{color:var(--gold);font-size:10px;margin-bottom:4px}.impact-target-groups button{width:100%;border:0;border-top:1px solid var(--line);background:transparent;color:var(--text);padding:5px 0;text-align:left;cursor:pointer}.impact-target-groups button small,.impact-target-groups span{color:var(--muted);font-size:10px}.impact-check-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;margin-top:7px}.impact-check-grid>div{background:var(--panel2);padding:6px}.impact-check-grid span,.impact-check-grid b,.impact-check-grid em{display:block}.impact-check-grid span,.impact-check-grid em{color:var(--muted);font-size:10px}.impact-check-grid em{font-style:normal;margin-top:3px}@media(max-width:700px){.impact-chain-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.impact-stage-tabs{grid-template-columns:1fr}.impact-stage-button{min-height:0}.impact-stage-title{grid-template-columns:1fr}.impact-target-groups,.impact-check-grid{grid-template-columns:1fr}}';document.head.appendChild(newsChainV2Style);
'''
    stock_final_ui = r'''const finalStockOverlay=()=>{const x=selectedStock,p=prices.filter(v=>v.ts_code===x.ts_code).sort((a,b)=>String(a.trade_date).localeCompare(String(b.trade_date)));if(!p.length)return;const close=p.map(v=>Number(v.close)).filter(Number.isFinite),current=close.at(-1),window=p.slice(-60),distinct=(values,ascending)=>{const out=[];for(const v of [...values].sort((a,b)=>ascending?a-b:b-a)){if(!out.some(z=>Math.abs(z-v)/Math.max(Math.abs(z),1)<.012))out.push(Number(v.toFixed(2)))}return out};const lows=[],highs=[];for(let i=2;i<p.length-2;i++){const lo=Number(p[i].low),hi=Number(p[i].high);if(Number.isFinite(lo)&&[p[i-2],p[i-1],p[i+1],p[i+2]].every(v=>lo<=Number(v.low)))lows.push(lo);if(Number.isFinite(hi)&&[p[i-2],p[i-1],p[i+1],p[i+2]].every(v=>hi>=Number(v.high)))highs.push(hi)}const recentLow=Math.min(...window.map(v=>Number(v.low)).filter(Number.isFinite)),recentHigh=Math.max(...window.map(v=>Number(v.high)).filter(Number.isFinite)),supports=distinct(lows.filter(v=>v<current),false).slice(0,3),resistances=distinct(highs.filter(v=>v>current),true).slice(0,3);if(!supports.length&&Number.isFinite(recentLow)&&recentLow<current)supports.push(Number(recentLow.toFixed(2)));if(!resistances.length&&Number.isFinite(recentHigh)&&recentHigh>current)resistances.push(Number(recentHigh.toFixed(2)));const s1=supports[0],r1=resistances[0],risk=Number.isFinite(s1)?Math.max(0,current-s1):null,reward=Number.isFinite(r1)?Math.max(0,r1-current):null,rr=risk&&reward?reward/risk:null;stockChart.setOption({series:[{markLine:{silent:true,symbol:['none','none'],data:[...supports.map((v,i)=>({yAxis:v,name:`支撑${i+1}`})),...resistances.map((v,i)=>({yAxis:v,name:`压力${i+1}`}))],label:{color:C.gold}}}]});const box=document.querySelector('#tradeLevels');if(box)box.innerHTML=`<div class="trade-level-grid"><div><span>当前价</span><b>${fmt(current)}</b></div><div><span>支撑位1 / 2 / 3</span><b>${supports.map(fmt).join(' / ')||'—'}</b></div><div><span>压力位1 / 2 / 3</span><b>${resistances.map(fmt).join(' / ')||'—'}</b></div><div><span>最近压力空间</span><b>${reward!=null?fmt(reward/current*100)+'%':'—'}</b></div><div><span>最近支撑风险</span><b>${risk!=null?fmt(risk/current*100)+'%':'—'}</b></div><div><span>观察盈亏比</span><b>${rr!=null?fmt(rr,2):'—'}</b></div></div><small class="source">支撑/压力取近60个交易日的真实局部高低点，并按1.2%邻近价位去重；不足时回退到近60日区间边界。它们是观察区间，不是保证反转的价格。</small>`;const decision=document.querySelector('#stockDecision');if(decision){const aligned=selectedStock.industry===summary.trade_sector||selectedStock.industry===summary.strongest_sector,sync=Number(selectedStock.week_ret)>0&&Number(selectedStock.net_mf_5d_yi)>0?'价格与5日资金同步':Number(selectedStock.week_ret)>0?'上涨但资金未确认':Number(selectedStock.net_mf_5d_yi)>0?'下跌但资金承接':'价格与资金未同步';decision.innerHTML=`<b>交易判断摘要：${aligned?'属于当前主线观察范围':'不属于当前主线优先范围'}</b><br><small>价格/资金：${sync}。${rr!=null&&rr>=1.5?'位置赔率尚可，仍需等验证。':'距离压力较近或支撑证据不足，优先等待。'}</small><br><small>继续观察条件：所属板块资金保持正向，且龙头/中军与该股相对强弱同步。</small><br><small>失效条件：跌破支撑1并伴随资金转负，或板块状态转弱；不构成买卖指令。</small>`}};const priorFinalRenderStock=renderStock;renderStock=function(){priorFinalRenderStock();finalStockOverlay()};finalStockOverlay();
'''
    stock_final_ui = stock_final_ui.replace("支撑/压力取近60个交易日的真实局部高低点", "支撑/压力取最近 ${window.length} 个交易日的真实局部高低点").replace("不足时回退到近60日区间边界", "不足时回退到当前加载区间边界")
    news_link_ui = r'''document.querySelectorAll('#topNewsList .news-brief a[href="#"]').forEach(a=>{const span=document.createElement('span');span.textContent=a.textContent;a.replaceWith(span)});const logicStockLinks=document.querySelectorAll('#logicChainList .logic-node[data-action="stock"]');logicStockLinks.forEach(node=>node.addEventListener('click',()=>node.dataset.target&&selectStock(node.dataset.target,true)));const newsLinkStyle=document.createElement('style');newsLinkStyle.textContent='#topNewsList .news-brief a{color:var(--text);text-decoration:none}#topNewsList .news-brief a:hover{color:var(--gold);text-decoration:underline}#topNewsList .news-brief b>span{cursor:pointer}';document.head.appendChild(newsLinkStyle)
'''
    etf_detail_ui = r'''const etfDetailHost=document.querySelector('#etfBoard');if(etfDetailHost&&!document.querySelector('#etfSelectedEvidence')){const detail=document.createElement('div');detail.id='etfSelectedEvidence';detail.className='etf-selected-evidence';const renderEtfDetail=i=>{const x=etfs[i]||etfs[0];if(!x)return;const exposure=(x.industry_exposure||[]).slice(0,5).map(v=>`${escHtml(v.industry)} ${fmt(v.weight,1)}%`).join(' · ')||'未提供行业权重';const overseasText=x.overseas_asset?`${escHtml(x.overseas_asset)} · 5日 ${fmt(x.overseas_ret_5d)}% · 20日 ${fmt(x.overseas_ret_20d)}%`:'暂无可验证海外联动';detail.innerHTML=`<div class="etf-selected-title">ETF观察证据 <small>${escHtml(x.name||x.ts_code||'')}</small></div><div class="etf-selected-grid"><div><span>观察理由</span><b>${escHtml(x.selection_reason||x.tool_role||'暂无')}</b></div><div><span>跟踪基准</span><b>${escHtml(x.benchmark||'未提供')}</b></div><div><span>收盘 / 窗口涨跌</span><b>${fmt(x.close)} / ${fmt(x.week_ret)}%</b></div><div><span>成交额 / 溢折价</span><b>${fmt(x.amount_yi)}亿 / ${fmt(x.premium_discount)}%</b></div><div><span>成分数 / 前十大权重</span><b>${fmt(x.component_count,0)} / ${fmt(x.top10_weight,1)}%</b></div><div><span>基金份额变化</span><b>${fmt(x.share_change_pct,2)}%</b></div><div><span>行业暴露前五</span><b>${exposure}</b></div><div><span>海外联动</span><b>${overseasText}</b></div></div><small class="source">ETF只作为指数、行业和流动性观察工具；成交额不等于申赎资金，份额变化也不等于纯申购赎回。溢折价、流动性或跟踪关系异常时，停止把它作为对应方向的替代观察。</small>`;};document.querySelectorAll('#etfBoard tbody tr').forEach((row,i)=>{row.dataset.etfIndex=i;row.addEventListener('click',()=>{document.querySelectorAll('#etfBoard tbody tr').forEach(r=>r.classList.remove('etf-selected-row'));row.classList.add('etf-selected-row');renderEtfDetail(i)})});renderEtfDetail(0);const style=document.createElement('style');style.textContent='.etf-selected-evidence{margin-top:9px;padding:10px;background:var(--panel2);border-top:2px solid var(--gold)}.etf-selected-title{font-weight:700;color:var(--gold);margin-bottom:7px}.etf-selected-title small{color:var(--text);margin-left:8px}.etf-selected-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:5px}.etf-selected-grid>div{padding:6px;background:var(--panel)}.etf-selected-grid span,.etf-selected-grid b{display:block}.etf-selected-grid span{color:var(--muted);font-size:10px}.etf-selected-grid b{font-size:11px;line-height:1.45;overflow-wrap:anywhere}.etf-selected-row{background:#263442!important;outline:1px solid var(--gold)}@media(max-width:900px){.etf-selected-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}';document.head.appendChild(style)}
'''
    etf_observer_ui = r'''const mountEtfEvidence=()=>{const host=document.querySelector('#etfBoard');if(!host||!etfs.length)return;let panel=host.querySelector('#etfSelectedEvidence');if(!panel){panel=document.createElement('div');panel.id='etfSelectedEvidence';panel.className='etf-selected-evidence';host.appendChild(panel)}const rows=[...host.querySelectorAll('tbody tr')],render=i=>{const x=etfs[i]||etfs[0];if(!x)return;panel.innerHTML='<div class="etf-selected-title">ETF观察证据 <small>'+escHtml(x.name||x.ts_code||'')+'</small></div><div class="etf-selected-grid"><div><span>观察理由</span><b>'+escHtml(x.selection_reason||x.tool_role||'暂无')+'</b></div><div><span>跟踪基准</span><b>'+escHtml(x.benchmark||'未提供')+'</b></div><div><span>收盘 / 窗口涨跌</span><b>'+fmt(x.close)+' / '+fmt(x.week_ret)+'%</b></div><div><span>成交额 / 溢折价</span><b>'+fmt(x.amount_yi)+'亿 / '+fmt(x.premium_discount)+'%</b></div><div><span>成分数 / 前十大权重</span><b>'+fmt(x.component_count,0)+' / '+fmt(x.top10_weight,1)+'%</b></div><div><span>基金份额变化</span><b>'+fmt(x.share_change_pct,2)+'%</b></div><div><span>行业暴露前五</span><b>'+((x.industry_exposure||[]).slice(0,5).map(v=>escHtml(v.industry)+' '+fmt(v.weight,1)+'%').join(' · ')||'未提供')+'</b></div><div><span>海外联动</span><b>'+(x.overseas_asset?escHtml(x.overseas_asset)+' · 5日 '+fmt(x.overseas_ret_5d)+'%':'暂无可验证海外联动')+'</b></div></div><small class="source">ETF是指数、行业和流动性观察工具；成交额不等于申赎资金，份额变化不等于纯申购赎回。</small>'};rows.forEach((row,i)=>{if(row.dataset.etfEvidenceBound)return;row.dataset.etfEvidenceBound='1';row.onclick=()=>{rows.forEach(r=>r.classList.remove('etf-selected-row'));row.classList.add('etf-selected-row');render(i)}});render(0);if(!host.dataset.etfObserver){host.dataset.etfObserver='1';new MutationObserver(mountEtfEvidence).observe(host,{childList:true})}};mountEtfEvidence();setInterval(mountEtfEvidence,1000);const etfObserverStyle=document.createElement('style');etfObserverStyle.textContent='.etf-selected-evidence{margin-top:9px;padding:10px;background:var(--panel2);border-top:2px solid var(--gold)}.etf-selected-title{font-weight:700;color:var(--gold);margin-bottom:7px}.etf-selected-title small{color:var(--text);margin-left:8px}.etf-selected-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:5px}.etf-selected-grid>div{padding:6px;background:var(--panel)}.etf-selected-grid span,.etf-selected-grid b{display:block}.etf-selected-grid span{color:var(--muted);font-size:10px}.etf-selected-grid b{font-size:11px;line-height:1.45;overflow-wrap:anywhere}.etf-selected-row{background:#263442!important;outline:1px solid var(--gold)}@media(max-width:900px){.etf-selected-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}';document.head.appendChild(etfObserverStyle)
'''
    etf_observer_ui = etf_observer_ui.replace("setInterval(mountEtfEvidence,1000);", "")
    valuation_evidence_ui = r'''const valuationEvidenceStyle=document.createElement('style');valuationEvidenceStyle.textContent='.valuation-evidence{margin-top:8px;padding:9px;background:var(--panel2);border-top:2px solid var(--gold);line-height:1.55}.valuation-evidence-title{color:var(--gold);font-weight:700;margin-bottom:6px}.valuation-evidence-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:5px}.valuation-evidence-grid>div{background:var(--panel);padding:6px;min-width:0}.valuation-evidence-grid span,.valuation-evidence-grid b{display:block}.valuation-evidence-grid span{color:var(--muted);font-size:10px}.valuation-evidence-grid b{font-size:11px;overflow-wrap:anywhere}.valuation-risk{color:var(--down)}@media(max-width:900px){.valuation-evidence-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}';document.head.appendChild(valuationEvidenceStyle);const mountValuationEvidence=()=>{const host=document.querySelector('.valuation-form');if(!host||!selectedStock)return;let box=document.querySelector('#valuationEvidence');if(!box){box=document.createElement('div');box.id='valuationEvidence';box.className='valuation-evidence';host.appendChild(box)}const x=selectedStock,pe=Number(x.pe),pb=Number(x.pb),peers=stocks.filter(v=>v.industry===x.industry),nums=f=>peers.map(v=>Number(v[f])).filter(v=>Number.isFinite(v)&&v>0).sort((a,b)=>a-b),rank=(v,arr)=>v>0&&arr.length?Math.round((arr.filter(z=>z<=v).length/arr.length)*100):null,q=(v,suffix='')=>v===null||v===undefined||!Number.isFinite(Number(v))?'未提供':fmt(v)+suffix,peArr=nums('pe'),pbArr=nums('pb'),method=/银行|保险/.test(x.industry)?'PB/ROE':/公用|电力|燃气|水务/.test(x.industry)?'DCF/股息':/半导体|电子设备|专用机械/.test(x.industry)?'PE/订单':/医药|生物/.test(x.industry)?'PE/管线':'PE+PB+FCFE',risk=Number(x.debt_to_assets)>70?'负债率较高，估值对利率和现金流更敏感':Number(x.netprofit_yoy)<0?'净利润同比为负，盈利假设可能下修':Number(x.q_sales_yoy)<0?'营收同比为负，成长假设可能下修':Number(x.fcfe_ps)<=0?'FCFE缺失或非正，现金流折现参考性有限':'周期、一次性损益和行业估值切换仍是主要风险';box.innerHTML='<div class="valuation-evidence-title">估值依据与数据边界 <small>当前仅作区间观察</small></div><div class="valuation-evidence-grid"><div><span>估值方法</span><b>'+method+'</b></div><div><span>财务期/公告日</span><b>'+q(x.ann_date)+'</b></div><div><span>EPS / BPS / FCFE</span><b>'+q(Number(x.eps))+' / '+q(Number(x.bps))+' / '+q(Number(x.fcfe_ps))+'</b></div><div><span>ROE / 毛利率 / 净利率</span><b>'+q(Number(x.roe),'%')+' / '+q(Number(x.grossprofit_margin),'%')+' / '+q(Number(x.netprofit_margin),'%')+'</b></div><div><span>营收同比 / 净利同比</span><b>'+q(Number(x.q_sales_yoy),'%')+' / '+q(Number(x.netprofit_yoy),'%')+'</b></div><div><span>同行样本数量</span><b>'+peers.length+' 家（'+escHtml(x.industry||'未分类')+'）</b></div><div><span>当前 PE / 同行分位</span><b>'+q(pe)+' / '+q(rank(pe,peArr),'%')+'</b></div><div><span>当前 PB / 同行分位</span><b>'+q(pb)+' / '+q(rank(pb,pbArr),'%')+'</b></div><div><span>总股本/历史估值分位</span><b>未提供 / 未提供</b></div><div><span>最大风险</span><b class="valuation-risk">'+risk+'</b></div></div><small class="source">区间由可编辑的 EPS、BPS、FCFE、同行 PE/PB 和增长/折现假设计算；同行分位只是当前样本横截面，不等于历史分位。没有接口证据的总股本、历史分位和行业专用模型不填数字。</small>'};mountValuationEvidence();const priorEvidenceRenderStock=renderStock;renderStock=function(){priorEvidenceRenderStock();setTimeout(mountValuationEvidence,0)};document.querySelectorAll('.valuation-form input').forEach(i=>i.addEventListener('input',mountValuationEvidence));'''
    valuation_evidence_ui = valuation_evidence_ui.replace("method=/银行|保险/.test(x.industry)?'PB/ROE':/公用|电力|燃气|水务/.test(x.industry)?'DCF/股息':/半导体|电子设备|专用机械/.test(x.industry)?'PE/订单':/医药|生物/.test(x.industry)?'PE/管线':'PE+PB+FCFE'", "method='通用交叉估值（行业专用模型未启用）'")
    stock_trade_odds_ui = r'''const mountStockTradeOdds=()=>{const host=document.querySelector('#tradeLevels'),x=selectedStock,p=prices.filter(v=>v.ts_code===x.ts_code).sort((a,b)=>String(a.trade_date).localeCompare(String(b.trade_date)));if(!host||p.length<5)return;let box=host.querySelector('.stock-trade-odds');if(!box){box=document.createElement('div');box.className='stock-trade-odds';host.appendChild(box)}const current=Number(p.at(-1).close),windowRows=p.slice(-60),lows=[],highs=[];for(let i=2;i<p.length-2;i++){const lo=Number(p[i].low),hi=Number(p[i].high);if([p[i-2],p[i-1],p[i+1],p[i+2]].every(v=>lo<=Number(v.low)))lows.push(lo);if([p[i-2],p[i-1],p[i+1],p[i+2]].every(v=>hi>=Number(v.high)))highs.push(hi)}const support=lows.filter(v=>v<current).sort((a,b)=>b-a)[0]??Math.min(...windowRows.map(v=>Number(v.low)).filter(Number.isFinite)),resistance=highs.filter(v=>v>current).sort((a,b)=>a-b)[0]??Math.max(...windowRows.map(v=>Number(v.high)).filter(Number.isFinite)),trueRanges=p.slice(-15).map((v,i,a)=>{const prev=i?Number(a[i-1].close):Number(v.close);return Math.max(Number(v.high)-Number(v.low),Math.abs(Number(v.high)-prev),Math.abs(Number(v.low)-prev))}).filter(Number.isFinite),atr=trueRanges.length?trueRanges.reduce((a,b)=>a+b,0)/trueRanges.length:null,reward=Number.isFinite(resistance)?Math.max(0,resistance-current):null,risk=Number.isFinite(support)?Math.max(0,current-support):null,rr=risk&&reward?reward/risk:null,aligned=x.industry===summary.trade_sector||x.industry===summary.strongest_sector,flowOk=Number(x.net_mf_5d_yi)>0,structure=Number.isFinite(support)&&current<support&&Number(x.net_mf_5d_yi)<0?'价格跌破支撑且资金转负，逻辑受损风险上升':Number.isFinite(support)&&current>=support?'仍在支撑区间上方，暂按正常波动观察':'支撑证据不足，无法区分正常回调与逻辑破坏',role=Number(x.leader_score)>=Math.max(Number(x.core_score),Number(x.elastic_score))?'龙头候选':Number(x.core_score)>=Number(x.elastic_score)?'中军/趋势候选':'弹性/补涨候选',marketCap=Number(summary.position),singleCap=rr>=2&&aligned&&flowOk?marketCap*.4:rr>=1.2&&aligned?marketCap*.2:0,stop=Number.isFinite(support)?support-(atr||0)*.5:null,take=Number.isFinite(resistance)?resistance:null;box.innerHTML=`<div class="trade-odds-title">交易赔率与验证 <small>规则模型，不是个性化仓位建议</small></div><div class="trade-odds-grid"><div><span>主线一致性</span><b>${aligned?'符合当前优先方向':'不在当前优先方向'}</b></div><div><span>市场地位</span><b>${role}</b></div><div><span>结构判断</span><b>${structure}</b></div><div><span>预期收益 / 潜在风险</span><b>${reward!=null?fmt(reward/current*100)+'%':'—'} / ${risk!=null?fmt(risk/current*100)+'%':'—'}</b></div><div><span>观察盈亏比</span><b>${rr!=null?fmt(rr,2):'—'}</b></div><div><span>模型胜率</span><b>不可计算</b><small>缺少独立历史回测样本</small></div><div><span>单标的模型仓位上限</span><b>${singleCap?fmt(singleCap,0)+'%':'0%'}</b><small>由市场总仓位、主线、资金和盈亏比约束</small></div><div><span>止损 / 止盈观察价</span><b>${fmt(stop)} / ${fmt(take)}</b></div><div><span>触发条件</span><b>${Number.isFinite(resistance)?`放量站上 ${fmt(resistance)}，且板块资金和龙头/中军同步`:'等待形成有效压力位与资金共振'}</b></div><div><span>放弃条件</span><b>${Number.isFinite(support)?`跌破 ${fmt(support)} 且5日/当日资金转负`:'板块资金转负或相对行业继续走弱'}</b></div></div><small class="source">收益和风险按最近压力/支撑距离计算；止损参考=支撑位-0.5×ATR，止盈参考=最近压力位。模型仓位上限=市场总仓位×规则系数，不考虑个人资产、持仓和风险承受能力。</small>`};const priorTradeOddsRenderStock=renderStock;renderStock=function(){priorTradeOddsRenderStock();mountStockTradeOdds()};mountStockTradeOdds();const stockTradeOddsStyle=document.createElement('style');stockTradeOddsStyle.textContent='.stock-trade-odds{margin-top:9px;padding-top:8px;border-top:1px solid var(--line2)}.trade-odds-title{color:var(--gold);font-weight:700}.trade-odds-title small{color:var(--muted);font-weight:400;margin-left:6px}.trade-odds-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px;margin-top:6px}.trade-odds-grid>div{background:var(--panel2);padding:6px;min-width:0}.trade-odds-grid span,.trade-odds-grid b,.trade-odds-grid small{display:block}.trade-odds-grid span,.trade-odds-grid small{color:var(--muted);font-size:10px}.trade-odds-grid b{font-size:11px;line-height:1.45;overflow-wrap:anywhere}';document.head.appendChild(stockTradeOddsStyle)
'''
    trade_plan_ui = stock_agent_ui + valuation_ui + valuation_evidence_ui + etf_share_ui + news_ui + news_switch_ui + news_detail_ui + news_objects_ui + news_chain_v2_ui + overseas_corr_ui + stock_final_ui + stock_trade_odds_ui + news_link_ui + r'''
const tradePlanAnchor=document.querySelector('#overviewView .decision-grid');
if(tradePlanAnchor&&!document.querySelector('#tradePlanPanel')){
  const panel=document.createElement('section');panel.id='tradePlanPanel';panel.className='panel change-panel';
  const plans=summary.trade_plan||[];
  panel.innerHTML=`<div class="head">今日观察清单 <small>基于真实快照生成，不是买卖指令</small></div><div class="trade-plan-grid">${plans.map(x=>`<div class="trade-plan-card"><div class="trade-plan-title"><b>${escHtml(x.name||x.ts_code||'未知')}</b><small>${escHtml(x.kind||'')} · ${escHtml(x.role||'')} · ${escHtml(x.industry||'')}</small></div><div class="trade-plan-evidence">${escHtml(x.evidence||'暂无证据')}</div><div><strong>继续观察</strong>${escHtml(x.continue_if||'暂无')}</div><div><strong>放弃观察</strong>${escHtml(x.drop_if||'暂无')}</div><button class="impact-link" data-plan-stock="${escHtml(x.kind==='个股'?x.ts_code||'':'')}" data-plan-etf="${escHtml(x.kind==='ETF'?x.ts_code||'':'')}">查看相关证据</button></div>`).join('')||'<div class="empty">当前没有足够的真实快照生成观察清单</div>'}</div>`;
  tradePlanAnchor.parentNode.insertBefore(panel,tradePlanAnchor.nextSibling);
  panel.querySelectorAll('[data-plan-stock]').forEach(b=>b.onclick=()=>b.dataset.planStock&&selectStock(b.dataset.planStock,true));
  panel.querySelectorAll('[data-plan-etf]').forEach(b=>b.onclick=()=>{showView('overviewView');document.querySelector('#etfBoard')?.scrollIntoView({behavior:'smooth',block:'center'})});
  const style=document.createElement('style');style.textContent='.trade-plan-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px;padding:9px}.trade-plan-card{background:var(--panel2);padding:10px;border-top:2px solid var(--gold);min-width:0}.trade-plan-title b,.trade-plan-title small{display:block}.trade-plan-title b{color:var(--text);overflow-wrap:anywhere}.trade-plan-title small,.trade-plan-evidence{color:var(--muted);font-size:11px;line-height:1.5;margin-top:4px}.trade-plan-card>div:not(.trade-plan-title):not(.trade-plan-evidence){font-size:11px;color:var(--muted);line-height:1.5;margin-top:7px}.trade-plan-card strong{color:var(--gold);display:block}.trade-plan-card button{margin-top:8px}@media(max-width:1200px){.trade-plan-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:600px){.trade-plan-grid{grid-template-columns:1fr}}';document.head.appendChild(style)
}
const sectorAgentBox=document.querySelector('#sectorAgentChart')?.parentElement;if(sectorAgentBox&&!document.querySelector('#sectorAgentCurrent')){const box=document.createElement('div');box.id='sectorAgentCurrent';box.className='agent-current';sectorAgentBox.appendChild(box);const render=()=>{const rows=selectedSector?.agent_flows||[];box.innerHTML='<div class="agent-current-title">当前板块代理资金明细 <small>按订单规模与股票特征回算；估算，不代表账户归属</small></div>'+rows.map(x=>`<div class="agent-current-row"><span>${escHtml(x.name)}<small>${escHtml(x.basis||'')} · ${escHtml(x.window||'可用窗口')} · 覆盖 ${fmt(x.coverage,0)} 家</small></span><b class="${cls(x.value)}">${fmt(x.value)}亿</b><em>${escHtml(x.direction||'暂无数据')}</em></div>`).join('')||'<div class="empty">暂无足够覆盖数据</div>'};const prior=renderSector;renderSector=function(){prior();render()};render()}
const agentCurrentStyle=document.createElement('style');agentCurrentStyle.textContent='.agent-current{padding:9px;border-top:1px solid var(--line2)}.agent-current-title{color:var(--gold);font-weight:700;margin-bottom:5px}.agent-current-title small{color:var(--muted);font-weight:400;margin-left:8px}.agent-current-row{display:grid;grid-template-columns:1fr 80px 60px;gap:7px;padding:6px 0;border-bottom:1px solid var(--line2);font-size:12px}.agent-current-row small{display:block;color:var(--muted);font-size:10px;margin-top:2px}.agent-current-row b{text-align:right}.agent-current-row em{text-align:right;font-style:normal;color:var(--muted)}';document.head.appendChild(agentCurrentStyle)
''' + etf_observer_ui
    concept_change_ui = r'''
const activeConcepts=summary.active_concepts||[],conceptMeta=summary.concept_meta||{};
const rotationHost=document.querySelector('#rotationView .rotation-bottom');
if(rotationHost&&!document.querySelector('#conceptPanel')){
  const panel=document.createElement('section');panel.id='conceptPanel';panel.className='panel concept-panel';
  panel.innerHTML='<div class="head">活跃概念与资金 <small>同花顺概念口径；点击查看真实成分覆盖</small></div><div class="concept-layout"><div id="conceptChart" class="chart"></div><div id="conceptDetail" class="concept-detail"></div></div>';
  rotationHost.parentNode.insertBefore(panel,rotationHost);
  const chart=echarts.init(panel.querySelector('#conceptChart')),rows=activeConcepts.slice(0,14),names=rows.map(x=>x.name).reverse(),values=rows.map(x=>Number(x.net_amount)).reverse();charts.conceptChart=chart;
  chart.setOption({animation:false,tooltip:{...tooltip,trigger:'axis',axisPointer:{type:'shadow'}},grid:{left:105,right:26,top:16,bottom:28},xAxis:{type:'value',name:'亿元',axisLabel:{color:C.muted},splitLine:{lineStyle:{color:'#24303a'}}},yAxis:{type:'category',data:names,axisLabel:{color:C.text,width:94,overflow:'truncate'}},series:[{type:'bar',data:values,itemStyle:{color:p=>Number(p.value)>=0?C.up:C.down}}]});
  const detail=panel.querySelector('#conceptDetail'),renderConcept=index=>{const x=rows[index]||rows[0];if(!x){detail.innerHTML='<div class="empty">概念接口未返回可用数据</div>';return}const members=stocks.filter(s=>(s.concepts||[]).some(c=>c.code===x.ts_code)).sort((a,b)=>Number(b.week_ret||0)-Number(a.week_ret||0)).slice(0,10);detail.innerHTML=`<div class="concept-title"><b>${escHtml(x.name)}</b><small>${escHtml(x.ts_code)} · ${escHtml(x.trade_date||'日期未知')}</small></div><div class="concept-kpis"><span>涨跌 <b class="${cls(x.pct_change)}">${fmt(x.pct_change)}%</b></span><span>净流 <b class="${cls(x.net_amount)}">${fmt(x.net_amount)}亿</b></span><span>接口公司数 <b>${fmt(x.reported_company_num,0)}</b></span><span>本地映射 <b>${fmt(x.mapped_member_count,0)}</b></span></div><div class="concept-lead">接口领涨：${escHtml(x.lead_stock||'未提供')} ${fmt(x.lead_stock_ret)}%</div><div class="concept-members">${members.map(s=>`<button data-concept-stock="${escHtml(s.ts_code)}"><span>${escHtml(s.name)}</span><b class="${cls(s.week_ret)}">${fmt(s.week_ret)}%</b></button>`).join('')||'<small>该概念不在当前活跃成分抓取覆盖内</small>'}</div><small class="source">${escHtml(conceptMeta.coverage_note||conceptMeta.reason||'缺少覆盖说明')}</small>`;detail.querySelectorAll('[data-concept-stock]').forEach(b=>b.onclick=()=>selectStock(b.dataset.conceptStock,true))};
  chart.on('click',p=>{const index=rows.findIndex(x=>x.name===p.name);renderConcept(index)});renderConcept(0);
}
const priorConceptEvidence=renderStockEvidence;
renderStockEvidence=function(){priorConceptEvidence();const x=selectedStock,p=prices.filter(v=>v.ts_code===x.ts_code).sort((a,b)=>String(a.trade_date).localeCompare(String(b.trade_date))),concept=activeConcepts.find(v=>v.ts_code===x.primary_concept_code),status=document.querySelector('#relativeStrengthStatus'),conceptCell=status?.querySelector('span:last-child');if(!concept||!concept.price_series?.length||!p.length){if(conceptCell)conceptCell.innerHTML='概念比较<b>当前活跃概念成分未覆盖</b>';return}const dates=p.map(v=>String(v.trade_date)),lookup=Object.fromEntries(concept.price_series.map(v=>[String(v[0]),Number(v[1])])),first=dates.map(d=>lookup[d]).find(Number.isFinite),line=dates.map(d=>Number.isFinite(lookup[d])&&Number.isFinite(first)?lookup[d]/first*100:null),stockBase=Number(p[0].close),stockLast=Number(p.at(-1).close)/stockBase*100,conceptLast=[...line].reverse().find(Number.isFinite),option=stockEvidenceChart.getOption(),name='概念：'+concept.name;option.legend[0].data.push(name);option.series.push({name,type:'line',data:line,showSymbol:false,lineStyle:{color:'#f08a5d',type:'dotted'}});stockEvidenceChart.setOption(option,true);if(conceptCell)conceptCell.innerHTML=`相对概念 ${escHtml(concept.name)}<b class="${cls(stockLast-conceptLast)}">${fmt(stockLast-conceptLast,2)}点</b>`};
renderStockEvidence();
const changesHost=document.querySelector('#changeList');
if(changesHost&&!document.querySelector('#changeModeSwitch')){const sw=document.createElement('div');sw.id='changeModeSwitch';sw.className='change-mode-switch';sw.innerHTML='<button class="btn active" data-change-mode="active">当前异动</button><button class="btn" data-change-mode="history">变化历史</button>';changesHost.parentNode.insertBefore(sw,changesHost);const renderChanges=mode=>{const rows=mode==='history'?(summary.change_history||[]):(summary.changes||[]);changesHost.innerHTML=rows.map(item=>{const x=mode==='history'?(item.latest||{}):item;return `<div class="change-item"><b>${escHtml(item.title||x.title||'未知变化')}</b><small>${escHtml(item.category||x.category||'变化')} · ${mode==='history'?`首次 ${escHtml(item.first_seen||'未知')} · 最近 ${escHtml(item.last_seen||'未知')} · 出现 ${fmt(item.occurrences,0)} 次 · ${escHtml(item.status||'未知')}`:`${escHtml(x.time||'未知时间')} · ${escHtml(x.before_text||('前值 '+fmt(x.before)))} → ${escHtml(x.after_text||('后值 '+fmt(x.after)))}`}</small><em>${escHtml(x.meaning||'暂无解释')}</em><small>置信度：${escHtml(x.confidence||'低')} · 验证：${escHtml(x.validation||'暂无')}</small></div>`}).join('')||'<div class="empty">暂无达到阈值的记录</div>'};sw.querySelectorAll('button').forEach(b=>b.onclick=()=>{sw.querySelectorAll('button').forEach(x=>x.classList.toggle('active',x===b));renderChanges(b.dataset.changeMode)});renderChanges('active')}
const enhanceEtfEvidence=()=>{const panel=document.querySelector('#etfSelectedEvidence');if(!panel||panel.querySelector('.etf-data-basis'))return;const selected=[...document.querySelectorAll('#etfBoard tbody tr')].findIndex(row=>row.classList.contains('etf-selected-row')),x=etfs[selected>=0?selected:0];if(!x)return;panel.insertAdjacentHTML('beforeend',`<div class="source etf-data-basis"><b>数据口径</b>：窗口收益=${escHtml(x.return_basis||'未复权收盘价')}；成分权重=${escHtml(x.component_weight_basis||'接口未提供可计算权重')}；净值日=${escHtml(x.nav_date||'未提供')}；复权因子 ${fmt(x.start_adj_factor,4)} → ${fmt(x.latest_adj_factor,4)}。</div>`)};document.querySelector('#etfBoard')?.addEventListener('click',()=>setTimeout(enhanceEtfEvidence,0));setTimeout(enhanceEtfEvidence,0);
const conceptStyle=document.createElement('style');conceptStyle.textContent='.concept-panel{height:390px;margin-bottom:7px}.concept-layout{display:grid;grid-template-columns:1.35fr .85fr;height:calc(100% - 39px)}.concept-detail{padding:10px;border-left:1px solid var(--line);overflow:auto}.concept-title b,.concept-title small{display:block}.concept-title b{color:var(--gold);font-size:15px}.concept-title small,.concept-lead{color:var(--muted);margin-top:4px}.concept-kpis{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px;margin:8px 0}.concept-kpis span{padding:6px;background:var(--panel2);color:var(--muted);font-size:10px}.concept-kpis b{display:block;font-size:13px}.concept-members{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px;margin:8px 0}.concept-members button{display:flex;justify-content:space-between;gap:5px;border:1px solid var(--line);background:var(--panel2);color:var(--text);padding:6px;text-align:left;cursor:pointer}.change-mode-switch{display:flex;gap:5px;padding:7px 9px;border-bottom:1px solid var(--line)}.etf-data-basis b{color:var(--gold)}@media(max-width:900px){.concept-panel{height:auto}.concept-layout{grid-template-columns:1fr;height:auto}.concept-layout>.chart{height:320px}.concept-detail{border-left:0;border-top:1px solid var(--line)}}';document.head.appendChild(conceptStyle);
'''
    news_validation_ui = r'''
const newsValidationRows=summary.news_briefs||[],newsValidationView=document.querySelector('#newsView');
if(newsValidationView&&!document.querySelector('#newsValidationPanel')){
  const panel=document.createElement('section');panel.id='newsValidationPanel';panel.className='panel news-validation-panel';panel.innerHTML='<div class="head">消息与价格资金验证 <small>事件时间只作对齐，时间相关性不等于因果</small></div><div class="news-validation-layout"><div id="newsValidationChart" class="chart"></div><div id="newsValidationEvidence" class="news-validation-evidence"></div></div>';
  newsValidationView.appendChild(panel);const chart=echarts.init(panel.querySelector('#newsValidationChart'));charts.newsValidationChart=chart;let activeNewsValidationIndex=0;
  const renderNewsValidation=index=>{activeNewsValidationIndex=index;const x=newsValidationRows[index]||newsValidationRows[0];if(!x){panel.querySelector('#newsValidationEvidence').innerHTML='<div class="empty">暂无可验证消息</div>';return}const sector=sectors.find(v=>v.industry===x.industry),scopeName=x.is_macro?'市场等权':'板块',priceRows=sector?.price_series||(x.is_macro?summary.market_price_series||[]:[]),flowRows=sector?flows.filter(v=>v.industry===x.industry):(x.is_macro?summary.market_flow_series||[]:[]),dates=[...new Set([...priceRows.map(v=>String(v[0])),...flowRows.map(v=>String(v.trade_date))])].sort(),priceLookup=Object.fromEntries(priceRows.map(v=>[String(v[0]),Number(v[1])])),flowLookup=Object.fromEntries(flowRows.map(v=>[String(v.trade_date),Number(v.net_mf_yi)])),first=dates.map(d=>priceLookup[d]).find(Number.isFinite),priceLine=dates.map(d=>Number.isFinite(priceLookup[d])&&Number.isFinite(first)?priceLookup[d]/first*100:null),eventDate=String(x.time||'').replace(/\D/g,'').slice(0,8),eventShort=eventDate?shortDate(eventDate):null,priceLabel=scopeName+'相对价格',flowLabel=scopeName+'主力净流';chart.setOption({animation:false,tooltip:{...tooltip,trigger:'axis'},legend:{type:'scroll',top:2,left:50,data:[priceLabel,flowLabel],textStyle:{color:C.muted}},grid:{left:58,right:58,top:46,bottom:34},xAxis:{type:'category',data:dates.map(shortDate),axisLabel:{color:C.muted}},yAxis:[{name:'起点=100',axisLabel:{color:C.muted},splitLine:{lineStyle:{color:'#24303a'}}},{name:'亿元',axisLabel:{color:C.muted}}],series:[{name:priceLabel,type:'line',showSymbol:false,data:priceLine,lineStyle:{color:C.gold,width:2},markLine:eventShort?{silent:true,symbol:['none','none'],label:{formatter:'消息',color:C.gold},lineStyle:{color:C.gold,type:'dashed'},data:[{xAxis:eventShort}]}:undefined},{name:flowLabel,type:'bar',yAxisIndex:1,data:dates.map(d=>flowLookup[d]??null),itemStyle:{color:p=>Number(p.value)>=0?C.up:C.down}}]},true);const history=x.validation_history||[];panel.querySelector('#newsValidationEvidence').innerHTML=`<div class="news-validation-title"><b>${escHtml(x.title||'未知消息')}</b><small>${escHtml(x.time||'时间未知')} · ${escHtml(x.industry||'未映射板块')} · ${escHtml(x.direction||'中性')}</small></div><div class="news-validation-status">当前判断：<b>${escHtml(x.validation_status||'待验证')}</b><small>影响价值 ${fmt(x.value_score,0)} · 可信度 ${fmt(x.trust_score,0)}</small></div><div class="news-validation-history">${history.slice().reverse().map(v=>`<div><b>${escHtml(v.trade_date||'未知日期')}</b><span>${escHtml(v.scope||scopeName)} ${fmt(v.sector_ret)}% · 资金 ${fmt(v.sector_flow)}亿</span><span>ETF ${escHtml(v.etf||'未映射')} · 溢折价 ${fmt(v.etf_premium)}%</span><em>${escHtml(v.status||'待验证')} · 置信度 ${escHtml(v.confidence||'低')}</em></div>`).join('')||'<small>当前为第一份验证记录，下一交易日后形成历史。</small>'}</div><small class="source">验证只比较消息映射对象与后续真实价格/资金，不证明消息是唯一原因。下一步验证：${escHtml(x.validation||'观察板块、龙头、中军和ETF是否同步')}。</small>`};
  window.renderNewsValidation=renderNewsValidation;document.querySelector('#impactNewsSwitcher')?.addEventListener('click',event=>{const button=event.target.closest('[data-impact-index]');if(button&&button.dataset.impactIndex!=='all')renderNewsValidation(Number(button.dataset.impactIndex))});renderNewsValidation(0);
  chartEvidenceRegistry.newsValidationChart={title:'消息与价格资金验证',definition:'把所选消息时间与映射板块的相对价格、主力净流及逐日验证状态放在同一时间轴上。',source:'真实消息快照、TinyShare板块成分日线与moneyflow资金快照',formula:'板块相对价格=板块价格序列/窗口首个有效值×100；板块资金=成分股主力净流合计。',unit:'相对价格点；资金：亿元',confidence:'中',raw:()=>({message:newsValidationRows[activeNewsValidationIndex],history:newsValidationRows[activeNewsValidationIndex]?.validation_history||[]})};mountChartEvidenceButtons();
}
const newsValidationStyle=document.createElement('style');newsValidationStyle.textContent='.news-validation-panel{height:390px;margin-top:7px}.news-validation-layout{display:grid;grid-template-columns:1.45fr .75fr;height:calc(100% - 39px)}.news-validation-evidence{padding:9px;border-left:1px solid var(--line);overflow:auto}.news-validation-title b,.news-validation-title small,.news-validation-status b,.news-validation-status small{display:block}.news-validation-title b{color:var(--text);line-height:1.45}.news-validation-title small,.news-validation-status small{color:var(--muted);margin-top:4px}.news-validation-status{padding:8px;background:var(--panel2);margin:8px 0}.news-validation-status b{color:var(--gold);font-size:14px}.news-validation-history>div{display:grid;grid-template-columns:70px 1fr;padding:6px 0;border-bottom:1px solid var(--line2);font-size:10px}.news-validation-history span,.news-validation-history em{color:var(--muted);font-style:normal}.news-validation-history em{grid-column:2}@media(max-width:900px){.news-validation-panel{height:auto}.news-validation-layout{grid-template-columns:1fr;height:auto}.news-validation-layout>.chart{height:300px}.news-validation-evidence{border-left:0;border-top:1px solid var(--line)}}';document.head.appendChild(newsValidationStyle);
'''
    trade_plan_ui += concept_change_ui + news_validation_ui
    template = template.replace('</script></body></html>', trade_plan_ui + '</script></body></html>')
    OUT.mkdir(parents=True, exist_ok=True)
    history_out = OUT / "history"
    history_out.mkdir(exist_ok=True)
    for stale in history_out.glob("*.json"):
        stale.unlink()
    for shard, payload in history_shards.items():
        (history_out / f"{shard}.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
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
