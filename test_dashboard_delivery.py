import json
import re
import tempfile
import unittest
from pathlib import Path
from dashboard_delivery import package_dashboard


class DeliveryTests(unittest.TestCase):
    def test_chunk_round_trip_and_script_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);vendor=out/'vendor';vendor.mkdir()
            (vendor/'dashboard-loader.js').write_text('/* loader */')
            (vendor/'review.js').write_text('/* review */')
            template='<html><body><script>const x=window.__MARKET_DATA__.rows;</script><script>window.result=x.length;</script><script defer src="vendor/review.js"></script></body></html>'
            rows=[{'name':'中文'*100,'index':i} for i in range(900)]
            page=package_dashboard(template,{'rows':rows,'meta':{'date':'20260908'}},out,vendor)
            manifest=json.loads(re.search(r'type="application/json">(.*?)</script>',page).group(1))
            paths=manifest['datasets']['rows']['paths']
            rebuilt=[r for p in paths for r in json.loads((out/p).read_text(encoding='utf-8'))]
            self.assertEqual(rows,rebuilt)
            self.assertGreater(len(paths),1)
            self.assertTrue(all((out/p).stat().st_size<=120000 for p in paths))
            self.assertLess(len(page.encode('utf-8')),10000)
            self.assertNotIn('<script>const x=',page)
            self.assertIn('const x=',(out/manifest['scripts'][0]).read_text())
            self.assertIn('window.result=',(out/manifest['scripts'][1]).read_text())
            self.assertTrue(manifest['extensions'][0].startswith('vendor/review.js?v='))
            self.assertIn('dashboardLoadRetry',page)

    def test_empty_arrays_and_immutable_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);(out/'dashboard-loader.js').write_text('/* loader */')
            a=package_dashboard('<body></body>',{'empty':[]},out,out)
            b=package_dashboard('<body></body>',{'empty':[]},out,out)
            self.assertEqual(a,b)
            self.assertIn('"paths": []',a)


if __name__=='__main__':unittest.main()
