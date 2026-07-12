import json
import os
from pathlib import Path

import tinyshare as ts


PERIOD = "20260331"
OUT = Path("data/fundamentals")
OUT.mkdir(parents=True, exist_ok=True)

ts.set_token(os.environ["TINYSHARE_TOKEN"])
pro = ts.pro_api()
pro.timeout = 45

manifest = []
for api in ["fina_indicator_vip", "income_vip", "balancesheet_vip", "cashflow_vip", "express_vip"]:
    try:
        df = pro.query(api, period=PERIOD)
        path = OUT / f"{api}_{PERIOD}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        manifest.append({"api": api, "rows": len(df), "file": str(path), "error": None})
        print("OK", api, len(df), path)
    except Exception as exc:
        manifest.append({"api": api, "rows": 0, "file": None, "error": str(exc)})
        print("FAIL", api, type(exc).__name__, exc)

(OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
