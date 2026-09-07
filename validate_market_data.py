import csv
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path


DATA = Path("data")
OUT = Path("output") / "market_dashboard"
MAX_AGE_DAYS = int(os.getenv("MAX_MARKET_DATA_AGE_DAYS", "10"))
MIN_DAILY_ROWS = int(os.getenv("MIN_DAILY_ROWS", "100"))


def parse_yyyymmdd(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def csv_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        rows = sum(1 for _ in reader)
    return max(0, rows - 1)


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    DATA.mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    if not (DATA / "stock_basic.csv").exists():
        errors.append("data/stock_basic.csv is missing.")

    daily_files = []
    for path in DATA.glob("daily_*.csv"):
        match = re.fullmatch(r"daily_(\d{8})\.csv", path.name)
        if match:
            daily_files.append((match.group(1), path))
    if not daily_files:
        errors.append("No data/daily_YYYYMMDD.csv files found.")
        latest_date = None
        latest_rows = 0
        latest_path = None
    else:
        latest_code, latest_path = sorted(daily_files)[-1]
        latest_date = parse_yyyymmdd(latest_code)
        latest_rows = csv_row_count(latest_path)
        if latest_rows < MIN_DAILY_ROWS:
            errors.append(f"{latest_path} has only {latest_rows} rows; expected at least {MIN_DAILY_ROWS}.")
        age_days = (date.today() - latest_date).days
        if age_days > MAX_AGE_DAYS:
            errors.append(f"Latest daily snapshot is stale: {latest_code}, {age_days} days old.")

    manifest_path = DATA / "manifest.json"
    manifest_end_date = None
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_end_date = str(manifest.get("end_date") or "")
            failed_core = [
                item for item in manifest.get("results", [])
                if item.get("api") in {"stock_basic", "daily", "daily_basic", "moneyflow"}
                and item.get("error") and item.get("rows", 0) == 0
            ]
            if failed_core:
                warnings.append(f"{len(failed_core)} core fetch calls returned errors; existing snapshots may have been reused.")
        except Exception as exc:
            warnings.append(f"Could not parse data/manifest.json: {exc}")
    else:
        warnings.append("data/manifest.json is missing.")

    status = {
        "ok": not errors,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "latest_daily_file": str(latest_path) if latest_path else None,
        "latest_daily_date": latest_date.isoformat() if latest_date else None,
        "latest_daily_rows": latest_rows,
        "manifest_end_date": manifest_end_date,
        "max_age_days": MAX_AGE_DAYS,
        "min_daily_rows": MIN_DAILY_ROWS,
        "errors": errors,
        "warnings": warnings,
    }
    (OUT / "data_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
