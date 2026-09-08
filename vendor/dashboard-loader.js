(() => {
  'use strict';
  const manifest=JSON.parse(document.getElementById('dashboardManifest').textContent);
  const text=document.getElementById('dashboardLoadText'),error=document.getElementById('dashboardLoadError'),retry=document.getElementById('dashboardLoadRetry');
  const cache=new Map();let running=false,started=false;
  async function read(path){
    if(cache.has(path))return cache.get(path);
    for(let attempt=0;attempt<3;attempt++){
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
      try{const r=await fetch(path,{signal:controller.signal});if(!r.ok)throw Error(`HTTP ${r.status}`);const body=await r.text();
        // Validate full JSON before accepting a possibly truncated response.
        if(path.endsWith('.json'))JSON.parse(body);
        cache.set(path,body);return body;
      }catch(e){if(attempt===2)throw Error(path+'：'+(e.name==='AbortError'?'连接超时':e.message));}
      finally{clearTimeout(timer)}
    }
  }
  function execute(source){
    let failure=null;const handler=e=>{failure=e.error||Error(e.message)};
    window.addEventListener('error',handler);
    const script=document.createElement('script');script.textContent=source;document.body.appendChild(script);
    window.removeEventListener('error',handler);if(failure)throw failure;
  }
  async function load(){
    if(running||started)return;running=true;retry.hidden=true;error.textContent='';
    try{
      const paths=[...new Set([...manifest.libraries,...Object.values(manifest.datasets).flatMap(x=>x.paths),...manifest.scripts,...manifest.extensions])];
      let cursor=0,done=0;
      const workers=await Promise.allSettled(Array.from({length:4},async()=>{while(cursor<paths.length){const path=paths[cursor++];await read(path);done++;text.textContent=`正在读取数据 ${done} / ${paths.length}；已完成部分会保留，断线会自动重试。`;document.getElementById('dashboardLoadProgress').value=done/paths.length*100;}}));
      const failed=workers.find(x=>x.status==='rejected');if(failed)throw failed.reason;
      window.__MARKET_DATA__={};
      for(const [key,part] of Object.entries(manifest.datasets)){
        const pieces=part.paths.map(path=>JSON.parse(cache.get(path)));
        window.__MARKET_DATA__[key]=part.array?pieces.flat():pieces[0];
      }
      text.textContent='数据已到齐，正在绘制图表…';
      // Only execute after all files arrive; retries never redeclare half-loaded globals.
      started=true;
      for(const path of [...manifest.libraries,...manifest.scripts,...manifest.extensions])execute(cache.get(path));
      document.getElementById('dashboardLoading').remove();
    }catch(e){
      text.textContent=started?'页面初始化失败':'部分数据未能加载';
      error.textContent=started?'请重新加载页面；若仍失败，请反馈此提示：'+e.message:'网络连接中断或超时。点击重试即可继续，不必重新下载已完成的数据。';
      retry.hidden=started;
    }finally{running=false}
  }
  retry.onclick=load;load();
})();
