const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const test=require('node:test');
const assert=require('node:assert/strict');

function ui(savedCookie=''){
  const elements=new Map();
  const listeners=new Map(),storage=new Map(),requests=[];
  const element=id=>{
    if(!elements.has(id)){
      const classes=new Set(['hidden']);
      elements.set(id,{value:'',dataset:{},style:{},classList:{toggle:(name,force)=>force?classes.add(name):classes.delete(name),add:name=>classes.add(name),remove:name=>classes.delete(name),contains:name=>classes.has(name)}});
    }
    return elements.get(id);
  };
  const document={getElementById:element,addEventListener:(name,callback)=>listeners.set(name,callback)};
  let cookie=savedCookie;
  Object.defineProperty(document,'cookie',{get:()=>cookie,set:value=>{cookie=value.includes('Max-Age=0')?'':value.split(';')[0];}});
  const context=vm.createContext({document,location:{protocol:'https:',search:''},URLSearchParams,localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},fetch:async(url,options)=>{requests.push({url,options});return{ok:true,json:async()=>url==='/health'?{api_key_required:false}:{success:true,data:options?.method==='POST'?{task_id:'test'}:{status:'done',progress:100,stage:'完成',result:{mode:'competitive',mode_label:'竞技',players:[player],summary:{requested_players:1,complete_players:1,rated_matches:12}}}}};},setTimeout:()=>{}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../api_server/static/game_assets.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../api_server/static/rating_account.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../api_server/static/batch_rating.js'),'utf8'),context);
  return {context,element,listeners,requests,document};
}
const rank={known:true,label:'黄金3',icon:'https://example.com/gold.png',role_label:'输出'};
const match={begin_ts:1792227400000,seconds:600,hero:'测试',role:'dps',role_label:'输出',map_name:'测试地图',metrics:{},stats:{},adjustments:[],warnings:[],rank,score:65,grade:'B',outcome_label:'胜',sample_weight:1,duration_weight:1};
const player={battletag:'Example#1234',tier:'中等马',score:65,grade:'B',rated_matches:12,status:'complete',primary_role:'dps',matches:[match,match],errors:[],averages:{},latest_rank:rank};

test('cache age and failed refresh are visible and escaped, without changing old reports',()=>{
  const {context}=ui();
  assert.equal(context.cacheNotice(player,true),'');
  const cached={...player,data_cache:{source:'cache',fetched_at:1800000000}};
  assert.match(context.cacheNotice(cached),/5分钟内缓存/);
  assert.match(context.playerDetails(cached),/data-freshness/);
  const stale={...cached,data_cache:{source:'stale_cache',fetched_at:1800000000,refresh_error:'<img src=x onerror=alert(1)>'}};
  const html=context.playerDetails(stale);
  assert.match(html,/刷新失败，使用此前战绩/);
  assert.match(html,/旧数据最多保留24小时/);
  assert.match(html,/&lt;img/);
  assert.doesNotMatch(html,/<img src=x/);
  assert.equal(context.cacheNotice({...cached,data_cache:{source:'cache',fetched_at:NaN}},true),'');
});

test('favorite ID controls add, select, deselect and remove while the query uses actual selected players',async()=>{
  const view=ui();
  view.element('favoriteInput').value='玩家甲#1234 玩家乙#5678';
  view.element('favoriteForm').onsubmit({preventDefault(){}});
  assert.match(view.element('favoriteList').innerHTML,/玩家甲#1234/);
  const select=index=>view.element('favoriteList').onclick({target:{closest:selector=>selector==='[data-favorite-index]'?{dataset:{favoriteIndex:String(index)}}:null}});
  select(0);select(1);
  assert.equal((view.element('favoriteList').innerHTML.match(/aria-pressed="true"/g)||[]).length,2);
  view.element('tags').value='玩家甲#1234 额外#8888';
  await view.context.query('competitive');
  assert.deepEqual(JSON.parse(view.requests.find(r=>r.options?.method==='POST').options.body).battletags,['玩家甲#1234','额外#8888','玩家乙#5678']);
  select(0);
  assert.equal((view.element('favoriteList').innerHTML.match(/aria-pressed="true"/g)||[]).length,1);
  view.element('favoriteList').onclick({target:{closest:selector=>selector==='[data-remove-favorite]'?{dataset:{removeFavorite:'1'}}:null}});
  assert.doesNotMatch(view.element('favoriteList').innerHTML,/玩家乙#5678/);
  assert.match(view.element('favoriteCount').textContent,/已选 0/);
});

test('saved names are escaped and favorite selection does not leak into a full-match query',async()=>{
  const view=ui();
  view.element('favoriteInput').value='<script>#1234';
  view.element('favoriteForm').onsubmit({preventDefault(){}});
  assert.match(view.element('favoriteList').innerHTML,/&lt;script&gt;#1234/);
  view.element('favoriteList').onclick({target:{closest:selector=>selector==='[data-favorite-index]'?{dataset:{favoriteIndex:'0'}}:null}});
  await view.context.query('competitive',['当局#1234']);
  const body=JSON.parse(view.requests.find(r=>r.options?.method==='POST').options.body);
  assert.deepEqual(body.battletags,['当局#1234']);
  assert.equal(body.full_match,true);
});

test('clear local records removes saved IDs, binding and visible report data',async()=>{
  const view=ui();
  view.element('favoriteInput').value='测试#1234';view.element('favoriteForm').onsubmit({preventDefault(){}});
  view.element('bindTag').value='本人#5678';view.element('bindForm').onsubmit({preventDefault(){}});
  view.context.render({mode:'competitive',mode_label:'竞技',scoring_version:'test-version',players:[player],summary:{requested_players:1,complete_players:1,rated_matches:12}});
  assert.match(view.element('reportMeta').textContent,/test-version/);
  await view.element('clearLocalData').onclick();
  assert.equal(view.element('boundTag').textContent,'');
  assert.equal(view.element('rankings').innerHTML,'');
  assert.equal(view.element('details').innerHTML,'');
  assert.doesNotMatch(view.element('favoriteList').innerHTML,/测试#1234/);
  assert.equal(view.element('results').classList.contains('hidden'),true);
  assert.ok(view.requests.some(request=>request.url==='/api/privacy/clear-session'));
});

test('child details are lazy, merge the target into ten players, and escape public names',()=>{
  const {context}=ui();
  const others=Array.from({length:9},(_,i)=>({...match,battletag:i===0?'<script>attack</script>':'其他玩家'+i+'#1234',team:i<4?'ally':'opponent'}));
  const complete={...match,match_details:{other_players:others,total_players:10,rated_players:10}};
  const parent=context.matchRow(complete,true,2,3);
  assert.match(parent,/展开对局详情/);
  assert.match(parent,/aria-controls="match-details-2-3"/);
  assert.doesNotMatch(parent,/其他玩家|match-player-table/);
  assert.match(context.matchRow(match),/toggle-match-details[^>]*disabled/);
  const html=context.matchDetailsMarkup(complete,player.battletag,true);
  assert.equal((html.match(/class="match-player-tag"/g)||[]).length,10);
  assert.match(html,/当局全员 10 人/);
  assert.equal((html.match(/class="target-label"/g)||[]).length,1);
  assert.match(html,/Example#1234/);
  assert.match(html,/&lt;script&gt;attack/);
  assert.doesNotMatch(html,/<script>/);
});

test('metric buttons show real contributions and exact threshold explanations without a dialog',()=>{
  const {context}=ui();
  const row={...match,fleta:.8,metrics:{survival:{reference_per10:4,weight:.4,contribution:-3.25,comparison:'全队参考'}},adjustments:[{reason:'阵亡参考扣分',points:-2.5}]};
  const hint=context.metricHint(row,'survival');
  assert.match(hint,/全队参考：4.00 \/10分钟/);
  assert.match(hint,/-3.3 分/);
  assert.match(hint,/阵亡参考扣分 -2.5分/);
  assert.match(hint,/比队友阵亡少也可能扣分/);
  assert.match(context.metricHint(row,'fleta'),/败方严格超过80%/);
  assert.match(context.metricHint(row,'healing'),/不按0分处理/);
  const supportHint=context.metricHint({...row,role:'support'},'survival');
  assert.match(supportHint,/普通负荷通常不超过10分/);
  assert.match(supportHint,/15分仅限制这项额外扣分，生存基础贡献仍另计/);
  assert.match(supportHint,/辅助每10分钟超过4次阵亡/);
  assert.match(context.delta(-5,'辅助阵亡偏多'),/15分仅限制这项额外扣分/);
  const html=context.matchRow({...row,stats:{kill:10,assist:2,death:9}});
  for(const value of ['10','2','9','80.0%'])assert.ok(html.includes('>'+value+'</button>'),value);
  assert.match(html,/data-explanation=/);
  assert.doesNotMatch(html,/dialog|onclick=/);
  assert.doesNotMatch(context.metricButton('1','<img onerror="alert(1)">'),/data-explanation="<img/);
});

test('every match has a leftmost query action and incomplete rosters stay disabled',()=>{
  const {context}=ui();
  const tags=Array.from({length:10},(_,index)=>'玩家'+index+'#1234');
  const html=context.matchRow({...match,participants:tags,roster_complete:true});
  assert.match(html,/<tr[^>]*><td class="match-query-column"><button/);
  assert.match(html,/查询当局/);
  assert.doesNotMatch(html,/>查询当局<\/button>.*disabled/);
  assert.match(context.matchRow(match),/class="query-match"[^>]*disabled/);
});

test('match queries replace the roster, preserve mode, and exclude an unrelated bound account',async()=>{
  const cookie='ow_rating_self='+encodeURIComponent(JSON.stringify({tag:'局外本人#5555',autoJoin:true}));
  const {context,element,requests,document}=ui(cookie);
  const originalCookie=document.cookie;
  const tags=Array.from({length:12},(_,index)=>'玩家'+index+'#1234');
  context.render({mode:'leisure',mode_label:'休闲',summary:{requested_players:1,complete_players:1,rated_matches:12},players:[player]});
  element('details').onclick({target:{closest:()=>({disabled:false,dataset:{complete:'true',participants:JSON.stringify(tags)}})}});
  await new Promise(resolve=>setImmediate(resolve));
  const submitted=requests.find(request=>request.options?.method==='POST');
  assert.equal(submitted.url,'/api/batch-rating/leisure');
  assert.deepEqual(JSON.parse(submitted.options.body),{battletags:tags,full_match:true});
  assert.equal(element('tags').value,tags.join('\n'));
  assert.equal(document.cookie,originalCookie);
});

test('an incomplete match action cannot start a partial roster query',async()=>{
  const {context,element,requests}=ui();
  context.render({mode:'competitive',mode_label:'竞技',summary:{requested_players:1,complete_players:1,rated_matches:12},players:[player]});
  element('details').onclick({target:{closest:()=>({disabled:true,dataset:{complete:'false',participants:'[]'}})}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.filter(request=>request.options?.method==='POST').length,0);
});

test('wins and losses use accessible green-up and red-down arrows',()=>{
  const {context}=ui();
  assert.match(context.outcomeBadge({outcome_label:'胜'}),/win.*本场胜利.*↑/);
  assert.match(context.outcomeBadge({outcome_label:'负'}),/loss.*本场战败.*↓/);
  assert.match(context.outcomeBadge({outcome_label:'平'}),/↔/);
});
test('competitive overview and per-match tables include rank icons',()=>{
  const {context,element}=ui();
  context.render({mode:'competitive',mode_label:'竞技',summary:{requested_players:1,complete_players:1,rated_matches:12},players:[player]});
  assert.match(element('rankings').innerHTML,/rank-icon.*\/static\/game-icons\/ranks\/[a-f0-9]+\.webp/);
  assert.match(element('details').innerHTML,/本场记录段位/);
  assert.match(element('details').innerHTML,/黄金3/);
  assert.equal(element('rankHeader').classList.contains('hidden'),false);
});
test('leisure hides competitive columns even after a competitive report',()=>{
  const {context,element}=ui();
  const report={mode:'competitive',mode_label:'竞技',summary:{requested_players:1,complete_players:1,rated_matches:12},players:[player]};
  context.render(report);context.render({...report,mode:'leisure',mode_label:'休闲'});
  assert.doesNotMatch(element('rankings').innerHTML,/rank-icon/);
  assert.doesNotMatch(element('details').innerHTML,/本场记录段位/);
  assert.equal(element('rankHeader').classList.contains('hidden'),true);
});
test('missing rank is shown as unknown without an invented icon',()=>{
  const {context}=ui();
  assert.match(context.rankBadge(null),/未公开\/未定级/);
  assert.doesNotMatch(context.rankBadge(null),/<img/);
});

test('MVP and CARRY coexist while the entire row keeps the MVP gold border',()=>{
  const {context}=ui();
  const html=context.matchRow({...match,is_mvp:true,is_carry:true,is_svp:true});
  assert.match(html,/<tr class="highlight-mvp"/);
  assert.match(html,/award mvp.*>MVP/);
  assert.match(html,/award carry.*>CARRY/);
  assert.match(html,/award svp.*>SVP/);
  assert.doesNotMatch(html,/<tr class="highlight-svp"/);
});

test('SVP gets pale purple priority over CARRY and CARRY alone gets pale gold',()=>{
  const {context}=ui();
  assert.match(context.matchRow({...match,is_carry:true,is_svp:true}),/<tr class="highlight-svp"/);
  assert.match(context.matchRow({...match,is_carry:true}),/<tr class="highlight-carry"/);
  assert.doesNotMatch(context.matchRow(match),/highlight-(mvp|svp|carry)/);
});

test('party badges use recorded labels and do not assume solo when unknown',()=>{
  const {context}=ui();
  assert.match(context.matchRow({...match,party:{known:true,size:3,label:'三排'}}),/pill party.*>三排/);
  assert.match(context.partyBadge(null),/组队未知/);
  assert.doesNotMatch(context.partyBadge(null),/单排/);
});

test('KD, new SMURF threshold and aggregate reference corrections appear in details',()=>{
  const {context}=ui();
  const html=context.playerDetails({...player,kd_perfect:true,smurf_matches:7,match_average:60,summary_adjustments:[{points:18,reason:'SMURF专项加分'}]});
  assert.match(html,/Fleta>65%：7\/12场 · 7场为SMURF/);
  assert.match(html,/KD ∞/);
  assert.match(html,/SMURF专项加分/);
  assert.doesNotMatch(html,/Fleta≥100%/);
});

test('all five numeric grades use their own subtle color class',()=>{
  const {context}=ui();
  for(const grade of ['S','A','B','C','D']){
    assert.match(context.scoreMarkup({score:80,grade}),new RegExp('grade-'+grade.toLowerCase()));
  }
  const smurf=context.scoreMarkup({score:95,grade:'SMURF',score_grade:'S',is_smurf:true});
  assert.match(smurf,/grade-s/);
  assert.match(smurf,/SMURF/);
});

test('hot streak appears alongside the win rate in overview and detail header',()=>{
  const {context,element}=ui();
  const hot={...player,hot_streak:true,wins:10,losses:2,win_rate:10/12};
  context.render({mode:'competitive',mode_label:'竞技',summary:{requested_players:1,complete_players:1,rated_matches:12},players:[hot]});
  assert.match(element('rankings').innerHTML,/83\.3%.*手感火热/);
  assert.match(element('details').innerHTML,/手感火热/);
  assert.equal(context.hotBadge({...hot,hot_streak:false}),'');
});

test('classic match table restores independent columns without losing current form indicators',()=>{
  const {context}=ui();
  const html=context.matchRow({...match,stats:{kill:20,assist:8,death:3,heroDamage:6000,cure:2000,finalHit:10},kd:20/3,team_final_blows:30,opponent_final_blows:31,balanced_final_blows:true,long_match:true},true);
  assert.equal((html.match(/<td(?:\s[^>]*)?>/g)||[]).length,16);
  for(const text of ['长局增权','势均力敌']) assert.ok(html.includes(text),text);
  const detailed=context.playerDetails(player,true);
  assert.equal((detailed.match(/<th\b/g)||[]).length,16);
  for(const text of ['本场记录段位','最后一击占比','FLETA指数','击杀','助攻','阵亡','KD','伤害/10分','治疗/10分']) assert.ok(detailed.includes(text),text);
  assert.doesNotMatch(detailed,/<th>胜负<\/th>/);
  assert.doesNotMatch(detailed,/colgroup/);
});

test('a single player keeps scores and details without a horse tier or comparison rank',()=>{
  const {context,element}=ui();
  const report={mode:'competitive',mode_label:'竞技',players:[player],summary:{requested_players:1,complete_players:1,rated_matches:12}};
  context.render(report);
  assert.doesNotMatch(element('rankings').innerHTML,/中等马/);
  assert.match(element('details').innerHTML,/Example#1234/);
  assert.equal(element('tierHeader').classList.contains('hidden'),true);
  assert.equal(element('comparisonRankHeader').classList.contains('hidden'),true);
  assert.equal(element('horseSummaryBox').classList.contains('hidden'),true);
  assert.equal(element('comparisonPrompt').classList.contains('hidden'),false);
  context.render({...report,players:[{...player,tier:'上等马'},{...player,battletag:'Second#1234',tier:'下等马'}]});
  assert.match(element('rankings').innerHTML,/上等马/);
  assert.equal(element('horseSummaryBox').classList.contains('hidden'),false);
  assert.equal(element('comparisonPrompt').classList.contains('hidden'),true);
});

test('SSS is reserved for 100 and coexists with the separate SMURF award',()=>{
  const {context}=ui();
  assert.match(context.scoreMarkup({score:100,grade:'S',is_smurf:true}),/grade-sss.*100\.00.*SSS.*SMURF/);
  assert.match(context.scoreMarkup({score:99.99,grade:'S'}),/grade-s"/);
  assert.doesNotMatch(context.scoreMarkup({score:null,grade:null}),/grade-sss/);
});

test('binding, toggle and unbinding persist through the cookie and affect actual query payloads',async()=>{
  const view=ui();
  view.element('bindTag').value='本人＃12345';
  view.element('bindForm').onsubmit({preventDefault(){}});
  assert.equal(view.element('boundTag').textContent,'本人#12345');
  assert.equal(view.element('autoJoin').checked,true);
  const reopened=ui(view.document.cookie);
  assert.equal(reopened.element('boundTag').textContent,'本人#12345');
  reopened.element('tags').value='队友#12345 本人#12345';
  await reopened.context.query('competitive');
  const request=reopened.requests.find(r=>r.options?.method==='POST');
  assert.deepEqual(JSON.parse(request.options.body).battletags,['队友#12345','本人#12345']);
  reopened.element('autoJoin').checked=false;
  reopened.element('autoJoin').onchange();
  const disabled=ui(reopened.document.cookie);
  assert.equal(disabled.element('autoJoin').checked,false);
  disabled.element('tags').value='队友#12345';
  await disabled.context.query('competitive');
  assert.deepEqual(JSON.parse(disabled.requests.find(r=>r.options?.method==='POST').options.body).battletags,['队友#12345']);
  disabled.element('unbind').onclick();
  assert.equal(ui(disabled.document.cookie).element('boundTag').textContent,'');
  assert.equal(disabled.element('autoJoin').disabled,true);
  assert.equal(ui().element('boundTag').textContent,'');
});

test('adding self beyond ten refuses before making a query instead of dropping players',async()=>{
  const {context,element,requests}=ui();
  element('bindTag').value='本人#1234';element('bindForm').onsubmit({preventDefault(){}});
  element('tags').value=Array.from({length:10},(_,i)=>'队友'+i+'#1234').join('\n');
  await context.query('competitive');
  assert.equal(requests.some(r=>r.options?.method==='POST'),false);
  assert.match(element('status').textContent,/最多10人/);
});

test('wheel is horizontal over an overflowing table, including at its ends',()=>{
  const {listeners}=ui();
  const table={scrollWidth:1000,clientWidth:500,scrollLeft:0};let prevented=false;
  listeners.get('wheel')({target:{closest:()=>table},deltaY:80,deltaX:0,deltaMode:0,preventDefault(){prevented=true;}});
  assert.equal(table.scrollLeft,80);assert.equal(prevented,true);
  table.scrollLeft=500;prevented=false;
  listeners.get('wheel')({target:{closest:()=>table},deltaY:1,deltaX:0,deltaMode:1,preventDefault(){prevented=true;}});
  assert.equal(prevented,true);
});

test('wheel remains native on page margins, small tables and browser zoom',()=>{
  const {listeners}=ui();
  for(const [table,ctrlKey] of [[null,false],[{scrollWidth:400,clientWidth:500},false],[{scrollWidth:1000,clientWidth:500},true]]){
    listeners.get('wheel')({target:{closest:()=>table},ctrlKey,deltaY:80,deltaX:0,deltaMode:0,preventDefault(){assert.fail('Native scrolling/zoom must remain available');}});
  }
});

test('sorting handles all requested metrics, infinity, missing values and stable ties without mutating input',()=>{
  const {context}=ui();
  const rows=[
    {...match,id:'low',score:20,kd:2,final_blow_share:.1,fleta:.2,sample_weight:.8,stats:{kill:4,assist:1,death:2,heroDamage:2000,cure:100}},
    {...match,id:'high',score:90,kd:5,final_blow_share:.4,fleta:.8,sample_weight:1.2,stats:{kill:10,assist:6,death:5,heroDamage:8000,cure:900}},
    {...match,id:'missing',score:null,kd:null,final_blow_share:null,fleta:null,sample_weight:null,stats:{}},
  ];
  for(const key of ['score','final_blow_share','fleta','kill','assist','death','kd','damage','healing','sample_weight']){
    assert.deepEqual(Array.from(context.sortedRows(rows,{key,descending:true}),row=>row.id),['high','low','missing'],key);
    assert.deepEqual(Array.from(context.sortedRows(rows,{key,descending:false}),row=>row.id),['low','high','missing'],key);
  }
  assert.deepEqual(rows.map(row=>row.id),['low','high','missing']);
  assert.deepEqual(Array.from(context.sortedRows([{id:'perfect',kd_perfect:true},{id:'finite',kd:100},{id:'unknown',kd:null}],{key:'kd',descending:true}),row=>row.id),['perfect','finite','unknown']);
  assert.deepEqual(Array.from(context.sortedRows([{id:'first',score:50},{id:'second',score:50}],{key:'score',descending:true}),row=>row.id),['first','second']);
  assert.equal(context.tableValue({stats:{heroDamage:3000},seconds:300},'damage'),6000);
  assert.equal(context.nextTableSort(null,'score').descending,true);
  assert.equal(context.nextTableSort({key:'score',descending:true},'score').descending,false);
});

test('both match and full-player tables expose the same independent metric sort controls',()=>{
  const {context}=ui();
  const full={...match,match_details:{other_players:Array.from({length:11},(_,i)=>({...match,battletag:'玩家'+i+'#1234'})),rated_players:12,total_players:12}};
  const parent=context.playerDetails({...player,matches:[full]},true,0);
  const child=context.matchDetailsMarkup(full,player.battletag,true,0,0);
  for(const key of ['score','final_blow_share','fleta','kill','assist','death','kd','damage','healing','sample_weight']){
    assert.ok(parent.includes('data-sort-key="'+key+'"'),key);
    assert.ok(child.includes('data-sort-key="'+key+'"'),key);
  }
  assert.equal((child.match(/class="match-player-tag"/g)||[]).length,12);
  assert.equal(full.match_details.other_players.length,11);
});

test('wheel over a child table falls back to its parent when it cannot scroll and hands off at its edge',()=>{
  const {listeners}=ui();
  const parent={scrollWidth:1400,clientWidth:700,scrollLeft:0};
  const child={scrollWidth:700,clientWidth:700,scrollLeft:0,parentElement:{closest:()=>parent}};
  let prevented=0;
  const wheel=delta=>listeners.get('wheel')({target:{closest:()=>child},deltaX:0,deltaY:delta,deltaMode:0,preventDefault(){prevented++;}});
  wheel(80);assert.equal(parent.scrollLeft,80);assert.equal(child.scrollLeft,0);
  child.scrollWidth=800;child.scrollLeft=100;
  wheel(50);assert.equal(parent.scrollLeft,130);assert.equal(child.scrollLeft,100);
  wheel(-40);assert.equal(child.scrollLeft,60);assert.equal(parent.scrollLeft,130);
  assert.equal(prevented,3);
});
