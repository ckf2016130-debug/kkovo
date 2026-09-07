import unittest
from datetime import date, timedelta
from pattern_watchlist import scan_stock, build_pattern_pool


def sample():
    rows=[]; day=date(2026,1,5)
    values=[(10,100)]*20+[(11,300)]+[(10.4,200)]*3+[(10.3,70)]*3+[(10.8,220)]
    for close,vol in values:
        while day.weekday()>=5: day+=timedelta(days=1)
        prev=rows[-1]['close'] if rows else close
        rows.append(dict(date=day.strftime('%Y%m%d'),open=close,close=close,high=close+.05,low=close-.05,vol=vol,pct_chg=(close/prev-1)*100))
        day+=timedelta(days=1)
    return rows


class PatternTests(unittest.TestCase):
    def test_causal_stages(self):
        rows=sample()
        self.assertEqual(scan_stock(rows[:21])[-1]['status'],'initial')
        self.assertEqual(scan_stock(rows[:-1])[-1]['status'],'watch')
        self.assertEqual(scan_stock(rows)[-1]['status'],'confirmed')
        self.assertEqual(scan_stock(rows)[-1]['status_date'],rows[-1]['date'])

    def test_new_low_invalidates(self):
        rows=sample(); last=dict(rows[-1],date='20260302',close=7,open=7,low=6.9,high=7.1,pct_chg=(7/10.8-1)*100)
        # Consecutive business session, to exercise price invalidation not gap.
        d=date.fromisoformat(rows[-1]['date'][:4]+'-'+rows[-1]['date'][4:6]+'-'+rows[-1]['date'][6:])+timedelta(days=1)
        while d.weekday()>=5:d+=timedelta(days=1)
        last['date']=d.strftime('%Y%m%d'); rows.append(last)
        self.assertEqual(scan_stock(rows)[-1]['status'],'invalid')
        self.assertIn('75%',scan_stock(rows)[-1]['reason'])

    def test_ex_rights_resets(self):
        rows=sample();rows[-1]=dict(rows[-1],open=5.4,close=5.4,high=5.45,low=5.35,pct_chg=4.85)
        self.assertEqual(scan_stock(rows)[-1]['status'],'invalid')
        self.assertIn('跳变',scan_stock(rows)[-1]['reason'])

    def test_no_signal_with_missing_history_or_bad_rows(self):
        rows=[dict(r,trade_date=r['date'],ts_code='000001.SZ') for r in sample()[:10]]
        rows.append(dict(rows[-1],vol=None))
        result=build_pattern_pool(rows,[])
        self.assertEqual(result['rows'],[])
        self.assertEqual(result['insufficient_history'],1)
        self.assertEqual(result['invalid_rows'],1)

    def test_stale_and_duplicate_days(self):
        rows=[dict(r,trade_date=r['date'],ts_code='000001.SZ') for r in sample()]
        rows+=rows[-2:]
        rows.append(dict(rows[-1],ts_code='000002.SZ',trade_date='20260301'))
        result=build_pattern_pool(rows,[])
        self.assertTrue(result['rows'][0]['stale'])
        self.assertEqual(result['rows'][0]['coverage'],len(sample()))

    def test_future_does_not_create_earlier_confirmation(self):
        rows=sample();prefix=scan_stock(rows[:-1])[-1]
        self.assertNotEqual(prefix['status'],'confirmed')
        full=scan_stock(rows)[-1]
        self.assertEqual(prefix['event_date'],full['event_date'])
        self.assertGreater(full['status_date'],prefix['status_date'])


if __name__=='__main__':unittest.main()
