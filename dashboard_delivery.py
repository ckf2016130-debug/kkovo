"""Small HTML shell, versioned data chunks and a recoverable loader."""
import hashlib
import json
import re


def package_dashboard(template, datasets, out, vendor):
    assets=out/'payloads'; assets.mkdir(parents=True,exist_ok=True)
    def asset(content,suffix):
        data=content.encode('utf-8'); name=hashlib.sha256(data).hexdigest()[:20]+suffix
        (assets/name).write_bytes(data)
        return 'payloads/'+name
    manifest={'datasets':{},'scripts':[], 'libraries':[], 'extensions':[]}
    for key,value in datasets.items():
        if isinstance(value,list):
            paths=[]; batch=[]; size=2
            for item in value:
                encoded=json.dumps(item,ensure_ascii=False,separators=(',',':'))
                if batch and size+len(encoded.encode('utf-8'))>120000:
                    paths.append(asset('['+','.join(batch)+']','.json'));batch=[];size=2
                batch.append(encoded);size+=len(encoded.encode('utf-8'))+1
            if batch:paths.append(asset('['+','.join(batch)+']','.json'))
            manifest['datasets'][key]={'array':True,'paths':paths}
        else:
            manifest['datasets'][key]={'array':False,'paths':[asset(json.dumps(value,ensure_ascii=False,separators=(',',':')),'.json')]}
    # Preserve each original classic script's global lexical scope and order.
    def script(match):
        attrs,body=match.group(1),match.group(2)
        source=re.search(r'src="([^"]+)"',attrs)
        if source:
            name=source.group(1).split('/')[-1]
            path=vendor/name
            version=hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            target='extensions' if name in ['review.js','pattern-watchlist.js'] else 'libraries'
            manifest[target].append(source.group(1)+'?v='+version)
        elif body.strip():
            manifest['scripts'].append(asset(body,'.js'))
        return ''
    template=re.sub(r'<script\b([^>]*)>(.*?)</script>',script,template,flags=re.S|re.I)
    loader=(vendor/'dashboard-loader.js').read_text(encoding='utf-8')
    loader_path=asset(loader,'.js')
    shell='''<div id="dashboardLoading" role="status" style="position:fixed;inset:0;z-index:9999;background:#0e151c;color:#e2e8ef;display:flex;align-items:center;justify-content:center;padding:24px;font:15px/1.8 system-ui,Microsoft YaHei,sans-serif"><div style="max-width:540px;width:100%"><h2>正在加载看板</h2><p id="dashboardLoadText">正在连接数据。首次打开需要下载历史快照，请稍候。</p><progress id="dashboardLoadProgress" max="100" value="0" style="width:100%"></progress><p id="dashboardLoadError" style="color:#e6ad34"></p><button id="dashboardLoadRetry" hidden>重试未完成的数据</button> <button onclick="location.reload()">重新加载页面</button><p style="color:#8493a1;font-size:12px">行情日期以加载完成后显示的截止日为准。加载失败不会生成空白市场结论。</p></div></div>'''
    template=template.replace('<body>','<body>'+shell,1)
    tail='<script id="dashboardManifest" type="application/json">'+json.dumps(manifest,ensure_ascii=False).replace('<','\\u003c')+'</script><script defer src="'+loader_path+'"></script>'
    return template.replace('</body>',tail+'</body>')
