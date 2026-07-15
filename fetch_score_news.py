import json
import re
from datetime import date, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin


OUT = Path("data/news")
OUT.mkdir(parents=True, exist_ok=True)
today = date.today()
DATES = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(10, -1, -1) if (today - timedelta(days=i)).weekday() < 5][-5:]
SOURCE_TRUST = {"中国人民银行": 100, "交易所公告": 85, "公司公告": 78, "AKShare宏观数据镜像": 75, "东方财富": 65, "其他": 45}
RULES = [
    ("退市", -35, 28), ("立案", -32, 25), ("重大违法", -35, 28), ("业绩暴雷", -30, 25),
    ("大额亏损", -28, 24), ("减持", -18, 15), ("处罚", -20, 18), ("终止", -18, 16),
    ("违约", -30, 25), ("风险提示", -10, 10), ("质押", -8, 9), ("冻结", -12, 12),
    ("业绩预增", 28, 24), ("同比增长", 16, 14), ("扭亏", 24, 21), ("重大合同", 24, 22),
    ("中标", 18, 17), ("回购", 20, 18), ("增持", 18, 16), ("获批", 25, 22),
    ("涨价", 15, 14), ("政策支持", 20, 18), ("降息", 18, 20), ("并购", 22, 20),
    ("重组", 24, 22), ("分红", 12, 10), ("产能投产", 18, 17),
]
DIRECTION_OVERRIDES = [
    (r"终止.{0,8}减持|减持.{0,8}终止", 18, "终止减持计划"),
    (r"(?:合同|项目|中标).{0,12}终止|终止.{0,12}(?:合同|项目|中标)", -35, "合同或项目终止"),
    (r"未中标|流标|取消中标|解除合同", -30, "订单或合同落空"),
    (r"立案|重大违法|退市|违约", -35, "重大风险事件"),
]


def score(row):
    text = f"{row.get('title', '')} {row.get('content', '')}"
    hits, direction, event = [], 0, 10
    for keyword, direct, value in RULES:
        if keyword in text:
            hits.append(keyword)
            direction += direct
            event += value
    # Status phrases take precedence over isolated positive nouns. For example,
    # "中标项目合同终止" is a high-value negative event, not a positive tender.
    for pattern, override, reason in DIRECTION_OVERRIDES:
        if re.search(pattern, text):
            direction = override
            hits.append(reason)
            break
    trust = SOURCE_TRUST.get(row.get("source"), 8)
    scope = 18 if re.search(r"行业|全国|政策|国务院|央行|证监会", text) else 10 if re.search(r"公司|项目|产品", text) else 7
    earnings = 18 if re.search(r"收入|利润|订单|价格|成本|产能|合同|中标|业绩", text) else 8
    persistence = 15 if re.search(r"长期|多年|战略|政策|产能|合同|并购|重组", text) else 7
    scope_points = round(scope / 18 * 20)
    earnings_points = round(earnings / 18 * 20)
    persistence_points = round(persistence / 15 * 15)
    event_points = round(min(event, 29) / 29 * 10)
    source_points = round(trust * 0.35)
    relevance_points = min(15, len(hits) * 5)
    row.update({
        "value_score": min(100, round(source_points + scope_points + earnings_points + persistence_points + event_points + relevance_points)),
        "direction_score": max(-100, min(100, direction)),
        "trust_score": trust,
        "score_breakdown": {"source": source_points, "scope": scope_points, "earnings": earnings_points, "persistence": persistence_points, "event": event_points, "relevance": relevance_points},
        "score_formula": "来源35% + 影响范围20分 + 业绩/经营20分 + 持续性15分 + 事件强度10分 + 命中相关性15分封顶",
        "reasons": "、".join(hits) if hits else "未命中高权重事件词",
    })
    return row


MACRO_SOURCES = [
    ("\u4e2d\u56fd CPI", "macro_china_cpi_monthly", "https://www.stats.gov.cn/", "inflation"),
    ("\u4e2d\u56fd\u56de\u8d2d\u5229\u7387", "repo_rate_query", "http://www.pbc.gov.cn/", "liquidity"),
    ("\u4e2d\u56fd LPR", "macro_china_lpr", "http://www.pbc.gov.cn/", "monetary_policy"),
    ("\u7f8e\u8054\u50a8\u653f\u7b56\u5229\u7387", "macro_bank_usa_interest_rate", "https://www.federalreserve.gov/", "overseas_rate"),
]


def macro_event(label, function_name, url, category, row):
    values = {str(k): str(v) for k, v in row.items() if pd.notna(v) and str(v).strip() not in ("", "nan", "None")}
    if not values:
        return None
    date_value = next((v for k, v in values.items() if any(x in k.lower() for x in ("date", "time", "release", "\u65e5\u671f", "\u65f6\u95f4", "\u53d1\u5e03"))), "")
    latest = "; ".join(f"{k}: {v}" for k, v in list(values.items())[:8])
    item = {
        "time": date_value or str(today), "retrieved_at": str(today), "title": f"\u5b8f\u89c2\uff5c{label}: {latest[:180]}", "content": latest,
        "source": f"AKShare\u5b8f\u89c2\u6570\u636e\u955c\u50cf\uff5c{label}", "url": url, "ts_code": "", "name": "", "industry": "\u5b8f\u89c2\u653f\u7b56",
        "macro_category": category, "is_macro": True, "impact_type": "Indirect", "impact_scope": "Index + sectors + ETF",
        "impact_strength": "\u9ad8", "direction": "\u4e2d\u6027\u5f85\u524d\u503c/\u9884\u671f\u9a8c\u8bc1", "direction_score": 0,
        "market_acceptance": "\u9700\u7ed3\u5408\u4ef7\u683c\u3001\u8d44\u91d1\u548c\u4e00\u81f4\u9884\u671f\u9a8c\u8bc1",
        "validation": "\u6bd4\u8f83\u524d\u503c\u4e0e\u4e00\u81f4\u9884\u671f\uff0c\u518d\u89c2\u5bdf\u6307\u6570\u5bbd\u5ea6\u3001\u5229\u7387\u3001\u76f8\u5173\u677f\u5757\u548c ETF \u662f\u5426\u540c\u6b65",
        "reasons": "\u5b98\u65b9\u5b8f\u89c2\u6570\u636e\uff1b\u7f3a\u5c11\u53ef\u6bd4\u4e00\u81f4\u9884\u671f\u65f6\u4e0d\u76f4\u63a5\u5224\u65ad\u5229\u591a\u6216\u5229\u7a7a",
    }
    item = score(item)
    item.update({"value_score": 85, "trust_score": 75, "direction_score": 0, "macro_category": category,
                 "is_macro": True, "score_formula": "AKShare\u5b8f\u89c2\u6570\u636e\u955c\u50cf\uff1a\u4ef7\u503c85\uff0c\u6765\u6e90\u53ef\u4fe1\u5ea675\uff1b\u65b9\u5411\u5fc5\u987b\u7ed3\u5408\u524d\u503c/\u4e00\u81f4\u9884\u671f\u4e0e\u5e02\u573a\u4ef7\u683c\u9a8c\u8bc1"})
    return item


def latest_pbc_open_market_event():
    index_url = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; A-share-dashboard/1.0)"}
    response = requests.get(index_url, timeout=25, headers=headers)
    response.raise_for_status()
    response.encoding = "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")
    link = next((a for a in soup.find_all("a") if re.search(r"公开市场业务交易公告\s*\[\d{4}\]第\d+号", a.get_text(" ", strip=True))), None)
    if not link or not link.get("href"):
        return None
    article_url = urljoin(index_url, link["href"])
    article = requests.get(article_url, timeout=25, headers=headers)
    article.raise_for_status()
    article.encoding = "utf-8"
    text = " ".join(BeautifulSoup(article.text, "html.parser").get_text(" ", strip=True).split())
    sentence_match = re.search(r"(20\d{2}年\d{1,2}月\d{1,2}日中国人民银行.*?逆回购操作.*?。)", text)
    sentence = sentence_match.group(1) if sentence_match else link.get_text(" ", strip=True)
    date_match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", sentence)
    event_date = "-".join([date_match.group(1), date_match.group(2).zfill(2), date_match.group(3).zfill(2)]) if date_match else str(today)
    amount_match = re.search(r"([\d,.]+)亿元", sentence)
    term_match = re.search(r"(\d+)天期", sentence)
    rate_match = re.search(r"(\d+\s*\.\s*\d+)\s*%", text)
    amount = amount_match.group(1).replace(",", "") if amount_match else "未提取"
    term = f"{term_match.group(1)}天" if term_match else "期限未提取"
    rate = rate_match.group(1).replace(" ", "") + "%" if rate_match else "利率未提取"
    return {
        "time": event_date, "retrieved_at": str(today),
        "title": f"央行公开市场｜开展{amount}亿元{term}逆回购，操作利率{rate}",
        "content": sentence, "source": "中国人民银行", "url": article_url,
        "ts_code": "", "name": "", "industry": "宏观政策", "macro_category": "liquidity",
        "is_macro": True, "impact_type": "间接影响", "impact_scope": "利率、流动性、指数、金融与成长板块、宽基ETF",
        "impact_strength": "高", "direction": "中性待到期量验证", "direction_score": 0,
        "market_acceptance": "操作量不等于净投放，需结合当日到期量、资金利率和股债价格验证",
        "validation": "核对当日逆回购到期量后计算净投放，再观察DR007、国债收益率、指数成交额和宽基ETF是否同步",
        "macro_benefit_scenarios": "净投放扩大且资金利率回落时，流动性敏感的成长、券商和宽基ETF可能受益",
        "macro_risk_scenarios": "到期量更大形成净回笼，或资金利率不降时，不应把操作量直接解释为利好",
        "reasons": "中国人民银行官网公开市场业务交易公告；仅确认操作量、期限和利率",
        "value_score": 96, "trust_score": 100,
        "score_formula": "央行官网原始公告；价值96，来源可信度100；方向需由到期量和市场价格二次验证",
    }


def latest_bls_cpi_event():
    api_url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    series_ids = ["CUUR0000SA0", "CUSR0000SA0", "CUUR0000SA0L1E", "CUSR0000SA0L1E"]
    response = requests.post(api_url, json={"seriesid": series_ids, "startyear": str(today.year - 1), "endyear": str(today.year)}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError("BLS API did not return a successful response")
    series = {item["seriesID"]: item.get("data", []) for item in payload.get("Results", {}).get("series", [])}
    def values(series_id):
        return {(str(x["year"]), str(x["period"])): float(x["value"]) for x in series.get(series_id, []) if x.get("period") != "M13" and x.get("value") not in (None, "", "-")}
    headline_nsa, headline_sa = values("CUUR0000SA0"), values("CUSR0000SA0")
    core_nsa, core_sa = values("CUUR0000SA0L1E"), values("CUSR0000SA0L1E")
    if not headline_nsa or not headline_sa or not core_nsa or not core_sa:
        return None
    latest_year, latest_period = max(headline_nsa, key=lambda x: (int(x[0]), int(x[1][1:])))
    month = int(latest_period[1:])
    previous = (str(int(latest_year) - 1), "M12") if month == 1 else (latest_year, f"M{month - 1:02d}")
    year_ago = (str(int(latest_year) - 1), latest_period)
    required = [(headline_nsa, year_ago), (headline_sa, previous), (core_nsa, year_ago), (core_sa, previous)]
    if any(key not in mapping for mapping, key in required):
        return None
    headline_yoy = (headline_nsa[(latest_year, latest_period)] / headline_nsa[year_ago] - 1) * 100
    headline_mom = (headline_sa[(latest_year, latest_period)] / headline_sa[previous] - 1) * 100
    core_yoy = (core_nsa[(latest_year, latest_period)] / core_nsa[year_ago] - 1) * 100
    core_mom = (core_sa[(latest_year, latest_period)] / core_sa[previous] - 1) * 100
    period_text = f"{latest_year}年{month}月"
    metrics = f"CPI同比{headline_yoy:.2f}%、季调环比{headline_mom:.2f}%；核心CPI同比{core_yoy:.2f}%、季调环比{core_mom:.2f}%"
    return {
        "time": f"{latest_year}-{month:02d}", "retrieved_at": str(today),
        "title": f"美国劳工统计局｜{period_text}{metrics}", "content": metrics,
        "source": "美国劳工统计局", "url": "https://www.bls.gov/cpi/",
        "ts_code": "", "name": "", "industry": "宏观政策", "macro_category": "overseas_inflation",
        "is_macro": True, "impact_type": "间接影响", "impact_scope": "美债利率、美元、人民币汇率、A股成长与外资敏感资产、相关ETF",
        "impact_strength": "高", "direction": "中性待一致预期验证", "direction_score": 0,
        "market_acceptance": "实际值需与一致预期比较，并观察美债、美元、人民币和A股开盘后的价格反应",
        "validation": "补充一致预期后判断超预期方向；观察10年期美债、美元指数、离岸人民币、成长板块和海外ETF是否同步",
        "macro_benefit_scenarios": "低于预期且美债收益率回落时，高估值成长、外资敏感方向和相关ETF可能受益",
        "macro_risk_scenarios": "高于预期且美债收益率上行时，高估值成长和外资敏感资产可能承压",
        "reasons": "美国劳工统计局公开API；同比使用非季调指数，环比使用季调指数；未接入一致预期",
        "value_score": 95, "trust_score": 100,
        "score_formula": "BLS官方API原始序列；价值95，来源可信度100；没有一致预期时方向保持中性",
    }


events = []
macro_events = []
try:
    pbc_event = latest_pbc_open_market_event()
    if pbc_event:
        macro_events.append(pbc_event)
        print("OK PBC open market", pbc_event["title"])
except Exception as exc:
    print("FAIL PBC open market", type(exc).__name__, exc)
try:
    bls_event = latest_bls_cpi_event()
    if bls_event:
        macro_events.append(bls_event)
        print("OK BLS CPI", bls_event["title"])
except Exception as exc:
    print("FAIL BLS CPI", type(exc).__name__, exc)
for label, function_name, url, category in MACRO_SOURCES:
    try:
        fn = getattr(ak, function_name)
        frame = fn()
        if frame is None or frame.empty:
            continue
        frame.to_csv(OUT / f"macro_{function_name}.csv", index=False, encoding="utf-8-sig")
        event = macro_event(label, function_name, url, category, frame.iloc[-1].to_dict())
        if event:
            macro_events.append(event)
    except Exception as exc:
        print("FAIL macro", function_name, type(exc).__name__, exc)
events.extend(macro_events)
print("MACRO", len(macro_events))


basic = pd.read_csv("data/stock_basic.csv", dtype={"symbol": str})
basic["symbol"] = basic["symbol"].str.zfill(6)
symbol_map = basic.set_index("symbol").to_dict("index")
name_rows = basic.loc[basic["name"].str.len() >= 3, ["name", "ts_code", "industry"]].sort_values("name", key=lambda s: s.str.len(), ascending=False).to_dict("records")

for date in DATES:
    try:
        df = ak.stock_notice_report(symbol="全部", date=date)
        df.to_csv(OUT / f"notices_{date}.csv", index=False, encoding="utf-8-sig")
        for r in df.to_dict("records"):
            symbol = str(r.get("代码", "")).zfill(6)
            meta = symbol_map.get(symbol, {})
            events.append(score({
                "time": str(r.get("公告日期", date)), "title": str(r.get("公告标题", "")),
                "content": str(r.get("公告类型", "")), "source": "交易所公告", "url": str(r.get("网址", "")),
                "ts_code": meta.get("ts_code", ""), "name": str(r.get("名称", meta.get("name", ""))),
                "industry": meta.get("industry", "未映射"),
            }))
        print("OK notice", date, len(df))
    except Exception as exc:
        print("FAIL notice", date, type(exc).__name__, exc)

try:
    news = ak.stock_info_global_em()
    news.to_csv(OUT / "global_news_latest.csv", index=False, encoding="utf-8-sig")
    for r in news.to_dict("records"):
        title, content = str(r.get("标题", "")), str(r.get("摘要", ""))
        text, ts_code, name, industry = title + " " + content, "", "", "未映射"
        code_match = re.search(r"\b(\d{6})(?:\.(?:SH|SZ|BJ))?\b", text)
        if code_match and code_match.group(1) in symbol_map:
            meta = symbol_map[code_match.group(1)]
            ts_code, name, industry = meta.get("ts_code", ""), meta.get("name", ""), meta.get("industry", "未映射")
        else:
            for meta in name_rows:
                if meta["name"] in text:
                    ts_code, name, industry = meta["ts_code"], meta["name"], meta["industry"]
                    break
        events.append(score({
            "time": str(r.get("发布时间", "")), "title": title, "content": content,
            "source": "东方财富", "url": str(r.get("链接", "")), "ts_code": ts_code,
            "name": name, "industry": industry,
        }))
    print("OK global_news", len(news))
except Exception as exc:
    print("FAIL global_news", type(exc).__name__, exc)

out = pd.DataFrame(events).drop_duplicates(["title", "time"]).sort_values(["value_score", "time"], ascending=[False, False])
out.to_csv(OUT / "news_scored.csv", index=False, encoding="utf-8-sig")
(OUT / "news_scored.json").write_text(json.dumps(out.to_dict("records"), ensure_ascii=False, indent=2), encoding="utf-8")
print("TOTAL", len(out), "HIGH_VALUE", int((out["value_score"] >= 75).sum()))
