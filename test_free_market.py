import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import build_free_dashboard as dashboard
from fetch_free_market import parse_history, parse_quote, symbol, extend_history, TZ


class FreeMarketTests(unittest.TestCase):
    def test_history_is_adjusted_and_bounded_to_completed_dates(self):
        body={'data':{'sh603221':{'qfqday':[
            ['2026-09-07','10','11','12','9','1234'],
            ['2026-09-08','11','12','13','10','1400']],
            'day':[['2026-09-07','100','110','120','90','1234']]}}}
        self.assertEqual(parse_history(body,'sh603221','20260907'),[['20260907',10,12,9,11,1234]])

    def test_bad_history_never_overwrites_cache(self):
        for rows in [[], [['2026-09-07','10','11','9','12','1']],
                     [['2026-09-07','10','11','12','9','1']]*2]:
            with self.assertRaises(ValueError):
                parse_history({'data':{'sh603221':{'qfqday':rows}}},'sh603221','20260908')

    def test_quote_timestamp_and_units(self):
        quote=['']*48
        for index,value in {1:'样本',3:'10',4:'9',5:'9.5',6:'1000',30:'20260820150001',
                            32:'11.11',33:'10',34:'9',37:'100000',38:'2',44:'80',45:'100',46:'2'}.items():
            quote[index]=value
        parsed=parse_quote(quote,'sh603221')
        self.assertEqual(parsed['trade_date'],'20260820')
        self.assertEqual(parsed['amount_yi'],10)
        self.assertEqual(parsed['vol'],1000)
        quote[30]=''
        with self.assertRaises(ValueError):parse_quote(quote,'sh603221')

    def test_exchange_mapping(self):
        self.assertEqual(symbol('920001'),'bj920001')
        self.assertEqual(symbol('603221.SH'),'sh603221')
        self.assertEqual(symbol('000001'),'sz000001')

    def test_incremental_quote_cannot_bridge_gap_or_corporate_action(self):
        old={'basis':'qfq','rows':[['20260907',10,11,9,10,100]]}
        quote=dict(trade_date='20260908',closed=True,open=10,high=12,low=9,close=11,vol=200,pre_close=10)
        self.assertEqual(extend_history(old,quote,'20260907','20260908')[-1],['20260908',10,12,9,11,200])
        self.assertIsNone(extend_history(old,quote,'20260906','20260908'))
        quote['pre_close']=9.9
        self.assertIsNone(extend_history(old,quote,'20260907','20260908'))
        quote['pre_close']=10;quote['closed']=False
        self.assertIsNone(extend_history(old,quote,'20260907','20260908'))

    def test_star_share_volume_converts_to_lots(self):
        body={'data':{'sh688981':{'qfqday':[['2026-09-08','100','101','102','99','27764164']]}}}
        self.assertEqual(parse_history(body,'sh688981','20260908')[0][5],277641.64)
        body={'data':{'sh689009':{'qfqday':[['2026-09-08','40','40','41','39','8979553']]}}}
        self.assertEqual(parse_history(body,'sh689009','20260908')[0][5],89795.53)

    def test_incomplete_manifest_refuses_full_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';data.mkdir()
            (data/'manifest.json').write_text(json.dumps({'diagnostic':True}))
            with patch.object(dashboard,'DATA',data),self.assertRaises(RuntimeError):
                dashboard.build(root/'site')

    def test_unchecked_histories_excluded_and_no_flow_fabrication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';(data/'history').mkdir(parents=True)
            day=datetime.now(TZ).strftime('%Y%m%d')
            (data/'manifest.json').write_text(json.dumps(dict(as_of=day,checked_codes=['603221.SH'],
                expected_stocks=2,successful_checks=1,generated_at='test',source='test fixture',errors=[])))
            members=[dict(ts_code=c,name=c,industry='行业') for c in ['603221.SH','600518.SH']]
            (data/'universe.json').write_text(json.dumps(members))
            for item in members:
                (data/'history'/f'{item["ts_code"]}.json').write_text(json.dumps({'rows':[[day,10,11,9,10,1000]]}))
            with patch.object(dashboard,'DATA',data):
                status=dashboard.build(root/'site',diagnostic=True)
            self.assertEqual(status['current_stocks'],1)
            self.assertEqual(status['excluded_stocks'],1)
            self.assertEqual(status['amount_count'],0)
            self.assertNotIn('net_mf',status)
            with patch.object(dashboard,'DATA',data),self.assertRaises(RuntimeError):
                dashboard.build(root/'rejected')


if __name__=='__main__':unittest.main()
