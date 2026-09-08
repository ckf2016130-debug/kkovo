"""Read-only source probes. Never log credentials or raw provider responses."""
import json
import os
import requests
from datetime import datetime, timedelta, timezone

from tushare_proxy import create_pro


def main():
    pro = create_pro(timeout=15)
    today = datetime.now(timezone(timedelta(hours=8))).date()
    end = today.strftime('%Y%m%d')
    start = (today - timedelta(days=20)).strftime('%Y%m%d')
    token = os.getenv('TUSHARE_TOKEN') or os.getenv('TINYSHARE_TOKEN')
    try:
        response = requests.post(os.environ['TUSHARE_API_URL'], json={
            'api_name': 'trade_cal', 'token': token,
            'params': dict(exchange='SSE', start_date=start, end_date=end),
            'fields': ''}, timeout=15)
        report = dict(http_status=response.status_code,
                      content_type=response.headers.get('Content-Type'))
        try:
            body = response.json()
            report['keys'] = list(body) if isinstance(body, dict) else []
            if isinstance(body, dict):
                report['code'] = body.get('code')
                message = str(body.get('msg') or body.get('message') or '')
                if token:
                    message = message.replace(token, '[redacted]')
                report['message'] = message[:240]
        except ValueError:
            report['format'] = 'non-JSON response'
        print(json.dumps(report, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(json.dumps(dict(transport_error=type(exc).__name__)), flush=True)
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
