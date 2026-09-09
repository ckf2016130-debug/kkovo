import json
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd


OUT = Path("data/intraday")
TARGET = OUT / "spot.csv"
MANIFEST = OUT / "manifest.json"
DATA = Path("data")


def market_suffix(code: str) -> str:
    if code.startswith(("4", "8", "92")):
        return ".BJ"
    if code.startswith(("5", "6", "9")):
        return ".SH"
    return ".SZ"


def latest_completed_trade_date(now: datetime) -> str | None:
    """Return the latest session that should already have complete OHLC data."""
    if now.weekday() < 5 and time(9, 15) <= now.time() < time(15, 10):
        print("SKIP free EOD promotion: current trading session is not complete")
        return None
    try:
        history = ak.stock_zh_index_daily_em(
            symbol="sh000001",
            start_date=(now.date() - timedelta(days=20)).strftime("%Y%m%d"),
            end_date=now.strftime("%Y%m%d"),
        )
        date_column = "date" if "date" in history else "日期" if "日期" in history else None
        dates = pd.to_datetime(history[date_column], errors="coerce").dropna() if date_column else pd.Series(dtype="datetime64[ns]")
        return dates.max().strftime("%Y%m%d") if not dates.empty else None
    except Exception as exc:
        print(f"WARN completed index date unavailable, use exchange calendar: {exc}")
        try:
            calendar = ak.tool_trade_date_hist_sina()
            dates = pd.to_datetime(calendar["trade_date"], errors="coerce").dropna().dt.date
            today = now.date()
            completed_today = now.time() >= time(15, 10)
            eligible = dates[(dates < today) | ((dates == today) & completed_today)]
            return max(eligible).strftime("%Y%m%d") if not eligible.empty else None
        except Exception as calendar_exc:
            print(f"SKIP free EOD promotion: exchange calendar unavailable: {calendar_exc}")
            return None


def normalize_spot_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize the common Eastmoney/Sina/Tencent spot schemas."""
    aliases = {
        "代码": ["code", "symbol"],
        "名称": ["name"],
        "最新价": ["trade", "price", "close"],
        "涨跌幅": ["changepercent", "pct_chg"],
        "涨跌额": ["pricechange", "change"],
        "成交量": ["volume", "vol"],
        "成交额": ["amount"],
        "最高": ["high"],
        "最低": ["low"],
        "今开": ["open"],
        "昨收": ["settlement", "pre_close", "prev_close"],
        "换手率": ["turnoverratio", "turnover_rate"],
        "量比": ["volume_ratio"],
        "市盈率-动态": ["per", "pe"],
        "市净率": ["pb"],
        "总市值": ["mktcap", "total_mv"],
        "流通市值": ["nmc", "circ_mv"],
    }
    rename = {}
    for target, candidates in aliases.items():
        if target in frame.columns:
            continue
        source = next((column for column in candidates if column in frame.columns), None)
        if source:
            rename[source] = target
    return frame.rename(columns=rename)


def promote_completed_snapshot(frame: pd.DataFrame, trade_date: str, retrieved_at: str, source_name: str = "AKShare公开行情") -> dict:
    required = {"ts_code", "name", "close", "pct_chg", "amount", "open", "high", "low", "pre_close"}
    if not required.issubset(frame.columns):
        missing = sorted(required - set(frame.columns))
        return {"promoted": False, "trade_date": trade_date, "error": f"missing EOD fields: {missing}"}

    eod = frame.copy()
    for column in ["open", "high", "low", "close", "pre_close", "pct_chg", "amount", "vol"]:
        if column in eod:
            eod[column] = pd.to_numeric(eod[column], errors="coerce")
    eod = eod.dropna(subset=["ts_code", "open", "high", "low", "close", "pre_close", "pct_chg"])
    eod = eod[(eod[["open", "high", "low", "close", "pre_close"]] > 0).all(axis=1)]
    if len(eod) < 3000:
        return {"promoted": False, "trade_date": trade_date, "error": f"EOD row-count sanity check failed: {len(eod)}"}

    eod["ts_code"] = eod["ts_code"].map(lambda code: code + market_suffix(code))
    eod["trade_date"] = trade_date
    eod["change"] = eod["close"] - eod["pre_close"]
    # Eastmoney amount is yuan; Tushare-compatible daily amount is thousand yuan.
    eod["amount"] = eod["amount"] / 1000
    # The free full-market snapshots report shares; Tushare-compatible daily volume is lots (100 shares).
    if "vol" in eod:
        eod["vol"] = eod["vol"] / 100
    daily_columns = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"]
    daily = eod[[c for c in daily_columns if c in eod]].copy()
    daily_path = DATA / f"daily_{trade_date}.csv"
    daily.to_csv(daily_path, index=False, encoding="utf-8-sig")

    valuation = pd.DataFrame({"ts_code": eod["ts_code"], "trade_date": trade_date, "close": eod["close"]})
    source_columns = {
        "turnover_rate": "turnover_rate",
        "volume_ratio": "volume_ratio",
        "pe": "pe",
        "pb": "pb",
        "total_mv": "total_mv",
        "circ_mv": "circ_mv",
    }
    for target, source in source_columns.items():
        values = pd.to_numeric(eod.get(source), errors="coerce") if source in eod else pd.Series(pd.NA, index=eod.index)
        if target in {"total_mv", "circ_mv"}:
            values = values / 10000  # yuan -> ten thousand yuan
        valuation[target] = values.to_numpy()
    valuation_path = DATA / f"daily_basic_{trade_date}.csv"
    valuation.to_csv(valuation_path, index=False, encoding="utf-8-sig")
    return {
        "promoted": True,
        "trade_date": trade_date,
        "rows": len(daily),
        "daily_file": str(daily_path),
        "daily_basic_file": str(valuation_path),
        "retrieved_at": retrieved_at,
        "source": f"{source_name}（收盘快照）",
    }


def fetch_free_moneyflow(spot: pd.DataFrame, trade_date: str) -> dict:
    path = DATA / f"moneyflow_{trade_date}.csv"
    errors = []

    def amount_in_yuan(value):
        if pd.isna(value):
            return float("nan")
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(",", "")
        match = re.fullmatch(r"([+-]?[\d.]+)\s*(万|亿)?", text)
        if not match:
            return float("nan")
        number = float(match.group(1))
        return number * (100000000 if match.group(2) == "亿" else 10000 if match.group(2) == "万" else 1)

    def eastmoney_rank():
        frame = ak.stock_individual_fund_flow_rank(indicator="今日")
        net_column = next((c for c in frame.columns if "主力净流入-净额" in str(c)), None)
        return frame, "代码", "最新价", net_column, lambda series: pd.to_numeric(series, errors="coerce"), "AKShare / 东方财富主力净流入排名"

    def ths_rank():
        frame = ak.stock_fund_flow_individual(symbol="即时")
        return frame, "股票代码", "最新价", "净额", lambda series: series.map(amount_in_yuan), "AKShare / 同花顺个股资金流"

    spot_prices = spot[["ts_code", "close"]].copy()
    spot_prices["close"] = pd.to_numeric(spot_prices["close"], errors="coerce")
    for fetcher in [eastmoney_rank, ths_rank]:
        try:
            frame, code_column, price_column, net_column, parse_amount, source = fetcher()
            if frame.empty or not net_column or not {code_column, price_column, net_column}.issubset(frame.columns):
                raise ValueError("money-flow ranking is empty or missing required fields")
            frame = frame.copy()
            frame["code"] = frame[code_column].astype(str).str.extract(r"(\d{6})", expand=False)
            frame["latest"] = pd.to_numeric(frame[price_column], errors="coerce")
            frame["net"] = parse_amount(frame[net_column])
            matched = frame.merge(spot_prices, left_on="code", right_on="ts_code", how="inner").dropna(subset=["latest", "close"])
            matched = matched[(matched["latest"] > 0) & (matched["close"] > 0)]
            agreement = float(((matched["latest"] / matched["close"] - 1).abs() <= 0.005).mean()) if len(matched) else 0
            if len(matched) < 2500 or agreement < 0.90:
                raise ValueError(f"money-flow/price alignment failed: matched={len(matched)}, agreement={agreement:.1%}")
            out = frame.dropna(subset=["code", "net"]).copy()
            if len(out) < 3000:
                raise ValueError(f"money-flow row-count sanity check failed: {len(out)}")
            out["ts_code"] = out["code"].map(lambda code: code + market_suffix(code))
            out["trade_date"] = trade_date
            # Both free sources are normalized to yuan; Tushare-compatible moneyflow is ten thousand yuan.
            out["net_mf_amount"] = out["net"] / 10000
            out[["ts_code", "trade_date", "net_mf_amount"]].to_csv(path, index=False, encoding="utf-8-sig")
            return {"valid": True, "rows": len(out), "file": str(path), "price_agreement": round(agreement, 4), "source": source, "source_errors": errors}
        except Exception as exc:
            errors.append(f"{fetcher.__name__}: {type(exc).__name__}: {exc}")
    return {"valid": False, "rows": 0, "file": str(path), "error": "; ".join(errors)}


def fetch_free_limit_pool(trade_date: str) -> dict:
    path = DATA / f"limit_list_{trade_date}.csv"
    rows = []
    errors = []
    sources = [("U", "stock_zt_pool_em"), ("Z", "stock_zt_pool_zbgc_em"), ("D", "stock_zt_pool_dtgc_em")]
    for limit_type, attr in sources:
        try:
            fetcher = getattr(ak, attr, None)
            if fetcher is None:
                raise AttributeError(f"AkShare has no {attr}")
            frame = fetcher(date=trade_date)
            code_column = "代码" if "代码" in frame else "股票代码" if "股票代码" in frame else None
            if frame.empty or not code_column:
                continue
            codes = frame[code_column].astype(str).str.extract(r"(\d{6})", expand=False).dropna()
            rows.extend({"ts_code": code + market_suffix(code), "trade_date": trade_date, "limit": limit_type} for code in codes)
        except Exception as exc:
            errors.append(f"{limit_type}: {type(exc).__name__}: {exc}")
    if not rows:
        return {"valid": False, "rows": 0, "file": str(path), "error": "; ".join(errors) or "all free limit pools were empty"}
    pd.DataFrame(rows).drop_duplicates().to_csv(path, index=False, encoding="utf-8-sig")
    return {"valid": True, "rows": len(rows), "file": str(path), "source": "AKShare / 东方财富涨跌停与炸板池", "source_errors": errors}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(exist_ok=True)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    retrieved_at = now.isoformat(timespec="seconds")
    result = {"source": "AKShare / 东方财富公开行情", "retrieved_at": retrieved_at, "valid": False, "rows": 0, "error": None}
    errors = []
    source_specs = [
        ("AKShare / 东方财富公开行情", "stock_zh_a_spot_em"),
        ("AKShare / 新浪公开行情", "stock_zh_a_spot"),
        ("AKShare / 腾讯公开行情", "stock_zh_a_spot_tx"),
    ]
    sources = [(name, fetcher) for name, attr in source_specs if (fetcher := getattr(ak, attr, None)) is not None]
    try:
        frame = None
        source_name = ""
        for candidate_name, fetcher in sources:
            try:
                candidate = normalize_spot_columns(fetcher())
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
        frame = frame.rename(columns={"代码": "ts_code", "名称": "name", "最新价": "close", "涨跌幅": "pct_chg", "涨跌额": "change", "成交额": "amount", "成交量": "vol", "换手率": "turnover_rate", "量比": "volume_ratio", "最高": "high", "最低": "low", "今开": "open", "昨收": "pre_close", "市盈率-动态": "pe", "市净率": "pb", "总市值": "total_mv", "流通市值": "circ_mv"})
        for column in ["close", "pct_chg", "amount"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["ts_code"] = frame["ts_code"].astype(str).str.extract(r"(\d{6})", expand=False)
        frame = frame[frame["ts_code"].notna()]
        frame = frame[(frame["close"] > 0) & frame["pct_chg"].notna() & (frame["amount"] >= 0)]
        if len(frame) < 3000:
            raise ValueError(f"row-count sanity check failed: {len(frame)}")
        frame["trade_date"] = now.strftime("%Y%m%d")
        frame["retrieved_at"] = retrieved_at
        frame.to_csv(TARGET, index=False, encoding="utf-8-sig")
        completed_date = latest_completed_trade_date(now)
        eod_result = promote_completed_snapshot(frame, completed_date, retrieved_at, source_name) if completed_date else {"promoted": False, "trade_date": None, "error": "no completed trade date"}
        if eod_result.get("promoted"):
            eod_result["moneyflow"] = fetch_free_moneyflow(frame, completed_date)
            eod_result["limit_pool"] = fetch_free_limit_pool(completed_date)
        result.update({"valid": True, "rows": len(frame), "source": source_name, "source_errors": errors, "eod": eod_result})
        print(f"OK free intraday snapshot: {len(frame):,} rows -> {TARGET}")
        if eod_result.get("promoted"):
            print(f"OK free EOD snapshot: {eod_result['trade_date']} -> {eod_result['daily_file']}")
        else:
            print(f"SKIP free EOD snapshot: {eod_result.get('error')}")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        print(f"FAIL free intraday snapshot: {result['error']}")
    MANIFEST.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not result["valid"] and not TARGET.exists():
        raise RuntimeError(result["error"] or "free intraday snapshot unavailable")


if __name__ == "__main__":
    main()
