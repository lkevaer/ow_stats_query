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
function delta(value,label='') { return '<span class="delta '+severity(value)+'">'+signed(value)+(label?' '+escapeHtml(label):'')+'</span>'; }
function tierPill(player) { return player.tier?'<span class="pill '+(player.tier==='上等马'?'upper':player.tier==='下等马'?'lower':player.tier==='中等马'?'middle':'')+'">'+escapeHtml(player.tier)+'</span>':''; }
function scoreGrade(row) { return row.score!=null&&row.score>=100?'SSS':row.score_grade||row.grade; }
function gradeTone(grade) { return ['SSS','S','A','B','C','D'].includes(grade)?'grade-'+grade.toLowerCase():'grade-unknown'; }
function gradeBadge(player) { return '<span class="grade-letter">'+escapeHtml(scoreGrade(player)||'')+'</span>'; }
function scoreMarkup(row,large=false) { return '<span class="rating-score '+gradeTone(scoreGrade(row))+(large?' large':'')+'"><strong>'+number(row.score,2)+'</strong>'+gradeBadge(row)+'</span>'+(row.is_smurf?' <span class="pill smurf" title="近12场至少7场Fleta严格大于65%">SMURF</span>':''); }
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
  return '<span class="outcome-arrow '+(win?'win':loss?'loss':'draw')+'" title="'+title+'" aria-label="'+title+'">'+(win?'↑':loss?'↓':'↔')+'</span> '+escapeHtml(match.outcome_label);
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
function statCell(match,key,field,per10=false) {
  const component=match.metrics[key], raw=match.stats?.[field]??component?.value;
  const value=per10&&raw!=null?raw*600/match.seconds:raw;
  const reference=component?component[per10?'reference_per10':'reference']:null;
  const title=component?component.comparison+'：'+number(reference)+(per10?'/10分':''):'';
  return '<td title="'+escapeHtml(title)+'">'+number(value,per10?0:0)+(component?'<br>'+delta(component.contribution):'')+'</td>';
}
function matchRow(match,competitive=false) {
  let ts=Number(match.begin_ts);if(ts<1e12)ts*=1000;
  const stamp=Number.isFinite(ts)&&ts>0?new Date(ts).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';
  const outcomeClass=match.outcome_label==='胜'?'outcome-win':match.outcome_label==='负'?'outcome-loss':'';
  const reasons=match.adjustments.map(a=>delta(a.points,a.reason)).join('<br>')||'<span class="muted">无额外加减分</span>';
  const roster=Array.isArray(match.participants)?match.participants:[];
  const complete=match.roster_complete===true&&[10,12].includes(roster.length);
  const rosterTitle=complete?'查询当局'+roster.length+'位玩家各自近12场评分':'当局完整战网ID未公开，或旧结果没有名单；请重新查询后查看。';
  return `<tr class="${highlightClass(match)}"><td class="match-query-column"><button type="button" class="query-match" data-complete="${complete}" data-participants="${escapeHtml(JSON.stringify(roster))}" title="${rosterTitle}" ${complete?'':'disabled'}>查询当局</button><span class="cell-note">${complete?roster.length+'人名单':'名单不完整'}</span></td><td>${stamp}<span class="cell-note">${number(match.seconds/60)}分钟 · ${match.team_size}人队</span>${matchAwards(match)}${partyBadge(match.party)}</td>
    <td>${asset(match.hero_icon,match.hero,'avatar')}<span class="cell-note">${escapeHtml(roleNames[match.role]||match.role_label)}</span></td>
    ${competitive?'<td>'+rankBadge(match.rank)+'</td>':''}
    <td>${asset(match.map_icon,match.map_name,'map-icon')}</td><td class="${outcomeClass}">${outcomeBadge(match)}</td>
    <td>${scoreMarkup(match)}</td>
    <td>${pct(match.final_blow_share)}<span class="cell-note">${number(match.stats?.finalHit??match.metrics.final_blows?.value,0)}/${number(match.team_final_blows,0)}</span></td>
    <td>${pct(match.fleta)}</td>
    ${statCell(match,'eliminations','kill')}${statCell(match,'assists','assist')}${statCell(match,'survival','death')}
    <td title="击杀 / 阵亡；有击杀且零阵亡显示∞">${kdLabel(match)}</td>
    ${statCell(match,'damage','heroDamage',true)}${statCell(match,'healing','cure',true)}
    <td>${pct(match.sample_weight)}${weightNotes(match)}</td>
    <td class="reasons">${reasons}${match.warnings?.length?'<br><span class="muted">'+match.warnings.map(escapeHtml).join('<br>')+'</span>':''}</td></tr>`;
}
function playerDetails(player,competitive=false) {
  const sample=escapeHtml(player.sample_note||'');
  const totals=player.averages?`<div class="detail-head"><span class="pill">${player.wins}胜${player.losses}负${player.draws}平 · 胜率${pct(player.win_rate)}</span>${hotBadge(player)}<span class="pill">Fleta>65%：${player.smurf_matches}/12场 · 7场为SMURF</span><span class="pill">KD ${kdLabel(player)} · KD≥7：${player.high_kd_matches??0}/12场</span><span class="pill">MVP ${player.mvp_matches??0} · CARRY ${player.carry_matches??0} · SVP ${player.svp_matches??0}</span><span class="weight-tags">${weightBadge('short','短局降权 '+(player.short_matches??0),'短局场数')}${weightBadge('long','长局增权 '+(player.long_matches??0),'长局场数')}${weightBadge('balanced','势均力敌 '+(player.balanced_matches??0),'双方最后一击接近的场数')}${weightBadge('stomp','碾压降权 '+(player.stomp_matches??0),'碾压局场数')}</span><span class="pill">权重合计${number(player.effective_matches)}</span><span class="muted">每10分钟：伤害 ${number(player.averages.damage_per10,0)} · 最后一击 ${number(player.averages.final_blows_per10)} · 阵亡 ${number(player.averages.survival_per10)} · 治疗 ${number(player.averages.healing_per10,0)}</span></div>`:'';
  const corrections=(player.summary_adjustments||[]).map(a=>delta(a.points,a.reason)).join(' ');
  const scoring=player.match_average==null?'':`<p class="note">对局加权均分 ${number(player.match_average,2)}${corrections?' · 综合参考修正：'+corrections:''}</p>`;
  const rows=player.matches.map(match=>matchRow(match,competitive)).join('');
  const errors=player.errors?.length?'<p class="note">未计分：'+player.errors.map(e=>escapeHtml(e.match_id)+' '+escapeHtml(e.error)).join('<br>')+'</p>':'';
  return `<details><summary>${escapeHtml(player.battletag)} · ${scoreMarkup(player)} · ${player.rated_matches}场</summary><p class="note">${sample}</p>${totals}${scoring}<div class="scroll" tabindex="0" aria-label="玩家逐场详情表"><table class="metrics"><thead><tr><th class="match-query-column">当局名单</th><th>时间 / 时长 / 组队</th><th>英雄 / 位置</th>${competitive?'<th>本场记录段位</th>':''}<th>地图</th><th>胜负</th><th>评分</th><th>最后一击占比</th><th>Fleta</th><th>击杀</th><th>助攻</th><th>阵亡</th><th>KD</th><th>伤害/10分</th><th>治疗/10分</th><th>本场权重</th><th>额外加减分</th></tr></thead><tbody>${rows}</tbody></table></div>${errors}</details>`;
}
$('details').onclick=event=>{
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
  $('rankings').innerHTML=report.players.map(p=>`<tr>${comparable?'<td>'+(p.rank??'—')+'</td>':''}<td class="tag">${escapeHtml(p.battletag).replace('#','<wbr>#')}${p.status==='partial'?'<span class="cell-note">样本不足，暂定</span>':''}</td>${comparable?'<td>'+tierPill(p)+'</td>':''}<td>${scoreMarkup(p,true)}</td><td>${p.rated_matches}/12<span class="cell-note">${pct(p.confidence)}完整</span></td><td>${escapeHtml(roleNames[p.primary_role]||'—')}</td>${competitive?'<td>'+rankBadge(p.latest_rank)+(p.latest_rank?.known?'<span class="cell-note">'+escapeHtml(p.latest_rank.role_label)+' · 最近可见记录</span>':'')+'</td>':''}<td>${pct(p.win_rate)}<span class="cell-note">${p.wins??0}胜${p.losses??0}负${p.draws??0}平</span>${hotBadge(p)}</td><td>${kdLabel(p)}<span class="cell-note">≥7：${p.high_kd_matches??0}/12场</span></td><td>${pct(p.final_blow_share)}</td><td>${pct(p.fleta)}<span class="cell-note">>65%：${p.smurf_matches??0}场</span></td><td>${number(p.averages?.damage_per10,0)}</td><td>${number(p.averages?.survival_per10)}</td><td>${weightBadge('stomp',(p.stomp_matches??0)+'场','碾压局降权场数')}</td></tr>`).join('');
  $('details').innerHTML=report.players.map(player=>playerDetails(player,competitive)).join('');
}
document.addEventListener('wheel', event => {
  if (event.ctrlKey || event.metaKey) return;
  const scroll = event.target.closest?.('.scroll');
  if (!scroll || scroll.scrollWidth <= scroll.clientWidth + 1) return;
  const raw = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
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
