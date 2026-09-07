(() => {
  'use strict';
  const labels={initial:'初选',watch:'整理观察',confirmed:'转强确认',invalid:'失效'};
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const date=x=>/^\d{8}$/.test(String(x))?`${x.slice(0,4)}-${x.slice(4,6)}-${x.slice(6)}`:'—';
  const num=(x,n=2)=>x==null?'—':Number(x).toFixed(n);
  let payload=null, mode='watch', query='', selected=null;
  const tab=document.createElement('button');tab.className='tab';tab.dataset.view='patternView';tab.textContent='量价观察池';
  const view=document.createElement('main');view.id='patternView';view.className='view';
  view.innerHTML=`<section class="panel"><div class="head">量价观察池 <small>盘后形态研究 · 未验证策略</small></div><div class="pv-intro"><p>先发现异常上冲，再观察回撤后量能收缩。转强确认只表示满足规则，不代表可买入或有人控盘。</p><div id="pvFreshness" role="status">正在读取观察池…</div></div><div class="pv-controls" aria-label="观察阶段">${Object.entries(labels).map(([k,v])=>`<button class="btn" data-stage="${k}">${v} <span data-count="${k}">—</span></button>`).join('')}<button class="btn" data-stage="all">全部</button><label>搜索 <input id="pvSearch" placeholder="名称 / 代码 / 行业" aria-label="搜索观察池"></label></div><div class="pv-layout"><div class="pv-list" id="pvList"></div><aside id="pvDetail" class="pv-detail">选择股票查看入池依据。</aside></div><details class="pv-method"><summary>规则、数据口径与验证状态</summary><div id="pvMethod"></div><p>“失效”仅指此形态超过25个交易日或触及固定失效位，不是对公司价值的判断。阶段由历史日线重算；数据窗口之外的旧事件不会保留。确认状态保留至失效，并显示原确认日期。</p><p>公告与持股结构尚未加入过滤。查看个股时请结合网站已有消息页；不把后来披露的消息当成提前信号。</p></details></section>`;
  const style=document.createElement('style');style.textContent=`#patternView{padding:8px 12px}.pv-intro,.pv-method{padding:12px 15px}.pv-intro p{margin:0 0 8px}.pv-muted,#pvFreshness{color:var(--muted)}.pv-controls{display:flex;gap:7px;flex-wrap:wrap;align-items:center;padding:10px 15px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.pv-controls label{margin-left:auto}.pv-controls input{background:var(--bg);color:var(--text);border:1px solid var(--line);padding:7px;width:190px}.pv-layout{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(290px,1fr);min-height:390px}.pv-list{max-height:650px;overflow:auto}.pv-detail{padding:18px;border-left:1px solid var(--line);overflow-wrap:anywhere}.pv-row{display:grid;grid-template-columns:1fr 1fr 90px;gap:9px;width:100%;text-align:left;background:transparent;color:var(--text);border:0;border-bottom:1px solid var(--line2);padding:13px;cursor:pointer}.pv-row:hover,.pv-row[aria-pressed=true]{background:#1c2b34}.pv-row small{display:block;color:var(--muted);margin-top:5px}.pv-row b{color:var(--gold)}.pv-detail h3{margin:0 0 8px}.pv-facts{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:15px 0}.pv-facts>div{background:var(--bg);padding:10px}.pv-facts small{display:block;color:var(--muted);margin-bottom:4px}.pv-alert{color:var(--gold)}.pv-method{border-top:1px solid var(--line);line-height:1.8}.pv-method summary{cursor:pointer}.pv-empty{padding:35px 18px;color:var(--muted);line-height:1.8}@media(max-width:800px){.pv-layout{grid-template-columns:1fr}.pv-detail{border-left:0;border-top:1px solid var(--line)}.pv-controls label{margin-left:0;width:100%}.pv-row{grid-template-columns:1fr 1fr 75px}}`;
  document.head.appendChild(style);document.querySelector('.tabs')?.appendChild(tab);document.body.appendChild(view);
  tab.onclick=()=>{showView('patternView');if(!payload)load()};
  const $=id=>view.querySelector('#'+id);
  function render(){
    if(!payload)return;
    view.querySelectorAll('[data-count]').forEach(e=>e.textContent=payload.rows.filter(r=>r.status===e.dataset.count).length);
    view.querySelectorAll('[data-stage]').forEach(e=>{e.classList.toggle('active',e.dataset.stage===mode);e.setAttribute('aria-pressed',String(e.dataset.stage===mode))});
    const rows=payload.rows.filter(r=>(mode==='all'||r.status===mode)&&`${r.name} ${r.ts_code} ${r.industry}`.toLowerCase().includes(query));
    if(!rows.some(r=>r.id===selected))selected=rows[0]?.id||null;
    $('pvList').innerHTML=rows.length?rows.map(r=>`<button class="pv-row" data-id="${esc(r.id)}" aria-pressed="${r.id===selected}"><span><b>${esc(r.name)}</b><small>${esc(r.ts_code)} · ${esc(r.industry)}</small></span><span>${labels[r.status]}<small>${date(r.status_date)}${r.stale?' · 个股数据落后':''}</small></span><span>${num(r.latest_close)}<small>回撤 ${num(r.depth)}%</small></span></button>`).join(''):`<div class="pv-empty">${query?'没有匹配的名称、代码或行业。':'此阶段暂无符合条件的股票。可切换其他阶段；暂无结果不等于市场没有机会。'}<br>覆盖 ${payload.universe} 只；历史不足 ${payload.insufficient_history} 只。</div>`;
    $('pvList').querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>{selected=b.dataset.id;render()});
    const r=rows.find(r=>r.id===selected);
    if(!r){$('pvDetail').textContent='当前筛选没有股票。可调整阶段或搜索条件。';return;}
    $('pvDetail').innerHTML=`<h3>${esc(r.name)} <small>${esc(r.ts_code)}</small></h3><p>${esc(r.reason)}</p><div class="pv-facts"><div><small>首段上冲</small>${date(r.event_date)}</div><div><small>阶段变更</small>${date(r.status_date)}</div><div><small>回撤幅度</small>${num(r.depth)}%</div><div><small>后3日量 / 前3日量</small>${num(r.contraction)} 倍</div><div><small>固定失效位（未复权）</small>${num(r.invalidation)} 元</div><div><small>个股数据截止</small>${date(r.observed_date)}</div></div><p class="pv-muted">失效位依据首段高点×75%计算，是研究阈值，不是止损成交保证。回撤与量比为最近一次整理判定值，确认后冻结。</p>${r.stale?'<p class="pv-alert">该股数据落后于全池截止日，不视为最新信号。</p>':''}<p class="pv-alert">${r.single_price?'该记录最近观测日为单一成交价，不能假设能够买入。':'尚未核验次日涨跌停及可成交性。'}</p><button class="btn" id="pvOpenStock">查看个股 K 线与关注操作</button>`;
    $('pvOpenStock').onclick=()=>{if(stocks.some(x=>x.ts_code===r.ts_code))selectStock(r.ts_code,true);else $('pvOpenStock').textContent='该股不在当前个股快照中'};
  }
  async function load(){
    $('pvFreshness').textContent='正在读取观察池…';
    try{
      const response=await fetch('pattern_pool.json',{cache:'no-cache'});if(!response.ok)throw Error('http');
      const data=await response.json();if(!Array.isArray(data.rows)||!data.params)throw Error('schema');
      payload=data;
      const age=data.as_of?Math.floor((Date.now()-Date.parse(date(data.as_of)+'T15:00:00+08:00'))/86400000):null;
      $('pvFreshness').style.color=age>3?'var(--gold)':'var(--muted)';
      $('pvFreshness').textContent=`数据截止 ${date(data.as_of)} · 覆盖 ${data.universe} 只 · 历史不足 ${data.insufficient_history} 只 · 排除无效记录 ${data.invalid_rows} 条 · ${data.version}${age>3?' · 数据已超过3天，请勿视为当日结果':''}`;
      $('pvMethod').innerHTML=`<p>首段：3日累计涨幅≥8%，当日量≥此前20日均量1.5倍。整理：至少6个回撤交易日，回撤5%—25%，后3日均量≤前3日75%。确认：已入观察池，收盘突破此前3日高点，量≥此前5日均量1.3倍。</p><ul>${data.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;
      render();
    }catch{
      $('pvFreshness').textContent='观察池未生成或读取失败；不使用空白数据推断市场状态。';
      $('pvList').innerHTML='<div class="pv-empty">暂时无法读取观察池。<br><button class="btn" id="pvRetry">重试</button></div>';
      $('pvRetry').onclick=load;
    }
  }
  view.querySelectorAll('[data-stage]').forEach(b=>b.onclick=()=>{mode=b.dataset.stage;render()});
  $('pvSearch').oninput=e=>{query=e.target.value.trim().toLowerCase();render()};
})();
