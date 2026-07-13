from pathlib import Path

import akshare as ak
import pandas as pd


OUT = Path("data")
TARGET = OUT / "overseas_daily.csv"


def main():
    rows = []
    sources = {
        ".INX": ("标普500", "美股宽基"),
        ".IXIC": ("纳斯达克", "海外科技"),
        ".DJI": ("道琼斯", "美股宽基"),
    }
    for symbol, (asset, group) in sources.items():
        try:
            frame = ak.index_us_stock_sina(symbol=symbol)
            frame = frame.rename(columns={"date": "trade_date"})
            frame["asset"] = asset
            frame["group"] = group
            rows.append(frame[["trade_date", "asset", "group", "open", "high", "low", "close", "volume"]])
        except Exception as exc:
            print(f"FAIL {asset}: {type(exc).__name__}: {exc}")
    for asset, group in [("费城半导体", "半导体"), ("中国台湾加权", "半导体"), ("日经225", "电子与汽车"), ("韩国综合", "半导体")]:
        try:
            if asset == "费城半导体":
                frame = ak.macro_global_sox_index().rename(columns={"日期": "trade_date", "最新值": "close"})
                frame["open"] = frame["close"]
                frame["high"] = frame["close"]
                frame["low"] = frame["close"]
                frame["volume"] = 0
            else:
                name = {"中国台湾加权": "中国台湾加权指数", "日经225": "日经225指数", "韩国综合": "首尔综合指数"}[asset]
                frame = ak.index_global_hist_sina(symbol=name)
            frame = frame.rename(columns={"日期": "trade_date", "date": "trade_date", "收盘": "close", "收盘价": "close"})
            if "trade_date" not in frame or "close" not in frame:
                continue
            frame["asset"] = asset
            frame["group"] = group
            frame["open"] = frame.get("open", frame["close"])
            frame["high"] = frame.get("high", frame["close"])
            frame["low"] = frame.get("low", frame["close"])
            frame["volume"] = frame.get("volume", 0)
            rows.append(frame[["trade_date", "asset", "group", "open", "high", "low", "close", "volume"]])
        except Exception as exc:
            print(f"FAIL {asset}: {type(exc).__name__}: {exc}")
    if not rows:
        if TARGET.exists() and TARGET.stat().st_size > 0:
            print(f"NO NEW OVERSEAS DATA: keep {TARGET}")
            return
        raise RuntimeError("no overseas source returned data")
    result = pd.concat(rows, ignore_index=True)
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce").dt.strftime("%Y%m%d")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result = result.dropna(subset=["trade_date", "asset", "close"]).drop_duplicates(["asset", "trade_date"])
    result = result.sort_values(["asset", "trade_date"]).groupby("asset", group_keys=False).tail(120)
    result.to_csv(TARGET, index=False, encoding="utf-8-sig")
    print(f"OK overseas: {len(result):,} rows -> {TARGET}")


if __name__ == "__main__":
    main()
