"""Publish a free price/volume dashboard without mixing paid historical metrics."""
import hashlib
import json
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median

from dashboard_delivery import package_dashboard
from fetch_free_market import OUT as DATA, TZ, read_json, write_json
from pattern_watchlist import build_pattern_pool

OUT = Path('output/market_dashboard')


def build(output=OUT, diagnostic=False):
    manifest = read_json(DATA / 'manifest.json', {})
    if not manifest or (manifest.get('diagnostic') and not diagnostic):
        raise RuntimeError('No verified full-market free data manifest')
    cutoff = manifest['as_of']
    members = read_json(DATA / 'universe.json', [])
    quotes = read_json(DATA / 'quotes.json', {})
    checked = set(manifest['checked_codes'])
    output.mkdir(parents=True, exist_ok=True)
    history_out = output / 'free-history'
    history_out.mkdir(exist_ok=True)
    stocks, prices, current = [], [], []
    for member in members:
        code = member['ts_code']
        history = read_json(DATA / 'history' / f'{code}.json', {})
        rows = history.get('rows', [])
        quote = quotes.get(code, {})
        ready = code in checked and bool(rows) and rows[-1][0] == cutoff
        quote_ready = quote.get('closed') and quote.get('trade_date') == cutoff
        item = dict(member, name=quote.get('name') or member.get('name') or code,
                    history_date=rows[-1][0] if rows else None, history_days=len(rows),
                    current=ready, quote_date=quote.get('trade_date'), quote_current=bool(quote_ready),
                    close=rows[-1][4] if rows else None, pct_chg=None, ret_5d=None,
                    vol_ratio=None, turnover_rate=None, amount_yi=None, circ_mv_yi=None, pe=None, pb=None)
        if rows:
            content = json.dumps(rows, separators=(',', ':')).encode('utf-8')
            filename = code + '-' + hashlib.sha256(content).hexdigest()[:12] + '.json'
            (history_out / filename).write_bytes(content)
            item['history_path'] = 'free-history/' + filename
        if ready:
            if len(rows) > 1:
                item['pct_chg'] = round((rows[-1][4] / rows[-2][4] - 1) * 100, 3)
            if len(rows) > 5:
                item['ret_5d'] = round((rows[-1][4] / rows[-6][4] - 1) * 100, 3)
                previous_volume = mean(r[5] for r in rows[-6:-1])
                item['vol_ratio'] = round(rows[-1][5] / previous_volume, 3) if previous_volume > 0 else None
            for i, row in enumerate(rows):
                prices.append(dict(ts_code=code, trade_date=row[0], open=row[1], high=row[2], low=row[3],
                                   close=row[4], vol=row[5], pct_chg=(row[4]/rows[i-1][4]-1)*100 if i else None))
            current.append(item)
        if quote_ready:
            for field in ['turnover_rate', 'amount_yi', 'circ_mv_yi', 'pe', 'pb']:
                item[field] = quote.get(field)
            if ready:
                item['pct_chg'] = quote.get('pct_chg')
        stocks.append(item)
    fresh_coverage = len(current) / max(1, manifest['expected_stocks'])
    if not diagnostic and (fresh_coverage < .85 or manifest['successful_checks'] / manifest['expected_stocks'] < .9):
        raise RuntimeError('Insufficient current free-history coverage; refuse misleading market summary')
    sectors = []
    grouped = defaultdict(list)
    for stock in current:
        grouped[stock['industry']].append(stock)
    for industry, part in grouped.items():
        values = [s['ret_5d'] for s in part if s['ret_5d'] is not None]
        daily = [s['pct_chg'] for s in part if s['pct_chg'] is not None]
        sectors.append(dict(industry=industry, count=len(part), return_count=len(values),
                            ret_5d=round(mean(values), 3) if values else None,
                            median_5d=round(median(values), 3) if values else None,
                            breadth=round(sum(x > 0 for x in daily)/len(daily)*100, 2) if daily else None))
    sectors.sort(key=lambda s: (s['ret_5d'] is not None, s['ret_5d'] or 0), reverse=True)
    pool = build_pattern_pool(prices, stocks)
    pool['price_basis'] = 'qfq'
    pool['version'] += '-qfq'
    pool['limitations'][1] = '腾讯前复权日线，成交量单位为手；整段窗口重算。除权后历史价及形态阈值可能调整，不作为历史回测结果。'
    pool['limitations'].append('仅对日线截止日一致、已核验的股票计算；行情落后或抓取失败的股票不进入当日池。')
    write_json(output / 'pattern_pool.json', pool)
    daily = [s['pct_chg'] for s in current if s['pct_chg'] is not None]
    amount = [s['amount_yi'] for s in current if s['amount_yi'] is not None]
    age = (datetime.now(TZ).date() - datetime.strptime(cutoff, '%Y%m%d').date()).days
    summary = dict(as_of=cutoff, generated_at=manifest['generated_at'], source=manifest['source'],
                   expected_stocks=manifest['expected_stocks'], current_stocks=len(current),
                   excluded_stocks=manifest['expected_stocks']-len(current), checked_stocks=manifest['successful_checks'],
                   history_rows=len(prices), advance=sum(x>0 for x in daily), decline=sum(x<0 for x in daily),
                   flat=sum(x==0 for x in daily), breadth_count=len(daily), amount_yi=round(sum(amount), 2) if amount else None,
                   amount_count=len(amount), coverage=round(fresh_coverage*100, 2), age_days=age,
                   indices=read_json(DATA / 'indices.json', []),
                   errors=manifest['errors'], archive_available=(output/'archive/index.html').exists(),
                   limitations=['行业分类沿用原基础资料；新增股票无法匹配时显示未分类。',
                                '当日涨跌幅优先使用同日收盘快照，缺失时用前复权相邻收盘价计算。',
                                '历史成交额缺失时不估算；成交量柱采用源数据手数。',
                                '资金流、财务、筹码和公告不参与当日形态判断。'])
    status = dict(ok=age<=4, latest_daily_date=cutoff, coverage=summary['coverage'],
                  current_stocks=len(current), errors=[] if age<=4 else ['免费行情超过4天，需检查数据源或休市安排'])
    write_json(output / 'data_status.json', status)
    write_json(output / 'market_context.json', summary)
    vendor = output / 'vendor'
    vendor.mkdir(exist_ok=True)
    for name in ['echarts.min.js', 'dashboard-loader.js', 'pattern-watchlist.js', 'free-dashboard.js']:
        shutil.copy2(Path('vendor')/name, vendor/name)
    template = Path('free_dashboard_template.html').read_text(encoding='utf-8')
    datasets = dict(stocks=stocks, sectors=sectors, summary=summary,
                    __PATTERN_META__={k:v for k,v in pool.items() if k!='rows'}, __PATTERN_ROWS__=pool['rows'])
    (output / 'index.html').write_text(package_dashboard(template, datasets, output, vendor), encoding='utf-8')
    print(f'FREE BUILD: cutoff={cutoff}, current={len(current)}, rows={len(prices)}, pool={len(pool["rows"])}, coverage={summary["coverage"]}%', flush=True)
    return summary


if __name__ == '__main__':
    build()
