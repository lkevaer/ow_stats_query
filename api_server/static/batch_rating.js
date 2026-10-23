'use strict';
let currentReport = null, polling = false;
const $ = id => document.getElementById(id);
const escapeHtml = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const roleNames = {tank:'坦克 T',dps:'输出 C',support:'辅助 N'};
const metricNames = {damage:'伤害',final_blows:'最后一击',survival:'生存',eliminations:'击杀',healing:'治疗',assists:'助攻',mitigation:'承伤',final_blow_share:'最后一击占比'};
function status(text, error=false) { $('status').textContent=text; $('status').className='status'+(error?' error':''); }
function busy(value) { for (const id of ['leisure','competitive','imageInput']) $(id).disabled=value; }
async function api(url, options={}) {
  const key=$('accessKey').value;
  const response=await fetch(url,{cache:'no-store',...options,headers:{...options.headers,...(key?{'X-API-Key':key}:{})}}); let body;
  try { body=await response.json(); } catch { throw new Error('服务响应异常，请确认服务正在运行'); }
  if (!response.ok || !body.success) throw new Error(body.error || '请求失败');
  return body.data;
}
async function loadImage(file) {
  if (!file) return;
  if (file.size>8*1024*1024) { status('图片不能超过8 MB',true); return; }
  busy(true); status('正在识别账号截图…');
  try {
    const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});
    $('preview').src=encoded; $('preview').classList.remove('hidden');
    const data=await api('/api/batch-rating/recognize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image_base64:encoded})});
    $('tags').value=data.battletags.join('\n');
    $('ocrNote').textContent=data.corrections.length?'编号校正：'+data.corrections.map(c=>c.original+' → '+c.corrected).join('；'):'已识别 '+data.battletags.length+' 位玩家';
    status('已识别 '+data.battletags.length+' 位玩家，请选择休闲或竞技查询。');
  } catch (error) { status(error.message,true); } finally { busy(false); }
}
$('imageInput').addEventListener('change',e=>loadImage(e.target.files[0]));
for (const event of ['dragenter','dragover']) $('drop').addEventListener(event,e=>{e.preventDefault();$('drop').classList.add('drag');});
$('drop').addEventListener('dragleave',()=>$('drop').classList.remove('drag'));
$('drop').addEventListener('drop',e=>{e.preventDefault();$('drop').classList.remove('drag');loadImage(e.dataTransfer.files[0]);});
document.addEventListener('paste',e=>{const item=[...(e.clipboardData?.items||[])].find(x=>x.type.startsWith('image/'));if(item){e.preventDefault();loadImage(item.getAsFile());}});
async function poll(id) {
  polling=true;
  while (polling) {
    const job=await api('/api/batch-rating/tasks/'+encodeURIComponent(id));
    $('progress').value=job.progress; status(job.stage+' · '+job.completed_players+'/'+job.total_players+' 位玩家');
    if (job.status==='done') { polling=false;localStorage.removeItem('ow_batch_rating_task');render(job.result);status('查询完成。');return; }
    if (job.status==='error') { localStorage.removeItem('ow_batch_rating_task');throw new Error(job.error||'查询失败'); }
    await new Promise(resolve=>setTimeout(resolve,2000));
  }
}
async function query(mode) {
  const tags=$('tags').value.split(/[\n,，;；]+/).map(s=>s.trim()).filter(Boolean);
  if (!tags.length) { status('请先上传截图或输入完整 BattleTag。',true);return; }
  busy(true);$('progress').classList.remove('hidden');$('progress').value=0;status('正在创建'+(mode==='leisure'?'休闲':'竞技')+'查询…');
  try {
    const job=await api('/api/batch-rating/'+mode,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({battletags:tags})});
    localStorage.setItem('ow_batch_rating_task',job.task_id);await poll(job.task_id);
  } catch (error) { status(error.message,true); } finally { busy(false);polling=false; }
}
$('leisure').onclick=()=>query('leisure');$('competitive').onclick=()=>query('competitive');
function number(value,digits=1) { return value==null?'—':Number(value).toFixed(digits); }
function pct(value) { return value==null?'—':number(value*100)+'%'; }
function signed(value) { return (value>0?'+':'')+number(value); }
function severity(value) { const level=Math.abs(value)>=12?3:Math.abs(value)>=4?2:1;return value>0?'pos'+level:value<0?'neg'+level:'zero'; }
function delta(value,label='') { return '<span class="delta '+severity(value)+'">'+signed(value)+(label?' '+escapeHtml(label):'')+'</span>'; }
function tierPill(player) { return '<span class="pill '+(player.tier==='上等马'?'upper':player.tier==='下等马'?'lower':'middle')+'">'+escapeHtml(player.tier)+'</span>'; }
function gradeBadge(player) { return player.is_smurf?'<span class="pill smurf">SMURF</span>':'<span class="badge">'+escapeHtml(player.grade||'')+'</span>'; }
function asset(url,name,kind) {
  const image=typeof url==='string'&&url.startsWith('https://')?'<img class="'+kind+'" src="'+escapeHtml(url)+'" alt="'+escapeHtml(name)+'" loading="lazy" referrerpolicy="no-referrer" onerror="this.hidden=true">':'';
  return '<span class="asset">'+image+'<span>'+escapeHtml(name)+'</span></span>';
}
function statCell(match,key,field,per10=false) {
  const component=match.metrics[key], raw=match.stats?.[field]??component?.value;
  const value=per10&&raw!=null?raw*600/match.seconds:raw;
  const reference=component?component[per10?'reference_per10':'reference']:null;
  const title=component?component.comparison+'：'+number(reference)+(per10?'/10分':''):'';
  return '<td title="'+escapeHtml(title)+'">'+number(value,per10?0:0)+(component?'<br>'+delta(component.contribution):'')+'</td>';
}
function matchRow(match) {
  let ts=Number(match.begin_ts);if(ts<1e12)ts*=1000;
  const stamp=Number.isFinite(ts)&&ts>0?new Date(ts).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';
  const outcomeClass=match.outcome_label==='胜'?'outcome-win':match.outcome_label==='负'?'outcome-loss':'';
  const reasons=match.adjustments.map(a=>delta(a.points,a.reason)).join('<br>')||'<span class="muted">无额外加减分</span>';
  return `<tr><td>${stamp}<span class="cell-note">${number(match.seconds/60)}分钟 · ${match.team_size}人队</span></td>
    <td>${asset(match.hero_icon,match.hero,'avatar')}<span class="cell-note">${escapeHtml(roleNames[match.role]||match.role_label)}</span></td>
    <td>${asset(match.map_icon,match.map_name,'map-icon')}</td><td class="${outcomeClass}">${escapeHtml(match.outcome_label)}</td>
    <td><strong>${number(match.score,2)}</strong> ${escapeHtml(match.grade)}</td>
    <td>${pct(match.final_blow_share)}<span class="cell-note">${number(match.stats?.finalHit??match.metrics.final_blows?.value,0)}/${number(match.team_final_blows,0)}</span></td>
    <td>${pct(match.fleta)}${match.final_blow_share>=.5?'<span class="cell-note outcome-win">达标</span>':''}</td>
    ${statCell(match,'eliminations','kill')}${statCell(match,'assists','assist')}${statCell(match,'survival','death')}
    ${statCell(match,'damage','heroDamage',true)}${statCell(match,'healing','cure',true)}
    <td>${pct(match.sample_weight)}${match.is_stomp?'<br><span class="pill discount" title="'+escapeHtml(match.stomp_reason)+'">碾压降权</span>':''}${match.duration_weight<1?'<span class="cell-note">短局降权</span>':''}</td>
    <td class="reasons">${reasons}${match.warnings?.length?'<br><span class="muted">'+match.warnings.map(escapeHtml).join('<br>')+'</span>':''}</td></tr>`;
}
function playerDetails(player) {
  const sample=escapeHtml(player.sample_note||'');
  const totals=player.averages?`<div class="detail-head"><span class="pill">${player.wins}胜${player.losses}负${player.draws}平 · 胜率${pct(player.win_rate)}</span><span class="pill">Fleta≥100%：${player.smurf_matches}/12场</span><span class="pill">碾压${player.stomp_matches}场 · 有效权重${number(player.effective_matches)}场</span><span class="muted">每10分钟：伤害 ${number(player.averages.damage_per10,0)} · 最后一击 ${number(player.averages.final_blows_per10)} · 阵亡 ${number(player.averages.survival_per10)} · 治疗 ${number(player.averages.healing_per10,0)}</span></div>`:'';
  const rows=player.matches.map(matchRow).join('');
  const errors=player.errors?.length?'<p class="note">未计分：'+player.errors.map(e=>escapeHtml(e.match_id)+' '+escapeHtml(e.error)).join('<br>')+'</p>':'';
  return `<details><summary>${escapeHtml(player.battletag)} · ${number(player.score)} ${gradeBadge(player)} · ${player.rated_matches}场</summary><p class="note">${sample}</p>${totals}<div class="scroll"><table class="metrics"><thead><tr><th>时间 / 时长</th><th>英雄 / 位置</th><th>地图</th><th>胜负</th><th>评分</th><th>最后一击占比</th><th>Fleta</th><th>击杀</th><th>助攻</th><th>阵亡</th><th>伤害/10分</th><th>治疗/10分</th><th>本场权重</th><th>额外加减分</th></tr></thead><tbody>${rows}</tbody></table></div>${errors}</details>`;
}
function render(report) {
  currentReport=report;$('horseSummary').value=report.summary_text||'';$('results').classList.remove('hidden');
  $('resultTitle').textContent=report.mode_label+' · 最近12场队友评分';
  const summary=report.summary;
  $('summary').textContent=summary.requested_players+'位玩家 · '+summary.complete_players+'位满12场 · 共'+summary.rated_matches+'场有效对局';
  $('relativeNotice').textContent=report.relative_notice||'上下等马仅在本次查询名单内相对区分；相对评分不代表绝对水平。';
  $('rankings').innerHTML=report.players.map(p=>`<tr><td>${p.rank??'—'}</td><td class="tag">${escapeHtml(p.battletag)}${p.status==='partial'?'<span class="cell-note">样本不足，暂定</span>':''}</td><td>${tierPill(p)}</td><td><span class="score">${number(p.score,2)}</span> ${gradeBadge(p)}</td><td>${p.rated_matches}/12<span class="cell-note">${pct(p.confidence)}完整</span></td><td>${escapeHtml(roleNames[p.primary_role]||'—')}</td><td>${pct(p.win_rate)}<span class="cell-note">${p.wins??0}胜${p.losses??0}负${p.draws??0}平</span></td><td>${pct(p.final_blow_share)}</td><td>${pct(p.fleta)}<span class="cell-note">≥100%：${p.smurf_matches??0}场</span></td><td>${number(p.averages?.damage_per10,0)}</td><td>${number(p.averages?.survival_per10)}</td><td>${p.stomp_matches??0}场</td></tr>`).join('');
  $('details').innerHTML=report.players.map(playerDetails).join('');
}
$('copySummary').onclick=async()=>{try{await navigator.clipboard.writeText($('horseSummary').value);$('copySummary').textContent='已复制';setTimeout(()=>$('copySummary').textContent='复制简短文本',1500);}catch{$('horseSummary').focus();$('horseSummary').select();status('请选择并复制分档文字。');}};
$('download').onclick=()=>{if(!currentReport)return;const blob=new Blob([JSON.stringify(currentReport,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download='守望队友评分-'+currentReport.mode+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
const previous=localStorage.getItem('ow_batch_rating_task');
if(previous){busy(true);$('progress').classList.remove('hidden');poll(previous).catch(error=>{localStorage.removeItem('ow_batch_rating_task');status(error.message,true);}).finally(()=>busy(false));}

fetch('/health').then(response=>response.json()).then(health=>{if(health.api_key_required) document.getElementById('accessSection').classList.remove('hidden');}).catch(()=>{});
