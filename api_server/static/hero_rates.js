'use strict';
(function(root){
  const fields=['role','rq','rank','map','input','region'];
  const categories={role:'roles',rq:'rulesetQueues',rank:'ranks',map:'maps',input:'inputs',region:'regions'};
  const defaults={role:'all',rq:'2',rank:'master',map:'all',input:'pc',region:'korea'};
  const roleNames={tank:'坦克',damage:'输出',support:'辅助'};
  const $=id=>document.getElementById(id);
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let report=null,sortKey='winRate',descending=true,requestNumber=0;
  function roleIcon(role){
    const shape=role==='tank'?'<path d="M10 2 3 5v5c0 4 7 8 7 8s7-4 7-8V5z"/>':role==='support'?'<path d="M7 2h6v5h5v6h-5v5H7v-5H2V7h5z"/>':'<path d="m3 17 4-1L17 6l-3-3L4 13zM11 6l3 3M3 17l3-3"/>';
    return '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">'+shape+'</svg>';
  }
  function rows(){
    if(!report)return;
    const heroes=[...report.heroes].sort((a,b)=>{
      if(sortKey==='name')return a.name.localeCompare(b.name,'zh-CN')*(descending?-1:1);
      if(a[sortKey]==null)return b[sortKey]==null?0:1;
      if(b[sortKey]==null)return -1;
      return (a[sortKey]-b[sortKey])*(descending?-1:1)||a.name.localeCompare(b.name,'zh-CN');
    });
    const ranges={};
    for(const key of ['pickRate','winRate','banRate']){
      const values=heroes.map(hero=>hero[key]).filter(value=>value!=null);
      ranges[key]={min:Math.min(...values),max:Math.max(...values)};
    }
    $('heroRateRows').innerHTML=heroes.map(hero=>{
      const color=/^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/i.test(hero.colorCode)?hero.colorCode:'#748397';
      const url=typeof hero.thumbnailUrl==='string'&&hero.thumbnailUrl.startsWith('https://')?hero.thumbnailUrl:'';
      let html='<tr class="hero-rate-row" data-hero="'+escape(hero.heroId)+'"><td><div class="hero-identity"><span class="hero-portrait" style="--hero-color:'+color+'">'+(url?'<img src="'+escape(url)+'" alt="'+escape(hero.name)+'" loading="eager" referrerpolicy="no-referrer" onerror="this.hidden=true">':'')+'</span><div><div class="hero-name">'+escape(hero.name)+'</div><span class="hero-role">'+roleIcon(hero.role)+escape(roleNames[hero.role]||'')+'</span></div></div></td>';
      for(const key of ['pickRate','winRate','banRate']){
        const value=hero[key],range=ranges[key];
        const width=value==null?0:range.max===range.min?100:Math.max(0,Math.min(100,(value-range.min)/(range.max-range.min)*100));
        html+='<td'+(key==='banRate'?' class="ban-column"':'')+'><span class="hero-metric">'+(value==null?'—':Number(value).toFixed(1)+'%')+'</span><div class="rate-bar" aria-hidden="true"><div class="rate-bar-fill '+key+'" style="width:'+width.toFixed(3)+'%;'+(value==null?'display:none':'')+'"></div></div></td>';
      }
      return html+'</tr>';
    }).join('');
    for(const header of $('heroRateTable').querySelectorAll('th[data-sort]')){
      header.removeAttribute('aria-sort');
      header.querySelector('span').textContent='↕';
      if(header.dataset.sort===sortKey){header.setAttribute('aria-sort',descending?'descending':'ascending');header.querySelector('span').textContent=descending?'↓':'↑';}
    }
    const label={name:'英雄名称',pickRate:'选取率',winRate:'胜率',banRate:'禁用率'}[sortKey];
    $('sortHint').textContent=label+(sortKey==='name'?'排序':descending?'从高到低':'从低到高');
    $('heroRateTable').classList.toggle('no-ban',report.filters.rq!=='2');
  }
  function renderHeroRates(data){
    report=data;sortKey='winRate';descending=true;
    const labels=data.filter_labels;
    $('heroRateTitle').textContent=labels.region+'英雄胜率数据';
    $('filterSummary').textContent=[labels.rank,labels.map,labels.input,labels.region,labels.rq,labels.role].join(' · ');
    $('cacheUpdated').textContent='缓存更新 '+new Date(data.fetched_at*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+'（北京时间） · 每24小时更新';
    $('cacheUpdated').classList.toggle('stale',data.stale===true);
    $('heroRateSource').href=data.source_url;
    $('heroRateStatus').textContent=data.stale?'原站暂未更新成功，正在展示上次成功保存的数据。':data.heroes.length?'':'原站在这些筛选条件下没有统计数据。';
    $('heroRateStatus').classList.toggle('error',data.stale===true);
    rows();
  }
  root.renderHeroRates=renderHeroRates;
  $('heroRateTable').onclick=event=>{
    const button=event.target.closest('button[data-key]');
    if(!button||!report)return;
    if(sortKey===button.dataset.key)descending=!descending;else{sortKey=button.dataset.key;descending=sortKey!=='name';}
    rows();
  };
  async function api(path){
    const key=$('heroAccessKey').value;
    const response=await fetch(path,{headers:key?{'X-API-Key':key}:{},cache:'no-store'});
    const body=await response.json();
    if(!response.ok||!body.success){
      if(response.status===401||response.status===403)$('heroAccess').hidden=false;
      throw new Error(body.detail||body.error||'统计数据暂时不可用');
    }
    return body.data;
  }
  async function load(){
    const id=++requestNumber;
    if($('region').value==='korea')$('input').value='pc';
    $('input').querySelector('option[value="console"]').disabled=$('region').value==='korea';
    $('rankFilter').hidden=$('rq').value==='0';
    if($('rq').value==='0')$('rank').value='all';
    const selected=Object.fromEntries(fields.map(key=>[key,$(key).value]));
    $('heroRateRows').textContent='';
    $('heroRateStatus').textContent='正在读取所选条件的缓存…';
    $('heroRateStatus').classList.remove('error');
    try{
      const data=await api('/api/hero-rates?'+new URLSearchParams(selected));
      if(id!==requestNumber)return;
      renderHeroRates(data);
      history.replaceState(null,'','/hero-rates?'+new URLSearchParams(selected));
    }catch(error){if(id===requestNumber){report=null;$('heroRateStatus').textContent=error.message;$('heroRateStatus').classList.add('error');}}
  }
  async function start(){
    try{
      const metadata=await api('/api/hero-rates/filters');
      const params=new URLSearchParams(location.search);
      for(const field of fields){
        const options=metadata.filters[categories[field]];
        $(field).innerHTML=options.map(option=>'<option value="'+escape(option.value)+'" '+(option.parentValue==='battlefield'?'disabled':'')+'>'+escape(option.parentValue&&option.parentValue!=='battlefield'?'　'+option.name:option.name)+'</option>').join('');
        const selected=params.get(field)||defaults[field];
        $(field).value=options.some(option=>option.value===selected&&option.parentValue!=='battlefield')?selected:defaults[field];
        $(field).onchange=load;
      }
      await load();
    }catch(error){$('heroRateStatus').textContent=error.message;$('heroRateStatus').classList.add('error');}
  }
  $('heroAccessSubmit').onclick=start;
  if(!root.__OW_STATIC_REPORT__)start();
})(globalThis);
