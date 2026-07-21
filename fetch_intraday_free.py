import json
from datetime import datetime
from pathlib import Path

import akshare as ak
import pandas as pd


OUT = Path("data/intraday")
TARGET = OUT / "spot.csv"
MANIFEST = OUT / "manifest.json"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now().astimezone().isoformat(timespec="seconds")
    result = {"source": "AKShare / 东方财富公开行情", "retrieved_at": retrieved_at, "valid": False, "rows": 0, "error": None}
    errors = []
    sources = [
        ("AKShare / 东方财富公开行情", ak.stock_zh_a_spot_em),
        ("AKShare / 新浪公开行情", ak.stock_zh_a_spot),
        ("AKShare / 腾讯公开行情", ak.stock_zh_a_spot_tx),
    ]
    try:
        frame = None
        source_name = ""
        for candidate_name, fetcher in sources:
            try:
                candidate = fetcher()
                if not candidate.empty and {"代码", "名称", "最新价", "涨跌幅", "成交额"}.issubset(candidate.columns):
                    frame, source_name = candidate, candidate_name
                    break
            except Exception as exc:
                errors.append(f"{candidate_name}: {type(exc).__name__}: {exc}")
        if frame is None:
            raise RuntimeError("; ".join(errors) or "all free spot sources returned no data")
        required = {"代码", "名称", "最新价", "涨跌幅", "成交额"}
        if frame.empty or not required.issubset(frame.columns):
            raise ValueError("free spot response is empty or missing required fields")
        frame = frame.rename(columns={"代码": "ts_code", "名称": "name", "最新价": "close", "涨跌幅": "pct_chg", "成交额": "amount", "成交量": "vol", "换手率": "turnover_rate", "量比": "volume_ratio", "最高": "high", "最低": "low", "今开": "open", "昨收": "pre_close"})
        for column in ["close", "pct_chg", "amount"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["ts_code"] = frame["ts_code"].astype(str).str.extract(r"(\d{6})", expand=False)
        frame = frame[frame["ts_code"].notna()]
        frame = frame[(frame["close"] > 0) & frame["pct_chg"].notna() & (frame["amount"] >= 0)]
        if len(frame) < 3000:
            raise ValueError(f"row-count sanity check failed: {len(frame)}")
        frame["trade_date"] = datetime.now().strftime("%Y%m%d")
        frame["retrieved_at"] = retrieved_at
        frame.to_csv(TARGET, index=False, encoding="utf-8-sig")
        result.update({"valid": True, "rows": len(frame), "source": source_name, "source_errors": errors})
        print(f"OK free intraday snapshot: {len(frame):,} rows -> {TARGET}")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        print(f"FAIL free intraday snapshot: {result['error']}")
    MANIFEST.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not result["valid"] and not TARGET.exists():
        raise RuntimeError(result["error"] or "free intraday snapshot unavailable")


if __name__ == "__main__":
    main()
