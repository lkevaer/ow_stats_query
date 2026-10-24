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
      elements.set(id,{value:'',dataset:{},style:{},classList:{toggle:(name,force)=>force?classes.add(name):classes.delete(name),remove:name=>classes.delete(name),contains:name=>classes.has(name)}});
    }
    return elements.get(id);
  };
  const document={getElementById:element,addEventListener:(name,callback)=>listeners.set(name,callback)};
  let cookie=savedCookie;
  Object.defineProperty(document,'cookie',{get:()=>cookie,set:value=>{cookie=value.includes('Max-Age=0')?'':value.split(';')[0];}});
  const context=vm.createContext({document,location:{protocol:'https:',search:''},URLSearchParams,localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},fetch:async(url,options)=>{requests.push({url,options});return{ok:true,json:async()=>url==='/health'?{api_key_required:false}:{success:true,data:options?.method==='POST'?{task_id:'test'}:{status:'done',progress:100,stage:'完成',result:{mode:'competitive',mode_label:'竞技',players:[player],summary:{requested_players:1,complete_players:1,rated_matches:12}}}}};},setTimeout:()=>{}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../api_server/static/rating_account.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../api_server/static/batch_rating.js'),'utf8'),context);
  return {context,element,listeners,requests,document};
}
const rank={known:true,label:'黄金3',icon:'https://example.com/gold.png',role_label:'输出'};
const match={begin_ts:1792227400000,seconds:600,hero:'测试',role:'dps',role_label:'输出',map_name:'测试地图',metrics:{},stats:{},adjustments:[],warnings:[],rank,score:65,grade:'B',outcome_label:'胜',sample_weight:1,duration_weight:1};
const player={battletag:'Example#1234',tier:'中等马',score:65,grade:'B',rated_matches:12,status:'complete',primary_role:'dps',matches:[match,match],errors:[],averages:{},latest_rank:rank};

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
  assert.match(element('rankings').innerHTML,/rank-icon.*gold\.png/);
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
  assert.equal((html.match(/<td(?:\s[^>]*)?>/g)||[]).length,17);
  for(const text of ['长局增权','势均力敌']) assert.ok(html.includes(text),text);
  const detailed=context.playerDetails(player,true);
  assert.equal((detailed.match(/<th\b/g)||[]).length,17);
  for(const text of ['本场记录段位','最后一击占比','Fleta','击杀','助攻','阵亡','KD','伤害/10分','治疗/10分']) assert.ok(detailed.includes(text),text);
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
