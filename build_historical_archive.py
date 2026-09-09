"""Keep the old paid-data dashboard explicitly historical and separate."""
import subprocess
import sys
from pathlib import Path


def main():
    if len(list(Path('data').glob('daily_????????.csv'))) < 2:
        print('No legacy daily cache; archive unavailable, free dashboard continues')
        return
    subprocess.run([sys.executable, 'build_rotation_report.py'], check=True, timeout=180)
    import build_market_dashboard as legacy
    legacy.OUT = Path('output/market_dashboard/archive').resolve()
    legacy.OUT.mkdir(parents=True, exist_ok=True)
    legacy.build()
    path = legacy.OUT / 'index.html'
    page = path.read_text(encoding='utf-8')
    notice = '<div style="position:sticky;top:0;z-index:9000;background:#573a20;color:#fff;padding:12px;text-align:center">历史资料：付费数据已停止更新，各栏目以自身日期为准，不代表当前市场。 <a style="color:#ffe0a0" href="../index.html">返回最新免费量价看板</a></div>'
    path.write_text(page.replace('<body>', '<body>'+notice, 1), encoding='utf-8')


if __name__ == '__main__':
    main()
