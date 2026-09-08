"""Read-only source probes. Never log credentials or raw provider responses."""
import json
from datetime import datetime, timedelta, timezone

from tushare_proxy import create_pro


def main():
    pro = create_pro(timeout=15)
    today = datetime.now(timezone(timedelta(hours=8))).date()
    end = today.strftime('%Y%m%d')
    start = (today - timedelta(days=20)).strftime('%Y%m%d')
    recent = today - timedelta(days=1)
    while recent.weekday() >= 5:
        recent -= timedelta(days=1)
    probes = [
        ('trade_cal', dict(exchange='SSE', start_date=start, end_date=end)),
        ('index_daily', dict(ts_code='000001.SH', start_date=start, end_date=end)),
        ('daily', dict(trade_date=recent.strftime('%Y%m%d'))),
        ('daily', dict(trade_date='20260820')),
        ('daily_basic', dict(trade_date=recent.strftime('%Y%m%d'))),
        ('moneyflow', dict(trade_date=recent.strftime('%Y%m%d'))),
    ]
    for api, params in probes:
        record = dict(api=api, params=params)
        try:
            frame = pro.query(api, **params)
            record.update(rows=len(frame), columns=list(frame.columns))
            if 'trade_date' in frame and not frame.empty:
                record['latest_date'] = str(frame.trade_date.max())
        except Exception as exc:
            record['error_type'] = type(exc).__name__
        print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
