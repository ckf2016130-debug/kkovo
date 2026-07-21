import json
import os
from pathlib import Path

from tushare_proxy import create_pro


PERIOD = "20260331"
OUT = Path("data/fundamentals")
OUT.mkdir(parents=True, exist_ok=True)

pro = create_pro(timeout=45)

manifest = []
for api in ["fina_indicator_vip", "income_vip", "balancesheet_vip", "cashflow_vip", "express_vip"]:
    try:
        df = pro.query(api, period=PERIOD)
        path = OUT / f"{api}_{PERIOD}.csv"
        if df.empty:
            if path.exists() and path.stat().st_size > 0:
                manifest.append({"api": api, "rows": 0, "file": str(path), "error": "empty response; previous snapshot kept"})
                print("EMPTY", api, "keep previous snapshot", path)
                continue
            raise ValueError("empty response and no previous snapshot")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        manifest.append({"api": api, "rows": len(df), "file": str(path), "error": None})
        print("OK", api, len(df), path)
    except Exception as exc:
        manifest.append({"api": api, "rows": 0, "file": None, "error": str(exc)})
        print("FAIL", api, type(exc).__name__, exc)

(OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
