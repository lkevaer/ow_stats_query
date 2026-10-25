'use strict';
let currentReport = null, polling = false, querying = false;
const $ = id => document.getElementById(id);
let selfBinding = RatingAccount.read(document.cookie);
function showBinding() {
  const bound = Boolean(selfBinding.tag);
  $('bindForm').classList.toggle('hidden', bound);
  $('boundAccount').classList.toggle('hidden', !bound);
  $('boundTag').textContent = selfBinding.tag;
  $('autoJoin').checked = selfBinding.autoJoin;
  $('autoJoin').disabled = !bound;
}
$('bindForm').onsubmit = event => {
  event.preventDefault();
  try {
    const tags = RatingAccount.parseTags($('bindTag').value);
    if (tags.length !== 1) throw new Error('请填写一个完整战网ID，例如 昵称#12345。');
    document.cookie = RatingAccount.cookie({tag: tags[0], autoJoin: true}, location.protocol === 'https:');
    selfBinding = RatingAccount.read(document.cookie);
    if (!selfBinding.tag) throw new Error('绑定未能保存，请允许本站保存数据后重试。');
    $('bindTag').value = '';
    $('bindingMessage').textContent = '已绑定，查询时会自动加入本人。';
    showBinding();
  } catch (error) { $('bindingMessage').textContent = error.message; }
};
$('unbind').onclick = () => {
  document.cookie = RatingAccount.clearCookie;
  selfBinding = RatingAccount.read(document.cookie);
  $('bindingMessage').textContent = selfBinding.tag ? '解绑未能保存，请检查浏览器设置。' : '已解绑。';
  showBinding();
};
$('autoJoin').onchange = () => {
  try {
    document.cookie = RatingAccount.cookie({tag: selfBinding.tag, autoJoin: $('autoJoin').checked}, location.protocol === 'https:');
    selfBinding = RatingAccount.read(document.cookie);
    $('bindingMessage').textContent = selfBinding.autoJoin ? '查询时自动加入本人。' : '查询时仅使用输入的名单。';
    showBinding();
  } catch (error) { $('bindingMessage').textContent = error.message; }
};
showBinding();
const escapeHtml = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const roleNames = {tank:'坦克 T',dps:'输出 C',support:'辅助 N'};
const metricNames = {damage:'伤害',final_blows:'最后一击',survival:'生存',eliminations:'击杀',healing:'治疗',assists:'助攻',mitigation:'承伤',final_blow_share:'最后一击占比'};
const supportDeathExplanation='新查询中的“辅助阵亡偏多”是独立额外扣分：略多于队友时温和递增，普通负荷通常不超过10分；只有阵亡率、队内差距、倍数和实际次数都明显突出时，才逐步接近15分的展示上限。15分仅限制这项额外扣分，生存基础贡献仍另计；Fleta达到50%的豁免保留。';
function metricHint(row,key) {
  const aggregate=Array.isArray(row.matches), adjustments=aggregate?row.summary_adjustments||[]:row.adjustments||[];
  const rules={
    score:aggregate?'综合评分由逐场分数按有效权重汇总，再应用综合参考修正；分数范围0–100，100分为SSS。':'本场评分综合各项相对参考贡献与额外规则，并限制在0–100。各项贡献不能直接视为独立总分。',
    fleta:'Fleta＝个人最后一击占全队比例×2，可超过100%。达到80%标记CARRY；败方严格超过80%标记SVP。近12场至少7场严格超过65%标记SMURF。',
    final_blow_share:'个人最后一击÷全队最后一击。坦克达到30%、输出达到40%可获得对应加分，达到50%还有高占比规则；辅助Fleta达到40%/50%有进攻表现规则。',
    kd:aggregate?'KD＝总击杀÷总阵亡；有击杀且零阵亡显示∞。综合KD达到10有小幅加分，至少9场KD达到7还有稳定表现加分。':'KD＝击杀÷阵亡；有击杀且零阵亡显示∞。本场分别评估击杀和生存，KD本身不重复加入基础分。',
    weight:'本场权重决定这场比赛对近12场综合评分的影响。短于6.5分钟降权，超过15分钟小幅增权，势均力敌增权，碾压局降权；权重不等于加减分。',
    win_rate:'近12场有效记录中的胜率；样本不足时按实际场数计算。胜率会影响综合修正，完整12场至少10胜时提高胜率加分权重。',
    confidence:'有效计分场数÷12。缺失、未公开或无法确认的数据不会按0分补齐。',
  };
  let text=rules[key]||'';
  if(key==='score')text+=' 当前'+(aggregate?'对局加权均分 '+number(row.match_average,2)+'，综合分 ':'本场分 ')+number(row.score,2)+'。';
  if(key==='fleta'||key==='final_blow_share')text+=' 当前'+(key==='fleta'?'Fleta '+pct(row.fleta):'占比 '+pct(row.final_blow_share))+'。';
  if(key==='weight')text+=' 当前有效权重 '+pct(row.sample_weight)+'。'+(row.stomp_reason||'');
  const component=row.metrics?.[key];
  if(component){
    const share=key==='final_blow_share',reference=share?pct(component.reference):number(component.reference_per10??component.reference,2)+(component.reference_per10!=null?' /10分钟':'');
    const value=share?pct(component.value):number(component.per10??component.value,2)+(component.per10!=null?' /10分钟':'');
    text=(metricNames[key]||'该项')+'按角色和队内参考比较'+(key==='survival'?'，阵亡更少通常更好':'，表现更高通常更好')+'。本人 '+value+'；'+(component.comparison||'参考数据')+'：'+reference+'；角色内有效权重 '+pct(component.weight)+'；相对参考贡献 '+signed(component.contribution)+' 分。';
    if(share)text+=' '+rules.final_blow_share;
    if(key==='survival'){
      const allowance=row.role==='support'?4:6;
      text+=' 同时考虑局长与全队阵亡环境，低阵亡局降低敏感度。'+(row.role==='support'?'辅助':'坦克/输出')+'每10分钟超过'+allowance+'次阵亡时，还会修正生存基础贡献，因此比队友阵亡少也可能扣分。';
      if(component.per10>allowance&&!row.support_death_exempt)text+=' 本场每10分钟'+number(component.per10,2)+'次，已纳入这项修正。';
      text+=' 辅助高Fleta可触发高阵亡豁免。';
    }
  }else if(metricNames[key]&&key!=='final_blow_share'){
    text=aggregate?'按有效对局权重汇总的每10分钟数据，具体贡献请查看各场数值。':'该角色不使用此项基础权重，或参考数据不足；未纳入该项基础分，不按0分处理。';
  }
  const related={fleta:/Fleta|最后一击|进攻|SMURF/,final_blow_share:/最后一击|Fleta|进攻/,final_blows:/最后一击|Fleta|进攻/,survival:/阵亡|生存|免除/,kd:/KD|阵亡/,damage:/伤害/,healing:/治疗/,assists:/助攻/,eliminations:/击杀/}[key];
  const actual=adjustments.filter(item=>key==='score'||related?.test(item.reason||''));
  if(actual.length)text+=' '+(aggregate?'本次综合修正：':'本场实际规则：')+actual.map(item=>item.reason+' '+signed(item.points)+'分').join('；')+'。';
  else if(related&&!aggregate)text+=' 本场没有额外触发与此项对应的加减分。';
  if(key==='survival'&&row.support_death_exempt)text+=' 本场已触发辅助高Fleta阵亡豁免。';
  if(row.role==='support'&&(key==='survival'||key==='score'))text+=' '+supportDeathExplanation;
  return text;
}
function metricButton(label,explanation,name='评分数据') {
  return '<button type="button" class="metric-button" aria-label="'+escapeHtml(name+' '+label+'，查看评分说明')+'" aria-expanded="false" data-explanation="'+escapeHtml(explanation)+'">'+escapeHtml(label)+'</button>';
}
function metricValue(row,key,label,name) {return metricButton(label,metricHint(row,key),name||metricNames[key]||key);}
let hintTrigger=null,hintPanel=null;
function hideMetricHint(){
  hintTrigger?.setAttribute('aria-expanded','false');hintTrigger?.removeAttribute('aria-describedby');
  if(hintPanel)hintPanel.hidden=true;hintTrigger=null;
}
function showMetricHint(button){
  if(hintTrigger===button){hideMetricHint();return;}
  hideMetricHint();hintTrigger=button;
  if(!hintPanel){hintPanel=document.createElement('div');hintPanel.id='metricHint';hintPanel.className='metric-hint';hintPanel.setAttribute('role','status');hintPanel.setAttribute('aria-live','polite');document.body.append(hintPanel);}
  hintPanel.textContent=button.dataset.explanation;hintPanel.hidden=false;
  button.setAttribute('aria-expanded','true');button.setAttribute('aria-describedby','metricHint');
  const anchor=button.getBoundingClientRect(),box=hintPanel.getBoundingClientRect();
  const width=document.documentElement.clientWidth,height=document.documentElement.clientHeight;
  hintPanel.style.left=Math.max(10,Math.min(width-box.width-10,anchor.left))+'px';
  hintPanel.style.top=Math.max(10,Math.min(height-box.height-10,anchor.bottom+9+box.height>height?anchor.top-box.height-9:anchor.bottom+9))+'px';
  if(!globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches){
    button.animate?.([{transform:'translateX(0) scale(1)'},{transform:'translateX(-.7px) scale(.985)',offset:.28},{transform:'translateX(.5px) scale(1)',offset:.66},{transform:'translateX(0) scale(1)'}],{duration:190,easing:'cubic-bezier(.22,.7,.3,1)'});
    hintPanel.animate?.([{opacity:0,transform:'translateY(-2px)'},{opacity:1,transform:'translateY(0)'}],{duration:150,easing:'ease-out'});
  }
}
document.addEventListener('click',event=>{
  const button=event.target.closest?.('.metric-button');
  if(button){event.preventDefault();showMetricHint(button);}
  else if(!event.target.closest?.('#metricHint'))hideMetricHint();
});
document.addEventListener('keydown',event=>{if(event.key==='Escape')hideMetricHint();});
document.addEventListener('scroll',hideMetricHint,true);
globalThis.addEventListener?.('resize',hideMetricHint);
function status(text, error=false) { $('status').textContent=text; $('status').className='status'+(error?' error':''); }
function busy(value) {
  querying=value;
  for (const id of ['leisure','competitive','imageInput','recognizeFull']) if($(id)) $(id).disabled=value;
  $('details').querySelectorAll?.('.query-match').forEach(button=>{button.disabled=value||button.dataset.complete!=='true';});
  if($('recognizeCrop')) $('recognizeCrop').disabled=value||$('recognizeCrop').dataset.ready!=='true';
  if($('cropStage')) $('cropStage').style.pointerEvents=value?'none':'';
}
async function api(url, options={}) {
  const key=$('accessKey').value;
  const response=await fetch(url,{cache:'no-store',...options,headers:{...options.headers,...(key?{'X-API-Key':key}:{})}}); let body;
  try { body=await response.json(); } catch { throw new Error('服务响应异常，请确认服务正在运行'); }
  if (!response.ok || !body.success) throw new Error(body.detail || body.error || '请求失败');
  return body.data;
}
async function poll(id, fromLink=false) {
  polling=true;
  while (polling) {
    const job=await api('/api/batch-rating/tasks/'+encodeURIComponent(id));
    $('progress').value=job.progress; status(job.stage+' · '+job.completed_players+'/'+job.total_players+' 位玩家');
    if (job.status==='done') { polling=false;localStorage.removeItem('ow_batch_rating_task');render(job.result);status(fromLink?'已载入本次查询的缓存结果，可展开查看每场详情（缓存保留24小时）。':'查询完成。');return; }
    if (job.status==='error') { localStorage.removeItem('ow_batch_rating_task');throw new Error(job.error||'查询失败'); }
    await new Promise(resolve=>setTimeout(resolve,2000));
  }
}
async function query(mode, matchTags=null) {
  if(querying)return;
  let tags;
  try {
    selfBinding = RatingAccount.read(document.cookie);
    showBinding();
    tags = matchTags===null?RatingAccount.roster($('tags').value,selfBinding):RatingAccount.parseTags(matchTags.join('\n'));
    if(matchTags!==null&&(!tags.length||tags.length>12))throw new Error('当局名单无效，未开始查询。');
  } catch (error) { status(error.message,true);return; }
  busy(true);$('progress').classList.remove('hidden');$('progress').value=0;status('正在创建'+(mode==='leisure'?'休闲':'竞技')+'查询…');
  try {
    const job=await api('/api/batch-rating/'+mode,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({battletags:tags,...(matchTags!==null?{full_match:true}:{})})});
    localStorage.setItem('ow_batch_rating_task',job.task_id);
    if(/^[a-f0-9]{32}$/.test(job.task_id))globalThis.history?.replaceState(null,'','/?task='+job.task_id);
    await poll(job.task_id);
  } catch (error) { status(error.message,true); } finally { busy(false);polling=false; }
}
$('leisure').onclick=()=>query('leisure');$('competitive').onclick=()=>query('competitive');
function number(value,digits=1) { return value==null?'—':Number(value).toFixed(digits); }
function pct(value) { return value==null?'—':number(value*100)+'%'; }
function signed(value) { return (value>0?'+':'')+number(value); }
function severity(value) { const level=Math.abs(value)>=12?3:Math.abs(value)>=4?2:1;return value>0?'pos'+level:value<0?'neg'+level:'zero'; }
function delta(value,label='',explanation='') {
  const note=explanation||(label?'本次实际规则：'+label+'，分数修正 '+signed(value)+' 分。'+(label.includes('辅助阵亡')?' '+supportDeathExplanation:''):'该项相对参考的分数贡献；点击同行原始数值可查看参考标准及规则。');
  return '<span class="delta '+severity(value)+'">'+metricButton(signed(value),note,'分数贡献')+(label?' '+escapeHtml(label):'')+'</span>';
}
function tierPill(player) { return player.tier?'<span class="pill '+(player.tier==='上等马'?'upper':player.tier==='下等马'?'lower':player.tier==='中等马'?'middle':'')+'">'+escapeHtml(player.tier)+'</span>':''; }
function scoreGrade(row) { return row.score!=null&&row.score>=100?'SSS':row.score_grade||row.grade; }
function gradeTone(grade) { return ['SSS','S','A','B','C','D'].includes(grade)?'grade-'+grade.toLowerCase():'grade-unknown'; }
function gradeBadge(player) { return '<span class="grade-letter">'+escapeHtml(scoreGrade(player)||'')+'</span>'; }
function scoreMarkup(row,large=false) { return '<span class="rating-score '+gradeTone(scoreGrade(row))+(large?' large':'')+'"><strong>'+metricValue(row,'score',number(row.score,2),'评分')+'</strong>'+gradeBadge(row)+'</span>'+(row.is_smurf?' <span class="pill smurf" title="近12场至少7场Fleta严格大于65%">SMURF</span>':''); }
function hotBadge(player) { return player.hot_streak?'<span class="hot-badge" title="完整近12场至少10胜，已提高胜率加分权重">手感火热</span>':''; }
function asset(url,name,kind) {
  const image=typeof url==='string'&&url.startsWith('https://')?'<img class="'+kind+'" src="'+escapeHtml(url)+'" alt="'+escapeHtml(name)+'" loading="lazy" referrerpolicy="no-referrer" onerror="this.hidden=true">':'';
  return '<span class="asset">'+image+'<span>'+escapeHtml(name)+'</span></span>';
}
function rankBadge(rank) {
  return rank?.known?'<span class="rank-badge">'+asset(rank.icon,rank.label,'rank-icon')+'</span>':'<span class="muted">未公开/未定级</span>';
}
function outcomeBadge(match) {
  const win=match.outcome_label==='胜', loss=match.outcome_label==='负';
  const title=win?'本场胜利':loss?'本场战败':match.outcome_label==='平'?'本场平局':'胜负未知';
  return '<span class="outcome-badge '+(win?'win':loss?'loss':'draw')+'" title="'+title+'" aria-label="'+title+'"><span class="outcome-arrow" aria-hidden="true">'+(win?'↑':loss?'↓':'↔')+'</span>'+escapeHtml(match.outcome_label||'未知')+'</span>';
}
function kdLabel(row) { return row.kd_perfect?'∞':number(row.kd,2); }
function matchAwards(match) {
  return '<span class="match-awards">'+(match.is_mvp?'<span class="award mvp" title="'+escapeHtml(match.mvp_tied?'当局完整十人评分并列最高':'当局完整十人评分最高')+'">MVP</span>':'')+
    (match.is_carry?'<span class="award carry" title="Fleta达到80%">CARRY</span>':'')+
    (match.is_svp?'<span class="award svp" title="败方Fleta超过80%">SVP</span>':'')+'</span>';
}
function highlightClass(match) { return match.is_mvp?'highlight-mvp':match.is_svp?'highlight-svp':match.is_carry?'highlight-carry':''; }
function partyBadge(party) { return '<span class="pill party" title="依据当局组队成员记录">'+escapeHtml(party?.known?party.label:'组队未知')+'</span>'; }
function weightBadge(kind, label, title) {
  const icons = {
    short: '<path d="M6 3h8M6 17h8M7 3c0 4 6 4 6 7s-6 3-6 7M13 3c0 4-6 4-6 7s6 3 6 7"/>',
    long: '<circle cx="10" cy="10" r="7"/><path d="M10 6v4l3 2"/>',
    balanced: '<path d="M10 3v14M4 6h12M6 6l-3 6h6L6 6Zm8 0-3 6h6l-3-6ZM7 17h6"/>',
    stomp: '<path d="m12 2-7 9h5l-2 7 7-10h-5l2-6Z"/>'
  };
  return '<span class="match-tag '+kind+'" title="'+escapeHtml(title)+'"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+(icons[kind]||'')+'</svg>'+escapeHtml(label)+'</span>';
}
function weightNotes(match) {
  return '<span class="weight-tags">'+
    (match.short_match?weightBadge('short','短局降权','小于6.5分钟，降低本场评分权重'):'')+
    (match.long_match?weightBadge('long','长局增权','超过15分钟，小幅提高本场评分权重'):'')+
    (match.balanced_final_blows?weightBadge('balanced','势均力敌','双方最后一击总数接近且数量充足，提高本场评分权重'):'')+
    (match.is_stomp?weightBadge('stomp','碾压降权',match.stomp_reason||'明显碾压局，降低本场评分权重'):'')+'</span>';
}
const matchSorts=new Map(),childSorts=new Map(),expandedMatches=new Set();
const sortLabels={begin_ts:'时间',score:'本场评分',final_blow_share:'最后一击占比',fleta:'FLETA指数',kill:'击杀',assist:'助攻',death:'阵亡',kd:'KD',damage:'伤害/10分',healing:'治疗/10分',sample_weight:'本场权重'};
const matchKey=(playerIndex,matchIndex)=>playerIndex+':'+matchIndex;
function tableValue(row,key){
  if(key==='kd'&&row.kd_perfect)return Infinity;
  let value;
  if(['kill','assist','death'].includes(key))value=row.stats?.[key];
  else if(key==='damage'||key==='healing'){
    const raw=row.stats?.[key==='damage'?'heroDamage':'cure'];
    value=raw!=null&&row.seconds>0?raw*600/row.seconds:row.metrics?.[key]?.per10;
  }else value=row[key];
  if(value==null||!Number.isFinite(Number(value)))return null;
  value=Number(value);
  return key==='begin_ts'&&value<1e12?value*1000:value;
}
function sortedRows(rows,state,project=row=>row){
  if(!state||!Object.hasOwn(sortLabels,state.key))return [...rows];
  return rows.map((row,index)=>({row,index,value:tableValue(project(row),state.key)})).sort((a,b)=>{
    if(a.value==null)return b.value==null?a.index-b.index:1;
    if(b.value==null)return -1;
    return (a.value===b.value?0:a.value<b.value?-1:1)*(state.descending?-1:1)||a.index-b.index;
  }).map(item=>item.row);
}
function nextTableSort(state,key){
  return Object.hasOwn(sortLabels,key)?{key,descending:state?.key===key?!state.descending:true}:state;
}
function sortHeader(key,state,label=sortLabels[key]){
  const active=state?.key===key,direction=active?(state.descending?'descending':'ascending'):'none';
  const next=active&&state.descending?'从低到高':'从高到低';
  return '<th class="sortable-heading" data-sort-key="'+key+'" aria-sort="'+direction+'"><button type="button" class="sort-metric" data-sort-key="'+key+'" title="按'+label+next+'排序"><span class="sort-label">'+label+'</span><span class="sort-indicator" aria-hidden="true">'+(active?(state.descending?'↓':'↑'):'↕')+'</span></button></th>';
}
function metricHeaders(state){
  return ['score','final_blow_share','fleta','kill','assist','death','kd','damage','healing','sample_weight'].map(key=>sortHeader(key,state)).join('');
}
function metricCell(value,detail='',className=''){
  return '<td class="metric-cell '+className+'"><div class="metric-stack"><div class="metric-main">'+value+'</div><div class="metric-sub">'+detail+'</div></div></td>';
}
function statCell(match,key,field,per10=false){
  const component=match.metrics?.[key],raw=match.stats?.[field]??component?.value;
  const value=per10&&raw!=null&&match.seconds>0?raw*600/match.seconds:per10?component?.per10:raw;
  return metricCell(metricValue(match,key,number(value,0)),component?delta(component.contribution,'',metricHint(match,key)):'','stat-column');
}
function performanceCells(match){
  const count=metricValue(match,'final_blows',number(match.stats?.finalHit??match.metrics?.final_blows?.value,0),'最后一击')+'<span class="fraction-separator">/</span>'+metricValue(match,'final_blow_share',number(match.team_final_blows,0),'全队最后一击');
  return metricCell(scoreMarkup(match),matchAwards(match)+(match.score==null?'<span class="muted">未计分</span>':''),'score-column')+
    metricCell(metricValue(match,'final_blow_share',pct(match.final_blow_share),'最后一击占比'),count,'share-column')+
    metricCell(metricValue(match,'fleta',pct(match.fleta),'FLETA指数'),'','fleta-column')+
    statCell(match,'eliminations','kill')+statCell(match,'assists','assist')+statCell(match,'survival','death')+
    metricCell(metricValue(match,'kd',kdLabel(match),'KD'),'','kd-column')+
    statCell(match,'damage','heroDamage',true)+statCell(match,'healing','cure',true)+
    metricCell(metricValue(match,'weight',pct(match.sample_weight),'本场权重'),weightNotes(match),'weight-column');
}
function heroCell(match,competitive){
  return '<td class="hero-cell">'+asset(match.hero_icon,match.hero,'avatar')+'<div class="role-context"><span>'+escapeHtml(roleNames[match.role]||match.role_label||'未知位置')+'</span>'+(!competitive?outcomeBadge(match):'')+'</div></td>';
}
function recordCell(match){
  return '<td class="record-cell"><div class="record-context">'+rankBadge(match.rank)+outcomeBadge(match)+'</div></td>';
}
function reasonsCell(match){
  const reasons=(match.adjustments||[]).map(item=>delta(item.points,item.reason)).join('')||'<span class="muted">无额外加减分</span>';
  return '<td class="reasons">'+reasons+(match.warnings?.length?'<span class="cell-note">'+match.warnings.map(escapeHtml).join('；')+'</span>':'')+'</td>';
}
function matchRow(match,competitive=false,playerIndex=0,matchIndex=0){
  let ts=Number(match.begin_ts);if(ts<1e12)ts*=1000;
  const stamp=Number.isFinite(ts)&&ts>0?new Date(ts).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';
  const roster=Array.isArray(match.participants)?match.participants:[];
  const complete=match.roster_complete===true&&[10,12].includes(roster.length);
  const details=Boolean(match.match_details?.other_players?.length),open=expandedMatches.has(matchKey(playerIndex,matchIndex));
  const rosterTitle=complete?'查询当局'+roster.length+'位玩家各自近12场评分':'当局完整战网ID未公开，或旧结果没有名单；请重新查询后查看。';
  return `<tr class="${highlightClass(match)}${open?' match-expanded':''}" data-match-index="${matchIndex}"><td class="match-query-column"><button type="button" class="toggle-match-details" data-player-index="${playerIndex}" data-match-index="${matchIndex}" aria-expanded="${open}" aria-controls="match-details-${playerIndex}-${matchIndex}" title="${details?'查看当局全员本场数据与评分':'旧结果没有当局详情，请重新查询'}" ${details?'':'disabled'}>${open?'收起对局详情':'展开对局详情'}</button><button type="button" class="query-match" data-complete="${complete}" data-participants="${escapeHtml(JSON.stringify(roster))}" title="${rosterTitle}" ${complete?'':'disabled'}>查询当局</button><span class="cell-note">${complete?roster.length+'人名单':'名单不完整'}</span></td><td class="match-time">${stamp}<span class="cell-note">${number(match.seconds/60)}分钟 · ${match.team_size??'—'}人队</span>${partyBadge(match.party)}</td>`+
    heroCell(match,competitive)+(competitive?recordCell(match):'')+
    '<td class="map-cell">'+asset(match.map_icon,match.map_name,'map-icon')+'</td>'+performanceCells(match)+reasonsCell(match)+'</tr>';
}
function fullMatchPlayers(match,battletag){
  return [{...match,battletag:battletag||match.battletag||'查询目标',team:'ally',is_target:true},...(match.match_details?.other_players||[]).map(row=>({...row,is_target:false}))];
}
function matchPlayerRows(match,battletag,competitive,state){
  return sortedRows(fullMatchPlayers(match,battletag),state).map(row=>'<tr class="'+highlightClass(row)+(row.is_target?' target-player':'')+'"><td class="match-player-identity"><div class="player-labels"><span class="team-label '+(row.team==='ally'?'ally':'opponent')+'">'+(row.team==='ally'?'我方':'对方')+'</span>'+(row.is_target?'<span class="target-label">查询目标</span>':'')+'</div><strong class="match-player-tag">'+escapeHtml(row.battletag)+'</strong></td>'+heroCell(row,competitive)+(competitive?recordCell(row):'')+performanceCells(row)+reasonsCell(row)+'</tr>').join('');
}
function matchDetailsMarkup(match,battletag='',competitive=false,playerIndex=0,matchIndex=0){
  const detail=match.match_details||{},state=childSorts.get(matchKey(playerIndex,matchIndex)),total=fullMatchPlayers(match,battletag).length;
  return '<div class="match-detail-panel"><div class="match-detail-title"><strong>当局全员 '+total+' 人</strong><span>已包含查询目标 · '+(detail.rated_players??'—')+'/'+(detail.total_players??total)+'人可评分 · 点击表头切换高低排序</span></div><div class="scroll match-child-scroll" tabindex="0" aria-label="当局全员数据表"><table class="match-player-table" data-sort-scope="players" data-player-index="'+playerIndex+'" data-match-index="'+matchIndex+'"><thead><tr><th>队伍 / 玩家</th><th>英雄 / 位置</th>'+(competitive?'<th>本场段位 / 胜负</th>':'')+metricHeaders(state)+'<th>额外加减分</th></tr></thead><tbody>'+matchPlayerRows(match,battletag,competitive,state)+'</tbody></table></div></div>';
}
function matchExpansionMarkup(match,player,competitive,playerIndex,matchIndex){
  return '<tr class="match-details-row" id="match-details-'+playerIndex+'-'+matchIndex+'"><td colspan="'+(competitive?16:15)+'">'+matchDetailsMarkup(match,player.battletag,competitive,playerIndex,matchIndex)+'</td></tr>';
}
function playerMatchesMarkup(player,competitive,playerIndex){
  const rows=player.matches.map((match,index)=>({match,index}));
  return sortedRows(rows,matchSorts.get(playerIndex),item=>item.match).map(item=>matchRow(item.match,competitive,playerIndex,item.index)+(expandedMatches.has(matchKey(playerIndex,item.index))?matchExpansionMarkup(item.match,player,competitive,playerIndex,item.index):'')).join('');
}
function updateSortHeaders(table,state){
  for(const header of table.tHead.querySelectorAll('th[data-sort-key]')){
    const key=header.dataset.sortKey,active=state?.key===key;
    header.setAttribute('aria-sort',active?(state.descending?'descending':'ascending'):'none');
    header.querySelector('.sort-indicator').textContent=active?(state.descending?'↓':'↑'):'↕';
    header.querySelector('button').title='按'+sortLabels[key]+(active&&state.descending?'从低到高':'从高到低')+'排序';
  }
}
function sortMetricTable(button){
  const table=button.closest('table'),playerIndex=Number(table?.dataset.playerIndex),player=currentReport?.players[playerIndex];
  if(!player||!Object.hasOwn(sortLabels,button.dataset.sortKey))return;
  const competitive=currentReport.mode==='competitive';
  if(table.dataset.sortScope==='players'){
    const matchIndex=Number(table.dataset.matchIndex),match=player.matches[matchIndex];if(!match)return;
    const key=matchKey(playerIndex,matchIndex),state=nextTableSort(childSorts.get(key),button.dataset.sortKey);
    childSorts.set(key,state);table.tBodies[0].innerHTML=matchPlayerRows(match,player.battletag,competitive,state);updateSortHeaders(table,state);
  }else if(table.dataset.sortScope==='matches'){
    const state=nextTableSort(matchSorts.get(playerIndex),button.dataset.sortKey),positions=new Map();
    for(const row of table.querySelectorAll('.match-details-row'))positions.set(row.id,row.querySelector('.match-child-scroll').scrollLeft);
    matchSorts.set(playerIndex,state);table.tBodies[0].innerHTML=playerMatchesMarkup(player,competitive,playerIndex);updateSortHeaders(table,state);
    for(const row of table.querySelectorAll('.match-details-row'))row.querySelector('.match-child-scroll').scrollLeft=positions.get(row.id)||0;
  }
  hideMetricHint();
}
function toggleMatchDetails(button){
  const playerIndex=Number(button.dataset.playerIndex),matchIndex=Number(button.dataset.matchIndex),player=currentReport?.players[playerIndex],match=player?.matches[matchIndex];
  if(!match?.match_details?.other_players?.length||button.disabled)return;
  const key=matchKey(playerIndex,matchIndex),open=!expandedMatches.has(key),parent=button.closest('tr'),id=button.getAttribute('aria-controls');let row=$(id);
  if(open){
    expandedMatches.add(key);
    if(!row){parent.insertAdjacentHTML('afterend',matchExpansionMarkup(match,player,currentReport.mode==='competitive',playerIndex,matchIndex));row=$(id);}
  }else expandedMatches.delete(key);
  row.hidden=!open;parent.classList.toggle('match-expanded',open);
  button.setAttribute('aria-expanded',String(open));button.textContent=open?'收起对局详情':'展开对局详情';
  hideMetricHint();
}
function playerDetails(player,competitive=false,playerIndex=0) {
  const sample=escapeHtml(player.sample_note||'');
  const totals=player.averages?`<div class="detail-head"><span class="pill">${player.wins}胜${player.losses}负${player.draws}平 · 胜率${pct(player.win_rate)}</span>${hotBadge(player)}<span class="pill">Fleta>65%：${player.smurf_matches}/12场 · 7场为SMURF</span><span class="pill">KD ${kdLabel(player)} · KD≥7：${player.high_kd_matches??0}/12场</span><span class="pill">MVP ${player.mvp_matches??0} · CARRY ${player.carry_matches??0} · SVP ${player.svp_matches??0}</span><span class="weight-tags">${weightBadge('short','短局降权 '+(player.short_matches??0),'短局场数')}${weightBadge('long','长局增权 '+(player.long_matches??0),'长局场数')}${weightBadge('balanced','势均力敌 '+(player.balanced_matches??0),'双方最后一击接近的场数')}${weightBadge('stomp','碾压降权 '+(player.stomp_matches??0),'碾压局场数')}</span><span class="pill">权重合计${number(player.effective_matches)}</span><span class="muted">每10分钟：伤害 ${number(player.averages.damage_per10,0)} · 最后一击 ${number(player.averages.final_blows_per10)} · 阵亡 ${number(player.averages.survival_per10)} · 治疗 ${number(player.averages.healing_per10,0)}</span></div>`:'';
  const corrections=(player.summary_adjustments||[]).map(a=>delta(a.points,a.reason)).join(' ');
  const scoring=player.match_average==null?'':`<p class="note">对局加权均分 ${number(player.match_average,2)}${corrections?' · 综合参考修正：'+corrections:''}</p>`;
  const rows=playerMatchesMarkup(player,competitive,playerIndex),sort=matchSorts.get(playerIndex);
  const errors=player.errors?.length?'<p class="note">未计分：'+player.errors.map(e=>escapeHtml(e.match_id)+' '+escapeHtml(e.error)).join('<br>')+'</p>':'';
  return `<details><summary>${escapeHtml(player.battletag)} · ${scoreMarkup(player)} · ${player.rated_matches}场</summary><p class="note">${sample}</p>${totals}${scoring}<div class="scroll" tabindex="0" aria-label="玩家逐场详情表"><table class="metrics" data-sort-scope="matches" data-player-index="${playerIndex}"><thead><tr><th class="match-query-column">对局操作</th>${sortHeader('begin_ts',sort,'时间 / 时长 / 组队')}<th>英雄 / 位置</th>${competitive?'<th>本场记录段位 / 胜负</th>':''}<th>地图</th>${metricHeaders(sort)}<th>额外加减分</th></tr></thead><tbody>${rows}</tbody></table></div>${errors}</details>`;
}
$('details').onclick=event=>{
  const sort=event.target.closest?.('.sort-metric');
  if(sort?.dataset.sortKey&&Object.hasOwn(sortLabels,sort.dataset.sortKey)){sortMetricTable(sort);return;}
  const toggle=event.target.closest?.('.toggle-match-details');
  if(toggle?.dataset.matchIndex!==undefined){toggleMatchDetails(toggle);return;}
  const button=event.target.closest?.('.query-match');
  if(!button||button.disabled||querying||!currentReport)return;
  try{
    const tags=RatingAccount.parseTags(JSON.parse(button.dataset.participants).join('\n'));
    if(![10,12].includes(tags.length)||button.dataset.complete!=='true')throw new Error('当局名单不完整，未开始查询。');
    $('tags').value=tags.join('\n');
    $('status').scrollIntoView?.({block:'center',behavior:'smooth'});
    query(currentReport.mode,tags);
  }catch(error){status(error.message,true);}
};
function render(report) {
  hideMetricHint();matchSorts.clear();childSorts.clear();expandedMatches.clear();
  currentReport=report;$('horseSummary').value=report.summary_text||'';$('results').classList.remove('hidden');
  const competitive=report.mode==='competitive';$('rankHeader').classList.toggle('hidden',!competitive);
  const comparable=report.players.filter(p=>p.score!=null).length>=2;
  $('comparisonRankHeader').classList.toggle('hidden',!comparable);
  $('tierHeader').classList.toggle('hidden',!comparable);
  $('horseSummaryBox').classList.toggle('hidden',!comparable);
  $('comparisonPrompt').classList.toggle('hidden',comparable);
  $('resultTitle').textContent=report.mode_label+' · 最近12场队友评分';
  const summary=report.summary;
  $('summary').textContent=summary.requested_players+'位玩家 · '+summary.complete_players+'位满12场 · 共'+summary.rated_matches+'场有效对局';
  $('relativeNotice').textContent=report.relative_notice||'上下等马仅在本次查询名单内相对区分；相对评分不代表绝对水平。名单段位差距过大时，上下等马评级没有比较意义。';
  $('rankings').innerHTML=report.players.map(p=>`<tr>${comparable?'<td>'+(p.rank??'—')+'</td>':''}<td class="tag">${escapeHtml(p.battletag).replace('#','<wbr>#')}${p.status==='partial'?'<span class="cell-note">样本不足，暂定</span>':''}</td>${comparable?'<td>'+tierPill(p)+'</td>':''}<td>${scoreMarkup(p,true)}</td><td>${p.rated_matches}/12<span class="cell-note">${metricValue(p,'confidence',pct(p.confidence),'样本完整度')}完整</span></td><td>${escapeHtml(roleNames[p.primary_role]||'—')}</td>${competitive?'<td>'+rankBadge(p.latest_rank)+(p.latest_rank?.known?'<span class="cell-note">'+escapeHtml(p.latest_rank.role_label)+' · 最近可见记录</span>':'')+'</td>':''}<td>${metricValue(p,'win_rate',pct(p.win_rate),'胜率')}<span class="cell-note">${p.wins??0}胜${p.losses??0}负${p.draws??0}平</span>${hotBadge(p)}</td><td>${metricValue(p,'kd',kdLabel(p),'KD')}<span class="cell-note">≥7：${p.high_kd_matches??0}/12场</span></td><td>${metricValue(p,'final_blow_share',pct(p.final_blow_share),'最后一击占比')}</td><td>${metricValue(p,'fleta',pct(p.fleta),'Fleta')}<span class="cell-note">>65%：${p.smurf_matches??0}场</span></td><td>${metricValue(p,'damage',number(p.averages?.damage_per10,0),'伤害/10分')}</td><td>${metricValue(p,'survival',number(p.averages?.survival_per10),'阵亡/10分')}</td><td>${weightBadge('stomp',(p.stomp_matches??0)+'场','碾压局降权场数')}</td></tr>`).join('');
  $('details').innerHTML=report.players.map((player,index)=>playerDetails(player,competitive,index)).join('');
}
document.addEventListener('wheel', event => {
  if (event.ctrlKey || event.metaKey) return;
  const raw = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
  if(!raw)return;
  let scroll=event.target.closest?.('.scroll'),fallback=null;
  while(scroll){
    if(scroll.scrollWidth>scroll.clientWidth+1){
      fallback??=scroll;
      if(raw<0&&scroll.scrollLeft>0||raw>0&&scroll.scrollLeft<scroll.scrollWidth-scroll.clientWidth-1)break;
    }
    scroll=scroll.parentElement?.closest?.('.scroll')||null;
  }
  scroll??=fallback;
  if(!scroll)return;
  const amount = raw * (event.deltaMode === 1 ? 20 : event.deltaMode === 2 ? scroll.clientWidth : 1);
  if (!amount) return;
  event.preventDefault();
  scroll.scrollLeft += amount;
}, {passive:false});
$('copySummary').onclick=async()=>{try{await navigator.clipboard.writeText($('horseSummary').value);$('copySummary').textContent='已复制';setTimeout(()=>$('copySummary').textContent='复制简短文本',1500);}catch{$('horseSummary').focus();$('horseSummary').select();status('请选择并复制分档文字。');}};
$('download').onclick=()=>{if(!currentReport)return;const blob=new Blob([JSON.stringify(currentReport,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download='守望队友评分-'+currentReport.mode+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
const linkedTask=new URLSearchParams(location.search).get('task');
const validLinkedTask=linkedTask&&/^[a-f0-9]{32}$/.test(linkedTask);
const previous=linkedTask?(validLinkedTask?linkedTask:null):localStorage.getItem('ow_batch_rating_task');
if(linkedTask&&!validLinkedTask) status('详情链接无效，请使用机器人本次输出的完整网址。',true);
if(previous){busy(true);$('progress').classList.remove('hidden');poll(previous,Boolean(validLinkedTask)).catch(error=>{localStorage.removeItem('ow_batch_rating_task');status(error.message,true);}).finally(()=>busy(false));}

fetch('/health').then(response=>response.json()).then(health=>{if(health.api_key_required) document.getElementById('accessSection').classList.remove('hidden');}).catch(()=>{});
