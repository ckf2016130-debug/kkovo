import os

import tushare as ts


def create_pro(timeout=30):
    token = os.getenv("TUSHARE_TOKEN") or os.getenv("TINYSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not configured")
    ts.set_token(token)
    pro = ts.pro_api()
    pro._DataApi__http_url = os.getenv(
        "TUSHARE_API_URL", "https://fastapic.stockai888.top"
    )
    pro.timeout = timeout
    return pro
