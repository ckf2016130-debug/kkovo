import html
import json
from pathlib import Path

import numpy as np
import pandas as pd


DATA = Path("data")
OUT = Path("output")
DATES = ["20260706", "20260707", "20260708", "20260709", "20260710"]


def num(s):
    return pd.to_numeric(s, errors="coerce")


def pct_rank(s):
    return s.rank(pct=True).fillna(0.5)


def fmt(v, digits=2):
    return "--" if pd.isna(v) else f"{v:,.{digits}f}"


def esc(v):
    return html.escape(str(v))


def load():
    basic = pd.read_csv(DATA / "stock_basic.csv", dtype={"symbol": str, "list_date": str})
    basic["industry"] = basic["industry"].fillna("未分类").replace("", "未分类")
    d0 = pd.read_csv(DATA / "daily_20260706.csv")
    d1 = pd.read_csv(DATA / "daily_20260710.csv")
    week = d0[["ts_code", "pre_close", "open"]].merge(
        d1[["ts_code", "close", "amount", "pct_chg"]], on="ts_code", how="inner", suffixes=("_start", "_end")
    )
    week["week_ret"] = (num(week["close"]) / num(week["pre_close"]) - 1) * 100
    week["fri_ret"] = num(week["pct_chg"])

    valuation = pd.read_csv(DATA / "daily_basic_20260710.csv")
    for c in ["turnover_rate", "total_mv", "circ_mv", "pe", "pb"]:
        valuation[c] = num(valuation[c])

    flows = []
    for date in DATES:
        x = pd.read_csv(DATA / f"moneyflow_{date}.csv")
        x["trade_date"] = x["trade_date"].astype(str)
        x["net_mf_amount"] = num(x["net_mf_amount"])
        flows.append(x[["ts_code", "trade_date", "net_mf_amount"]])
    flow = pd.concat(flows, ignore_index=True)
    flow_w = flow.groupby("ts_code", as_index=False)["net_mf_amount"].sum().rename(columns={"net_mf_amount": "net_mf_5d"})
    fri_flow = flow[flow["trade_date"] == DATES[-1]][["ts_code", "net_mf_amount"]].rename(columns={"net_mf_amount": "net_mf_fri"})

    limits = pd.concat([pd.read_csv(DATA / f"limit_list_{d}.csv") for d in DATES], ignore_index=True)
    lim_counts = limits.pivot_table(index="ts_code", columns="limit", values="trade_date", aggfunc="count", fill_value=0).reset_index()
    for c in ["U", "D", "Z"]:
        if c not in lim_counts:
            lim_counts[c] = 0

    stocks = basic.merge(week, on="ts_code", how="left").merge(
        valuation[["ts_code", "turnover_rate", "total_mv", "circ_mv", "pe", "pb"]], on="ts_code", how="left"
    ).merge(flow_w, on="ts_code", how="left").merge(fri_flow, on="ts_code", how="left").merge(
        lim_counts[["ts_code", "U", "D", "Z"]], on="ts_code", how="left"
    )
    for c in ["net_mf_5d", "net_mf_fri", "U", "D", "Z"]:
        stocks[c] = stocks[c].fillna(0)
    stocks["net_mf_5d_yi"] = stocks["net_mf_5d"] / 10000
    stocks["net_mf_fri_yi"] = stocks["net_mf_fri"] / 10000
    stocks["circ_mv_yi"] = stocks["circ_mv"] / 10000
    stocks["turnover_yi"] = num(stocks["amount"]) / 100000
    return stocks, flow, limits


def analyze(stocks, flow, limits):
    valid = stocks.dropna(subset=["week_ret"]).copy()
    valid["up"] = (valid["week_ret"] > 0).astype(int)
    valid["down"] = (valid["week_ret"] < 0).astype(int)
    valid["flat"] = (valid["week_ret"] == 0).astype(int)

    daily_flow = flow.merge(stocks[["ts_code", "industry"]], on="ts_code", how="left")
    daily_sector = daily_flow.groupby(["industry", "trade_date"], as_index=False)["net_mf_amount"].sum()
    daily_sector["net_mf_yi"] = daily_sector["net_mf_amount"] / 10000
    fri = daily_sector[daily_sector["trade_date"] == DATES[-1]][["industry", "net_mf_yi"]].rename(columns={"net_mf_yi": "fri_flow_yi"})

    sec = valid.groupby("industry").agg(
        constituents=("ts_code", "count"),
        week_ret=("week_ret", "mean"),
        median_ret=("week_ret", "median"),
        net_mf_yi=("net_mf_5d_yi", "sum"),
        turnover_yi=("turnover_yi", "sum"),
        up=("up", "sum"), down=("down", "sum"), flat=("flat", "sum"),
        limit_up=("U", "sum"), limit_down=("D", "sum"), broken=("Z", "sum"),
    ).reset_index().merge(fri, on="industry", how="left")
    sec["breadth"] = sec["up"] / sec["constituents"] * 100
    sec["flow_ratio"] = np.where(sec["turnover_yi"] != 0, sec["net_mf_yi"] / sec["turnover_yi"] * 100, np.nan)
    sec["strength"] = (
        35 * pct_rank(sec["week_ret"]) + 30 * pct_rank(sec["net_mf_yi"]) +
        20 * pct_rank(sec["breadth"]) + 15 * pct_rank(sec["limit_up"] - sec["limit_down"])
    )

    def state(r):
        if r.week_ret > 0 and r.net_mf_yi > 0 and r.breadth >= 55 and r.fri_flow_yi >= 0:
            return "强势延续"
        if r.week_ret > 0 and (r.net_mf_yi < 0 or r.fri_flow_yi < 0):
            return "上涨分歧"
        if r.week_ret <= 0 and r.fri_flow_yi > 0 and r.breadth >= 40:
            return "潜在轮入"
        if r.week_ret < 0 and r.net_mf_yi < 0:
            return "弱势流出"
        return "震荡观察"
    sec["state"] = sec.apply(state, axis=1)
    sec = sec.sort_values("strength", ascending=False).reset_index(drop=True)
    sec["rank"] = np.arange(1, len(sec) + 1)

    valid["leader_score"] = (
        0.33 * pct_rank(valid["week_ret"]) + 0.32 * pct_rank(valid["net_mf_5d_yi"]) +
        0.20 * pct_rank(valid["U"] * 2 + valid["Z"]) + 0.15 * pct_rank(valid["turnover_yi"])
    )
    valid["elastic_score"] = 0.55 * pct_rank(valid["week_ret"]) + 0.25 * pct_rank(valid["turnover_rate"]) + 0.20 * pct_rank(-valid["circ_mv_yi"])
    valid["core_score"] = 0.45 * pct_rank(valid["circ_mv_yi"]) + 0.30 * pct_rank(valid["net_mf_5d_yi"]) + 0.15 * pct_rank(valid["turnover_yi"]) + 0.10 * pct_rank(valid["week_ret"])
    return valid, sec, daily_sector


def stock_table(rows, limit=20):
    rows = rows.head(limit)
    body = []
    for _, r in rows.iterrows():
        body.append(
            f"<tr><td>{esc(r['name'])}</td><td class='mono'>{esc(r['ts_code'])}</td>"
            f"<td class='num {('pos' if r.week_ret > 0 else 'neg')}'>{fmt(r.week_ret)}%</td>"
            f"<td class='num'>{fmt(r.net_mf_5d_yi)}</td><td class='num'>{fmt(r.circ_mv_yi)}</td>"
            f"<td class='num'>{fmt(r.turnover_rate)}</td><td class='num'>{int(r.U)}/{int(r.Z)}/{int(r.D)}</td></tr>"
        )
    return "<table><thead><tr><th>股票</th><th>代码</th><th>周涨跌</th><th>5日主力净流(亿)</th><th>流通市值(亿)</th><th>换手率%</th><th>涨停/炸板/跌停</th></tr></thead><tbody>" + "".join(body) + "</tbody></table>"


def all_constituents(rows):
    body = []
    rows = rows.sort_values(["leader_score", "net_mf_5d_yi"], ascending=False)
    for _, r in rows.iterrows():
        body.append(
            f"<tr data-search='{esc(str(r['name']) + ' ' + str(r['ts_code']) + ' ' + str(r['industry']))}'>"
            f"<td>{esc(r['name'])}</td><td class='mono'>{esc(r['ts_code'])}</td><td>{esc(r['market'])}</td>"
            f"<td class='num'>{fmt(r.week_ret)}%</td><td class='num'>{fmt(r.net_mf_5d_yi)}</td>"
            f"<td class='num'>{fmt(r.net_mf_fri_yi)}</td><td class='num'>{fmt(r.circ_mv_yi)}</td>"
            f"<td class='num'>{fmt(r.turnover_rate)}</td><td class='num'>{int(r.U)}/{int(r.Z)}/{int(r.D)}</td></tr>"
        )
    return "<table class='constituents'><thead><tr><th>股票</th><th>代码</th><th>市场</th><th>周涨跌</th><th>5日净流(亿)</th><th>周五净流(亿)</th><th>流通市值(亿)</th><th>换手率%</th><th>涨/炸/跌</th></tr></thead><tbody>" + "".join(body) + "</tbody></table>"


def make_html(stocks, sec, daily_sector, limits):
    market_ret = stocks["week_ret"].mean()
    breadth = (stocks["week_ret"] > 0).mean() * 100
    total_flow = stocks["net_mf_5d_yi"].sum()
    lim = limits["limit"].value_counts()
    top = sec.head(12)
    max_abs = max(abs(top["net_mf_yi"]).max(), 1)
    bars = []
    for _, r in top.iterrows():
        w = abs(r.net_mf_yi) / max_abs * 100
        bars.append(f"<div class='barrow'><span>{esc(r.industry)}</span><div class='track'><i class='{('in' if r.net_mf_yi >= 0 else 'out')}' style='width:{w:.1f}%'></i></div><b>{fmt(r.net_mf_yi)}亿</b></div>")

    sec_rows = []
    for _, r in sec.iterrows():
        sec_rows.append(
            f"<tr><td>{int(r['rank'])}</td><td><a href='#sec-{int(r['rank'])}'>{esc(r['industry'])}</a></td><td><span class='tag'>{esc(r['state'])}</span></td>"
            f"<td class='num'>{fmt(r['week_ret'])}%</td><td class='num'>{fmt(r['net_mf_yi'])}</td><td class='num'>{fmt(r['fri_flow_yi'])}</td>"
            f"<td class='num'>{fmt(r['breadth'],1)}%</td><td class='num'>{int(r['limit_up'])}/{int(r['broken'])}/{int(r['limit_down'])}</td><td class='num'>{int(r['constituents'])}</td></tr>"
        )

    sections = []
    for _, s in sec.iterrows():
        rows = stocks[stocks["industry"] == s.industry].copy()
        leaders = rows.sort_values("leader_score", ascending=False)
        cores = rows[(rows.week_ret > 0) & (rows.net_mf_5d_yi > 0)].sort_values("core_score", ascending=False)
        elastic = rows.sort_values("elastic_score", ascending=False)
        sections.append(f"""
        <section class='sector' id='sec-{int(s['rank'])}'>
          <div class='sector-head'><div><span class='rank'>#{int(s['rank'])}</span><h3>{esc(s.industry)}</h3><span class='tag'>{esc(s.state)}</span></div>
          <p>周涨跌 {fmt(s.week_ret)}% · 5日主力净流 {fmt(s.net_mf_yi)}亿 · 上涨家数占比 {fmt(s.breadth,1)}% · 涨停/炸板/跌停 {int(s.limit_up)}/{int(s.broken)}/{int(s.limit_down)}</p></div>
          <div class='three'><div><h4>领涨龙头</h4>{stock_table(leaders,3)}</div><div><h4>中军候选</h4>{stock_table(cores,3)}</div><div><h4>弹性候选</h4>{stock_table(elastic,3)}</div></div>
          <details><summary>展开全部 {len(rows)} 只成分股</summary>{all_constituents(rows)}</details>
        </section>""")

    source_note = "TinyShare/Tushare兼容接口：stock_basic、daily、daily_basic、moneyflow、limit_list_d、trade_cal、moneyflow_hsgt、margin；冻结时间为2026-07-10收盘。"
    html_text = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>A股资金流与板块轮动周报</title><style>
    :root{{--bg:#f4f6f8;--paper:#fff;--ink:#17202a;--muted:#68727d;--line:#dfe4e8;--red:#c0392b;--green:#16794b;--gold:#b78103;--blue:#275d88}}
    *{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 system-ui,"Microsoft YaHei",sans-serif;letter-spacing:0}}
    header{{background:#17202a;color:white;padding:36px max(24px,calc((100% - 1380px)/2));border-bottom:5px solid #d2a84b}}header h1{{font-size:30px;margin:0 0 8px}}header p{{margin:0;color:#d6dde3}}
    main{{max-width:1380px;margin:auto;padding:24px}}section{{background:var(--paper);border:1px solid var(--line);border-radius:6px;margin-bottom:18px;padding:22px}}h2{{font-size:21px;margin:0 0 14px}}h3{{font-size:18px;margin:0;display:inline}}h4{{font-size:14px;margin:0 0 8px}}.muted{{color:var(--muted)}}
    .kpis{{display:grid;grid-template-columns:repeat(5,minmax(140px,1fr));gap:10px}}.kpi{{background:white;border-left:4px solid var(--blue);padding:14px;border-radius:4px}}.kpi b{{display:block;font-size:23px}}.kpi span{{color:var(--muted)}}
    .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{position:sticky;top:0;background:#f7f8f9;color:#4d5964}}td.num{{text-align:right}}.mono{{font-family:ui-monospace,Consolas,monospace}}.pos{{color:var(--red)}}.neg{{color:var(--green)}}
    .tag{{display:inline-block;border:1px solid #cbd3da;background:#f7f8f9;padding:2px 7px;border-radius:3px;font-size:12px}}.barrow{{display:grid;grid-template-columns:90px 1fr 90px;gap:8px;align-items:center;margin:9px 0}}.track{{height:10px;background:#edf0f2}}.track i{{display:block;height:100%}}.in{{background:var(--red)}}.out{{background:var(--green)}}
    .sector-head{{display:flex;justify-content:space-between;gap:16px;align-items:center;border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:14px}}.sector-head p{{margin:0;color:var(--muted)}}.rank{{color:var(--gold);font-weight:700;margin-right:7px}}.three{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.three>div{{min-width:0;overflow:auto;border:1px solid var(--line);padding:10px;border-radius:4px}}details{{margin-top:14px}}summary{{cursor:pointer;font-weight:700;padding:10px;background:#f7f8f9}}details[open] summary{{margin-bottom:8px}}.constituents{{display:block;max-height:520px;overflow:auto}}a{{color:var(--blue);text-decoration:none}}input{{width:100%;padding:11px;border:1px solid #adb7c0;border-radius:4px;margin:8px 0 14px}}
    .callout{{border-left:4px solid var(--gold);background:#fffaf0;padding:12px 14px}}footer{{max-width:1380px;margin:0 auto 30px;padding:0 24px;color:var(--muted);font-size:12px}}
    @media(max-width:900px){{.kpis{{grid-template-columns:repeat(2,1fr)}}.grid2,.three{{grid-template-columns:1fr}}.sector-head{{display:block}}main{{padding:12px}}section{{padding:14px}}}}
    </style></head><body>
    <header><h1>A股资金流与板块轮动周报</h1><p>2026年7月6日—7月10日 · 全市场资金、行业强弱、龙头/中军/弹性与条件化推演</p></header><main>
    <div class='kpis'><div class='kpi'><b>{len(stocks):,}</b><span>有周收益股票</span></div><div class='kpi'><b class='{('pos' if market_ret>0 else 'neg')}'>{fmt(market_ret)}%</b><span>个股等权周涨跌</span></div><div class='kpi'><b>{fmt(breadth,1)}%</b><span>周上涨家数占比</span></div><div class='kpi'><b class='{('pos' if total_flow>0 else 'neg')}'>{fmt(total_flow)}亿</b><span>5日主力净流合计</span></div><div class='kpi'><b>{int(lim.get('U',0))}/{int(lim.get('Z',0))}/{int(lim.get('D',0))}</b><span>涨停/炸板/跌停次数</span></div></div>
    <section><h2>先看结论</h2><div class='callout'><b>轮动判断框架：</b>“强势延续”要求周收益、五日资金、上涨宽度与周五资金同时为正；“上涨分歧”表示价格强但资金撤退；“潜在轮入”表示周表现仍弱、但周五出现资金回流。它们是下周验证队列，不是确定性预测或买入评级。</div></section>
    <section class='grid2'><div><h2>综合强度前12行业</h2><div class='barlist'>{''.join(bars)}</div><p class='muted'>条形为5日主力净流入/流出，排序综合周收益、资金、上涨宽度和涨跌停结构。</p></div><div><h2>未来动态怎么验证</h2><ol><li><b>延续：</b>周五仍净流入且上涨家数超过55%的板块，观察下周首两日是否继续放量而不出现炸板激增。</li><li><b>切换：</b>“潜在轮入”板块需要周收益转正并连续两日资金净流入才算确认。</li><li><b>退潮：</b>上涨分歧板块若中军转负、炸板增加、龙头跌破本周中枢，优先按退潮处理。</li><li><b>扩散：</b>龙头上涨但板块宽度低于40%，更像个股行情；宽度持续抬升才是板块行情。</li></ol></div></section>
    <section><h2>板块轮动总表</h2><input id='filter' placeholder='搜索板块名称或状态'><div style='overflow:auto'><table id='sectorTable'><thead><tr><th>排名</th><th>行业</th><th>状态</th><th>周涨跌</th><th>5日净流(亿)</th><th>周五净流(亿)</th><th>上涨宽度</th><th>涨/炸/跌</th><th>成分数</th></tr></thead><tbody>{''.join(sec_rows)}</tbody></table></div></section>
    <section><h2>分类说明</h2><p><b>领涨龙头：</b>周收益、主力净流、涨停强度和成交活跃度的综合排名。 <b>中军候选：</b>流通市值、资金承载力、成交额和周表现综合排名，且本周收益与资金均为正。 <b>弹性候选：</b>更偏高收益、高换手和较小流通市值。分类只描述本周市场角色。</p></section>
    {''.join(sections)}
    <section><h2>数据边界</h2><p>{esc(source_note)}</p><p>7月7日全市场日线和7月6日daily_basic端点因服务端超时/502缺失；周收益使用7月6日前收盘至7月10日收盘计算，五日资金流完整，因此核心周度排名可用。主力资金为成交拆单口径，不等同于机构真实持仓变化；北向端点在互联互通披露机制调整后不宜直接解释为当日净买入。</p></section>
    </main><footer>研究用途：本报告是基于市场数据的候选筛选与监测框架，不构成投资建议。数据源：TinyShare兼容接口。</footer>
    <script>const f=document.getElementById('filter');f.addEventListener('input',()=>{{const q=f.value.toLowerCase();document.querySelectorAll('#sectorTable tbody tr').forEach(r=>r.style.display=r.innerText.toLowerCase().includes(q)?'':'none')}});</script></body></html>"""
    return html_text


def main():
    OUT.mkdir(exist_ok=True)
    stocks, flow, limits = load()
    stocks, sectors, daily_sector = analyze(stocks, flow, limits)
    stocks.to_csv(OUT / "stock_week_metrics.csv", index=False, encoding="utf-8-sig")
    sectors.to_csv(OUT / "sector_rotation.csv", index=False, encoding="utf-8-sig")
    daily_sector.to_csv(OUT / "sector_daily_flow.csv", index=False, encoding="utf-8-sig")
    (OUT / "rotation_report.html").write_text(make_html(stocks, sectors, daily_sector, limits), encoding="utf-8")
    summary = {
        "stocks": len(stocks), "sectors": len(sectors),
        "top_sectors": sectors.head(10)[["industry", "state", "week_ret", "net_mf_yi", "breadth"]].to_dict("records"),
        "bottom_sectors": sectors.tail(10)[["industry", "state", "week_ret", "net_mf_yi", "breadth"]].to_dict("records"),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
