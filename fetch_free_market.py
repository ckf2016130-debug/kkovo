"""Free, credential-free close snapshots and coherent Tencent qfq histories.

All dates come from provider records. No spot date is fabricated from the clock.
One complete adjusted window replaces another; adjustment vintages are never merged.
"""
import argparse
import csv
import json
import os
import re
import time
import urllib.request
import urllib.error
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from math import isfinite

DATA = Path('data')
OUT = DATA / 'free'
TZ = timezone(timedelta(hours=8))
HISTORY_LOCK = threading.Lock()
LAST_HISTORY_REQUEST = 0.0
SOURCE_PAUSED = threading.Event()


class SourceRateLimit(RuntimeError):
    pass


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def request(url, encoding='utf-8'):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'})
            with urllib.request.urlopen(req, timeout=12) as response:
                return response.read().decode(encoding)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429, 456):
                SOURCE_PAUSED.set()
                raise SourceRateLimit(f'HTTP {exc.code}; requests paused, retain cache and retry later') from None
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))


def number(value):
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (ValueError, TypeError):
        return None


def symbol(code):
    code = str(code)
    if '.' in code:
        base, exchange = code.split('.')
        return exchange.lower() + base
    base = re.sub(r'\D', '', code).zfill(6)
    return ('sh' if base.startswith('6') else 'bj' if base.startswith(('4', '8', '9')) else 'sz') + base


def canonical(sym):
    return sym[2:] + '.' + sym[:2].upper()


def parse_quote(values, sym):
    if len(values) < 47 or not re.fullmatch(r'\d{14}', values[30]):
        raise ValueError('Quote timestamp missing')
    stamp = datetime.strptime(values[30], '%Y%m%d%H%M%S').replace(tzinfo=TZ)
    if stamp > datetime.now(TZ) + timedelta(minutes=5):
        raise ValueError('Future quote timestamp')
    row = dict(ts_code=canonical(sym), name=values[1], trade_date=values[30][:8],
               timestamp=stamp.isoformat(), close=number(values[3]), pre_close=number(values[4]),
               open=number(values[5]), high=number(values[33]), low=number(values[34]),
               vol=number(values[6]), amount_yi=number(values[37]) / 10000 if number(values[37]) is not None else None,
               pct_chg=number(values[32]), turnover_rate=number(values[38]),
               pe=number(values[39]), circ_mv_yi=number(values[44]), total_mv_yi=number(values[45]), pb=number(values[46]))
    if row['close'] is None or row['close'] <= 0 or row['vol'] is None:
        raise ValueError('Invalid quote')
    row['closed'] = stamp.hour >= 15
    if sym.startswith('sh688'):
        row['vol'] /= 100  # Tencent STAR raw feed is shares; storage/display is lots.
    return row


def quotes(symbols):
    text = request('https://qt.gtimg.cn/q=' + ','.join(symbols), 'gbk')
    result = {}
    for sym, body in re.findall(r'v_([a-z]{2}\d{6})="([^"]*)"', text):
        try:
            result[canonical(sym)] = parse_quote(body.split('~'), sym)
        except ValueError:
            continue
    return result


def parse_history(body, sym, cutoff):
    part = body.get('data', {}).get(sym, {})
    # For securities without corporate actions Tencent returns "day" for qfq too.
    raw = part.get('qfqday') or part.get('day') or []
    if not raw:
        raise ValueError('No daily history')
    result = []
    seen = set()
    for item in raw:
        day = datetime.strptime(item[0], '%Y-%m-%d').strftime('%Y%m%d')
        if day > cutoff:
            continue
        values = [number(v) for v in item[1:6]]
        if len(values) != 5 or any(v is None for v in values):
            raise ValueError('Non-numeric OHLCV')
        op, close, high, low, vol = values
        if sym.startswith('sh688'):
            vol /= 100
        if day in seen or vol < 0 or low <= 0 or not low <= min(op, close) <= max(op, close) <= high:
            raise ValueError('Invalid OHLCV or duplicate date')
        seen.add(day)
        result.append([day, op, high, low, close, vol])
    result.sort(key=lambda r: r[0])
    if not result:
        raise ValueError('No completed session history')
    return result[-180:]


def history(sym, cutoff):
    global LAST_HISTORY_REQUEST
    if SOURCE_PAUSED.is_set():
        raise SourceRateLimit('Source requests paused')
    with HISTORY_LOCK:
        wait = float(os.getenv('FREE_REQUEST_INTERVAL', '.65')) - (time.monotonic() - LAST_HISTORY_REQUEST)
        if wait > 0:
            time.sleep(wait)
        LAST_HISTORY_REQUEST = time.monotonic()
    end = datetime.strptime(cutoff, '%Y%m%d')
    start = (end - timedelta(days=400)).strftime('%Y-%m-%d')
    params = f'{sym},day,{start},{end:%Y-%m-%d},180,qfq'
    body = json.loads(request('https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=' + params))
    return parse_history(body, sym, cutoff)


def extend_history(old, quote, previous_session, cutoff):
    """Append a verified close only if the prior session and ex-right reference agree."""
    rows = old.get('rows', [])
    if old.get('basis') != 'qfq' or not rows or rows[-1][0] != previous_session:
        return None
    if quote.get('trade_date') != cutoff or not quote.get('closed') or not quote.get('pre_close'):
        return None
    if abs(rows[-1][4] - quote['pre_close']) > .00001:
        return None  # Corporate action: fetch an entirely new adjusted window.
    values = [quote.get(k) for k in ['open', 'high', 'low', 'close', 'vol']]
    if any(v is None for v in values):
        return None
    op, high, low, close, vol = values
    if low <= 0 or vol <= 0 or not low <= min(op, close) <= max(op, close) <= high:
        return None
    return (rows + [[cutoff, op, high, low, close, vol]])[-180:]


def universe():
    rows = {item['ts_code']: item for item in read_json(OUT / 'universe.json', [])}
    path = DATA / 'stock_basic.csv'
    if path.exists():
        with path.open(encoding='utf-8-sig', newline='') as handle:
            for item in csv.DictReader(handle):
                if re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', item.get('ts_code', '')):
                    rows[item['ts_code']] = dict(ts_code=item['ts_code'], name=item.get('name'), industry=item.get('industry') or '未分类')
    # This snapshot is only a symbol/name discovery source, never historical prices.
    path = DATA / 'intraday' / 'spot.csv'
    if path.exists():
        with path.open(encoding='utf-8-sig', newline='') as handle:
            for item in csv.DictReader(handle):
                sym = symbol(item['ts_code'])
                if re.fullmatch(r'(sh6|sz[03]|bj[489])\d{5}', sym):
                    code = canonical(sym)
                    rows.setdefault(code, dict(ts_code=code, name=item.get('name'), industry='未分类'))
    if len(rows) < 3000:
        raise RuntimeError('Stock universe is incomplete; cannot claim all-market coverage')
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=0, help='Bounded diagnostic only; never publish as all-market')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    members = universe()
    index_quotes = quotes(['sh000001', 'sz399001', 'sz399006'])
    if len(index_quotes) < 2:
        raise RuntimeError('Cannot establish market session from index timestamps')
    latest_quote = max(x['trade_date'] for x in index_quotes.values())
    now = datetime.now(TZ)
    cutoff = latest_quote
    if latest_quote == now.strftime('%Y%m%d') and (now.hour < 15 or not all(x['closed'] for x in index_quotes.values())):
        cutoff = (now.date() - timedelta(days=1)).strftime('%Y%m%d')
    benchmark = history('sh000001', cutoff)
    cutoff = benchmark[-1][0]
    previous_session = benchmark[-2][0]
    codes = sorted(members)
    if args.limit:
        codes = sorted(set(codes[:args.limit]) | {'605179.SH', '603221.SH', '002408.SZ', '600518.SH'})
    snapshot = {}
    errors = []
    batches = [codes[i:i+80] for i in range(0, len(codes), 80)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for task in as_completed([pool.submit(quotes, [symbol(c) for c in batch]) for batch in batches]):
            try:
                snapshot.update(task.result())
            except Exception as exc:
                errors.append('quotes: ' + type(exc).__name__)
    write_json(OUT / 'quotes.json', snapshot)
    write_json(OUT / 'universe.json', list(members.values()))
    write_json(OUT / 'indices.json', list(index_quotes.values()))
    print(f'FREE source session={cutoff}, universe={len(codes)}, quotes={len(snapshot)}', flush=True)
    successes = 0
    checked = []

    def one(code):
        path = OUT / 'history' / f'{code}.json'
        old = read_json(path, {})
        quote = snapshot.get(code, {})
        if old.get('basis') == 'qfq' and old.get('checked_session') == cutoff and old.get('rows') and (
            quote.get('trade_date') != cutoff or not quote.get('closed') or
            (old['rows'][-1][0] == cutoff and abs(old['rows'][-1][4] - quote['close']) <= .011 and
             abs(old['rows'][-1][5] - quote['vol']) <= 1)):
            return code, old, 'cached'
        rows = extend_history(old, quote, previous_session, cutoff)
        if rows is None:
            rows = history(symbol(code), cutoff)
        if quote.get('trade_date') == cutoff and quote.get('closed') and quote.get('vol', 0) > 0:
            if rows[-1][0] != cutoff or abs(rows[-1][4] - quote['close']) > 0.011:
                raise ValueError('History disagrees with completed quote')
        result = dict(ts_code=code, source='腾讯公开行情', basis='qfq', volume_unit='手',
                      checked_session=cutoff, retrieved_at=datetime.now(TZ).isoformat(), rows=rows)
        write_json(path, result)
        return code, result, 'fetched'

    with ThreadPoolExecutor(max_workers=int(os.getenv('FREE_WORKERS', '6'))) as pool:
        tasks = {pool.submit(one, code): code for code in codes}
        for task in as_completed(tasks):
            code = tasks[task]
            try:
                _, record, how = task.result()
                successes += 1
                checked.append(code)
            except Exception as exc:
                errors.append(code + ': ' + type(exc).__name__ + ': ' + str(exc)[:160])
                if len(errors) <= 12:
                    print('FREE error: ' + errors[-1], flush=True)
            if (successes + len(errors)) % 250 == 0:
                print(f'FREE progress: checked={successes}, errors={len(errors)}, seconds={int(time.monotonic()-started)}', flush=True)
    manifest = dict(source='腾讯公开行情 / 前复权日线', price_basis='qfq', volume_unit='手',
                    as_of=cutoff, generated_at=datetime.now(TZ).isoformat(), expected_stocks=len(codes),
                    successful_checks=successes, checked_codes=checked, errors=errors,
                    diagnostic=bool(args.limit), seconds=round(time.monotonic()-started, 1))
    write_json(OUT / 'manifest.json', manifest)
    print(json.dumps({k:v for k,v in manifest.items() if k not in ('checked_codes', 'errors')}, ensure_ascii=False), flush=True)
    if successes < len(codes) * .9:
        raise RuntimeError('Free history coverage below 90%; old complete per-stock caches retained')


if __name__ == '__main__':
    main()
