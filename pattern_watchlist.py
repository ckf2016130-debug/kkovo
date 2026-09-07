"""Exploratory, causal price/volume states; no trading or ownership inference."""
from collections import defaultdict
from datetime import datetime, timezone
from math import isfinite
from statistics import mean

VERSION = "pv-watch-v0.3"
PARAMS = {"impulse_return": 0.08, "impulse_volume": 1.5,
          "min_pullback": 0.05, "max_pullback": 0.25,
          "tail_volume": 0.75, "max_age": 25, "confirmation_volume": 1.3}


def number(value):
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (TypeError, ValueError):
        return None


def scan_stock(rows):
    """Rows sorted and valid; reset on gaps/corporate-action discontinuities.

    Input prices are raw. pct_chg versus adjacent closes detects corporate-action
    jumps; exclude that window rather than pretend raw prices are adjusted.
    """
    active, events, window = None, [], []
    for raw in rows:
        r = dict(raw)
        if window:
            last = window[-1]
            gap = (datetime.strptime(r['date'], '%Y%m%d') - datetime.strptime(last['date'], '%Y%m%d')).days
            actual = (r['close'] / last['close'] - 1) * 100
            discontinuity = r.get('pct_chg') is None or abs(actual-r['pct_chg']) > .5
            if gap > 14 or discontinuity:
                if active:
                    active.update(status='invalid', status_date=r['date'], latest_date=r['date'], latest_close=r['close'],
                                  reason='数据间断或疑似除权跳变，原形态停止跟踪')
                    events.append(dict(active))
                active, window = None, []
        window.append(r)
        if len(window) < 21:
            continue
        i = len(window)-1
        if active:
            active.update(latest_date=r['date'], latest_close=r['close'], single_price=r['high']==r['low'])
            age = i-active['index']
            active['age'] = age
            reason = None
            if r['low'] < active['high']*(1-PARAMS['max_pullback']):
                reason = '跌破首段高点的75%，撤销形态'
            elif age > PARAMS['max_age']:
                reason = '超过25个交易日，形态到期'
            if reason:
                active.update(status='invalid', status_date=r['date'], reason=reason)
                events.append(dict(active)); active = None
            elif active['status'] != 'confirmed':
                pullback = window[active['index']+1:i+1]
                if len(pullback) >= 6:
                    depth = 1-min(x['low'] for x in pullback)/active['high']
                    contraction = mean(x['vol'] for x in pullback[-3:])/mean(x['vol'] for x in pullback[:3])
                    active.update(depth=round(depth*100,2), contraction=round(contraction,3))
                    if depth >= PARAMS['min_pullback'] and contraction <= PARAMS['tail_volume']:
                        if active['status'] != 'watch':
                            active.update(status='watch', status_date=r['date'], reason='回撤后段三日均量低于前段，进入整理观察')
                # Confirmation uses yesterday's watch state; today's volume is not
                # part of the contraction test that admitted it to the pool.
                if active['status']=='watch' and active['status_date']<r['date']:
                    if r['close']>max(x['high'] for x in window[-4:-1]) and r['vol']>=PARAMS['confirmation_volume']*mean(x['vol'] for x in window[-6:-1]):
                        active.update(status='confirmed', status_date=r['date'], reason='收盘突破此前3日高点，成交量超过此前5日均量1.3倍')
        if active is None and r['close']/window[-4]['close']-1>=PARAMS['impulse_return'] and r['vol']>=PARAMS['impulse_volume']*mean(x['vol'] for x in window[-21:-1]):
            active={'event_date':r['date'],'status_date':r['date'],'index':i,'status':'initial',
                    'high':max(x['high'] for x in window[-3:]),'age':0,'depth':None,'contraction':None,
                    'reason':'3个交易日累计上涨≥8%，当日成交量≥此前20日均量1.5倍'}
        if active:
            active['latest_date']=r['date']
            active['latest_close']=r['close']
            active['single_price']=r['high']==r['low']
    if active:
        events.append(dict(active))
    return [{k:v for k,v in e.items() if k!='index'} for e in events]


def build_pattern_pool(prices, stocks):
    by_code=defaultdict(dict); invalid_rows=0
    for raw in prices:
        code=str(raw.get('ts_code') or '')
        date=str(raw.get('trade_date') or '').replace('-','')
        vals={k:number(raw.get(k)) for k in ['open','high','low','close','vol','pct_chg']}
        try: datetime.strptime(date,'%Y%m%d')
        except ValueError: invalid_rows+=1; continue
        if not code or any(vals[k] is None or vals[k]<=0 for k in ['open','high','low','close','vol']) or not(vals['low']<=min(vals['open'],vals['close'])<=max(vals['open'],vals['close'])<=vals['high']):
            invalid_rows+=1; continue
        by_code[code][date]=dict(vals,date=date)
    asof=max((d for rows in by_code.values() for d in rows),default=None)
    meta={str(s.get('ts_code')):s for s in stocks}
    result=[]; short=0
    for code, data in sorted(by_code.items()):
        rows=[data[d] for d in sorted(data)]
        if len(rows)<21: short+=1; continue
        for event in scan_stock(rows):
            # Keep recent terminal records and current setups, bounded by source sessions.
            if event['status']=='invalid' and event['status_date']<rows[max(0,len(rows)-20)]['date']: continue
            item=meta.get(code,{})
            event.update(ts_code=code,name=item.get('name') or code,industry=item.get('industry') or '未分类',
                         id=f"{code}:{event['event_date']}",coverage=len(rows),
                         stale=rows[-1]['date']!=asof,
                         observed_date=rows[-1]['date'],invalidation=round(event['high']*.75,3))
            result.append(event)
    order={'confirmed':0,'watch':1,'initial':2,'invalid':3}
    result.sort(key=lambda x:(order[x['status']],-int(x['status_date']),x['ts_code']))
    return {'version':VERSION,'as_of':asof,'generated_at':datetime.now(timezone.utc).isoformat(),
            'universe':len(by_code),'insufficient_history':short,'invalid_rows':invalid_rows,
            'params':PARAMS,'rows':result,
            'limitations':['探索规则，未完成独立样本回测；不推断庄家身份或控盘概率。',
                           '未复权日线；检测到除权疑似跳变、涨跌幅缺失或长停牌时重建观察窗口。',
                           '暂未加入自由流通市值、股东户数与公告事件过滤；单一价格日不假定能够买入。']}
