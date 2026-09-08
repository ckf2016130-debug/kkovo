"""Small public-source check without credentials, persistent data, or publishing."""
import json
from fetch_free_market import history, quotes


def main():
    rows = quotes(['sh000001', 'sh603221', 'sz002408'])
    if len(rows) != 3:
        raise RuntimeError('Free quote coverage check failed')
    for code, row in rows.items():
        symbol = code[-2:].lower() + code[:6]
        bars = history(symbol, row['trade_date'])
        print(json.dumps(dict(code=code, quote_date=row['trade_date'], history_date=bars[-1][0],
                              rows=len(bars), close=bars[-1][4], quote_close=row['close']), ensure_ascii=False))


if __name__ == '__main__':
    main()
