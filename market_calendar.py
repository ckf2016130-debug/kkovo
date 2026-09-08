"""Resolve trading dates from verified provider data, never guessed weekdays."""
from datetime import datetime, timedelta

import pandas as pd
from tushare_proxy import MarketSourceError


def trading_dates(pro, end, count=60):
    start = end - timedelta(days=120)
    bounds = dict(start_date=start.strftime('%Y%m%d'), end_date=end.strftime('%Y%m%d'))
    failures = []
    for api, params, column in [
        ('trade_cal', dict(exchange='SSE', **bounds), 'cal_date'),
        ('index_daily', dict(ts_code='000001.SH', **bounds), 'trade_date'),
    ]:
        try:
            frame = pro.query(api, **params)
            required = {column, 'is_open'} if api == 'trade_cal' else {column, 'close'}
            if frame.empty or not required.issubset(frame.columns):
                failures.append(f'{api}: empty or missing required columns')
                continue
            if api == 'trade_cal':
                frame = frame[pd.to_numeric(frame.is_open, errors='coerce') == 1]
            else:
                frame = frame[pd.to_numeric(frame.close, errors='coerce') > 0]
            dates = set()
            for value in frame[column].astype(str):
                try:
                    parsed = datetime.strptime(value, '%Y%m%d').date()
                except ValueError:
                    continue
                if start <= parsed <= end:
                    dates.add(parsed.strftime('%Y%m%d'))
            if dates:
                print(f'Trading dates source: {api}; latest: {max(dates)}', flush=True)
                return sorted(dates)[-count:]
            failures.append(f'{api}: no valid dates in requested range')
        except MarketSourceError as exc:
            failures.append(str(exc))
        except Exception as exc:
            failures.append(f'{api}: {type(exc).__name__}')
    raise RuntimeError('Market source unavailable; ' + '; '.join(failures))


def validate_daily(frame, requested_date, min_rows=3000):
    required = {'ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount', 'pct_chg', 'pre_close'}
    if not required.issubset(frame.columns) or len(frame) < min_rows:
        raise ValueError('Daily response is incomplete; previous snapshot retained')
    if frame.ts_code.duplicated().any() or set(frame.trade_date.astype(str)) != {requested_date}:
        raise ValueError('Daily response has duplicate stocks or mismatched dates')
    if pd.to_numeric(frame.close, errors='coerce').isna().any() or (pd.to_numeric(frame.close) <= 0).any():
        raise ValueError('Daily response has invalid closing prices')
