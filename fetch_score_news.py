import json
import re
from datetime import date, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd


OUT = Path("data/news")
OUT.mkdir(parents=True, exist_ok=True)
today = date.today()
DATES = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(10, -1, -1) if (today - timedelta(days=i)).weekday() < 5][-5:]
SOURCE_TRUST = {"交易所公告": 85, "公司公告": 78, "东方财富": 65, "其他": 45}
RULES = [
    ("退市", -35, 28), ("立案", -32, 25), ("重大违法", -35, 28), ("业绩暴雷", -30, 25),
    ("大额亏损", -28, 24), ("减持", -18, 15), ("处罚", -20, 18), ("终止", -18, 16),
    ("违约", -30, 25), ("风险提示", -10, 10), ("质押", -8, 9), ("冻结", -12, 12),
    ("业绩预增", 28, 24), ("同比增长", 16, 14), ("扭亏", 24, 21), ("重大合同", 24, 22),
    ("中标", 18, 17), ("回购", 20, 18), ("增持", 18, 16), ("获批", 25, 22),
    ("涨价", 15, 14), ("政策支持", 20, 18), ("降息", 18, 20), ("并购", 22, 20),
    ("重组", 24, 22), ("分红", 12, 10), ("产能投产", 18, 17),
]


def score(row):
    text = f"{row.get('title', '')} {row.get('content', '')}"
    hits, direction, event = [], 0, 10
    for keyword, direct, value in RULES:
        if keyword in text:
            hits.append(keyword)
            direction += direct
            event += value
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


basic = pd.read_csv("data/stock_basic.csv", dtype={"symbol": str})
basic["symbol"] = basic["symbol"].str.zfill(6)
symbol_map = basic.set_index("symbol").to_dict("index")
name_rows = basic.loc[basic["name"].str.len() >= 3, ["name", "ts_code", "industry"]].sort_values("name", key=lambda s: s.str.len(), ascending=False).to_dict("records")
events = []

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
