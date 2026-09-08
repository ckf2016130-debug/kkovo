import unittest
from datetime import date
from unittest.mock import Mock

import pandas as pd

from market_calendar import trading_dates, validate_daily
from tushare_proxy import MarketClient, MarketSourceError


class MarketSourceTests(unittest.TestCase):
    def test_auth_failure_is_not_an_empty_table(self):
        client = MarketClient('private-test-value', 'https://example.invalid', 15)
        client._session = Mock()
        client._session.post.return_value = Mock(status_code=401)
        with self.assertRaisesRegex(MarketSourceError, 'HTTP 401') as error:
            client.daily(trade_date='20260907')
        self.assertNotIn('private-test-value', str(error.exception))
        self.assertEqual(client._session.post.call_args.kwargs['timeout'], 15)

    def test_valid_table(self):
        client = MarketClient('test', 'https://example.invalid', 15)
        client._session = Mock()
        client._session.post.return_value = Mock(status_code=200)
        client._session.post.return_value.json.return_value = {
            'code': 0, 'data': {'fields': ['trade_date'], 'items': [['20260907']]}}
        self.assertEqual(client.daily().trade_date.tolist(), ['20260907'])

    def test_error_body_rejected(self):
        client = MarketClient('test', 'https://example.invalid', 15)
        client._session = Mock()
        client._session.post.return_value = Mock(status_code=200)
        client._session.post.return_value.json.return_value = {'error': 'no access'}
        with self.assertRaises(MarketSourceError):
            client.daily()

    def test_calendar_uses_actual_benchmark_dates(self):
        pro = Mock()
        pro.query.side_effect = [pd.DataFrame(), pd.DataFrame({
            'trade_date': ['20260904', '20260907', '20990101', 'garbage'],
            'close': [3000, 3001, 3002, 3003]})]
        self.assertEqual(trading_dates(pro, date(2026, 9, 8)), ['20260904', '20260907'])

    def test_no_dates_are_fabricated_on_outage(self):
        pro = Mock()
        pro.query.return_value = pd.DataFrame()
        with self.assertRaisesRegex(RuntimeError, 'Market source unavailable'):
            trading_dates(pro, date(2026, 9, 8))

    def test_bad_daily_data_rejected(self):
        columns = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount', 'pct_chg', 'pre_close']
        valid = pd.DataFrame([['000001.SZ', '20260907', 1, 1, 1, 1, 1, 1, 0, 1]], columns=columns)
        validate_daily(valid, '20260907', min_rows=1)
        with self.assertRaises(ValueError):
            validate_daily(valid, '20260908', min_rows=1)
        with self.assertRaises(ValueError):
            validate_daily(valid, '20260907')
        with self.assertRaises(ValueError):
            validate_daily(pd.concat([valid, valid]), '20260907', min_rows=1)


if __name__ == '__main__':
    unittest.main()
