from pathlib import Path

import akshare as ak
import pandas as pd


OUT = Path("data")
TARGET = OUT / "overseas_daily.csv"
KEEP_COLUMNS = ["trade_date", "asset", "group", "open", "high", "low", "close", "volume", "source"]


def normalize(frame, asset, group, source):
    if frame is None or frame.empty:
        return None
    frame = frame.copy().rename(columns={
        "日期": "trade_date", "date": "trade_date", "时间": "trade_date",
        "开盘": "open", "开盘价": "open", "最高": "high", "最高价": "high",
        "最低": "low", "最低价": "low", "收盘": "close", "收盘价": "close",
        "最新值": "close", "成交量": "volume",
    })
    if "trade_date" not in frame or "close" not in frame:
        return None
    frame["asset"], frame["group"], frame["source"] = asset, group, source
    for column in ["open", "high", "low"]:
        if column not in frame:
            frame[column] = frame["close"]
    if "volume" not in frame:
        frame["volume"] = 0
    return frame[KEEP_COLUMNS]


def add(rows, frame, asset, group, source):
    result = normalize(frame, asset, group, source)
    if result is not None and not result.empty:
        rows.append(result)
        print(f"OK {asset}: {len(result):,}")


def find_row(frame, keywords):
    if frame is None or frame.empty:
        return None
    name_col = next((x for x in ["名称", "指数名称", "品种名称", "中文名称"] if x in frame), None)
    if not name_col:
        return None
    names = frame[name_col].astype(str)
    for keyword in keywords:
        matched = frame[names.str.contains(keyword, case=False, na=False, regex=False)]
        if not matched.empty:
            return matched.iloc[0]
    return None


def row_value(row, names):
    if row is None:
        return None
    for name in names:
        if name in row.index and pd.notna(row[name]):
            return str(row[name])
    return None


def guarded(label, callback):
    try:
        callback()
    except Exception as exc:
        print(f"FAIL {label}: {type(exc).__name__}: {exc}")


def main():
    rows = []
    core_indices = {
        ".INX": ("标普500", "美股宽基"),
        ".IXIC": ("纳斯达克", "海外科技"),
        ".DJI": ("道琼斯", "美股宽基"),
    }
    for symbol, (asset, group) in core_indices.items():
        guarded(asset, lambda symbol=symbol, asset=asset, group=group: add(rows, ak.index_us_stock_sina(symbol=symbol), asset, group, "新浪财经"))

    regional = [("费城半导体", "半导体"), ("中国台湾加权", "半导体"), ("日经225", "电子与汽车"), ("韩国综合", "半导体")]
    for asset, group in regional:
        def fetch_regional(asset=asset, group=group):
            if asset == "费城半导体":
                frame = ak.macro_global_sox_index()
            else:
                name = {"中国台湾加权": "中国台湾加权指数", "日经225": "日经225指数", "韩国综合": "首尔综合指数"}[asset]
                frame = ak.index_global_hist_sina(symbol=name)
            add(rows, frame, asset, group, "新浪财经/宏观数据")
        guarded(asset, fetch_regional)

    global_spot = None
    try:
        global_spot = ak.index_global_spot_em()
    except Exception as exc:
        print(f"FAIL global index directory: {type(exc).__name__}: {exc}")
    global_targets = [
        ("纳斯达克中国金龙", "中国资产", ["中国金龙", "中概股"]),
        ("富时中国A50", "A股先行", ["富时中国A50", "中国A50", "A50"]),
        ("美元指数", "汇率", ["美元指数"]),
        ("离岸人民币", "汇率", ["离岸人民币", "美元人民币"]),
    ]
    for asset, group, keywords in global_targets:
        def fetch_global(asset=asset, group=group, keywords=keywords):
            row = find_row(global_spot, keywords)
            symbol = row_value(row, ["名称", "指数名称"])
            if not symbol:
                raise RuntimeError("global index directory has no matching symbol")
            add(rows, ak.index_global_hist_em(symbol=symbol), asset, group, "东方财富全球指数")
        guarded(asset, fetch_global)

    def fetch_hstech():
        try:
            frame, source = ak.stock_hk_index_daily_sina(symbol="HSTECH"), "新浪港股指数"
        except Exception:
            spot = ak.stock_hk_index_spot_em()
            row = find_row(spot, ["恒生科技"])
            code = row_value(row, ["代码", "指数代码", "symbol"])
            if not code:
                raise RuntimeError("HK index directory has no Hang Seng TECH code")
            frame, source = ak.stock_hk_index_daily_em(symbol=code), "东方财富港股指数"
        add(rows, frame, "恒生科技", "中国资产", source)
    guarded("恒生科技", fetch_hstech)

    commodity_targets = [
        ("COMEX黄金", "贵金属", "GC", "GC00Y"),
        ("WTI原油", "能源", "CL", "CL00Y"),
        ("COMEX铜", "工业金属", "HG", "HG00Y"),
    ]
    for asset, group, sina_code, em_code in commodity_targets:
        def fetch_commodity(asset=asset, group=group, sina_code=sina_code, em_code=em_code):
            try:
                frame, source = ak.futures_foreign_hist(symbol=sina_code), "新浪外盘期货"
            except Exception:
                frame, source = ak.futures_global_hist_em(symbol=em_code), "东方财富国际期货"
            add(rows, frame, asset, group, source)
        guarded(asset, fetch_commodity)

    def fetch_us10y():
        frame = ak.bond_zh_us_rate(start_date="20200101")
        date_col = next((x for x in ["日期", "date"] if x in frame), None)
        value_col = next((x for x in frame.columns if "美国国债收益率10年" in str(x) or "美国国债收益率_10年" in str(x)), None)
        if not date_col or not value_col:
            raise RuntimeError("US Treasury 10Y columns unavailable")
        add(rows, frame[[date_col, value_col]].rename(columns={date_col: "trade_date", value_col: "close"}), "美国国债10年", "利率", "东方财富中美国债收益率")
    guarded("美国国债10年", fetch_us10y)

    leaders = [
        ("英伟达", "海外AI产业链", "NVDA"),
        ("博通", "海外AI产业链", "AVGO"),
        ("美光科技", "海外存储产业链", "MU"),
        ("特斯拉", "海外汽车与机器人", "TSLA"),
    ]
    for asset, group, symbol in leaders:
        guarded(asset, lambda asset=asset, group=group, symbol=symbol: add(rows, ak.stock_us_daily(symbol=symbol, adjust="qfq"), asset, group, "新浪美股"))

    if not rows and (not TARGET.exists() or TARGET.stat().st_size == 0):
        raise RuntimeError("no overseas source returned data and no prior snapshot exists")
    fresh = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=KEEP_COLUMNS)
    if TARGET.exists() and TARGET.stat().st_size > 0:
        try:
            previous = pd.read_csv(TARGET)
            for column in KEEP_COLUMNS:
                if column not in previous:
                    previous[column] = "历史快照" if column == "source" else None
            fresh = pd.concat([previous[KEEP_COLUMNS], fresh[KEEP_COLUMNS]], ignore_index=True)
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
            pass
    fresh["trade_date"] = pd.to_datetime(fresh["trade_date"], errors="coerce").dt.strftime("%Y%m%d")
    for column in ["open", "high", "low", "close", "volume"]:
        fresh[column] = pd.to_numeric(fresh[column], errors="coerce")
    fresh = fresh.dropna(subset=["trade_date", "asset", "close"]).drop_duplicates(["asset", "trade_date"], keep="last")
    fresh = fresh.sort_values(["asset", "trade_date"]).groupby("asset", group_keys=False).tail(120)
    fresh.to_csv(TARGET, index=False, encoding="utf-8-sig")
    print(f"OK overseas: {len(fresh):,} rows, {fresh['asset'].nunique()} assets -> {TARGET}")


if __name__ == "__main__":
    main()
