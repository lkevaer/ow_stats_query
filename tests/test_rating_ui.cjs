const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const test=require('node:test');
const assert=require('node:assert/strict');

function ui(){
  const elements=new Map();
  const element=id=>{
    if(!elements.has(id)){
      const classes=new Set(['hidden']);
      elements.set(id,{value:'',dataset:{},classList:{toggle:(name,force)=>force?classes.add(name):classes.delete(name),remove:name=>classes.delete(name),contains:name=>classes.has(name)}});
    }
    return elements.get(id);
  };
  const context=vm.createContext({document:{getElementById:element},localStorage:{getItem:()=>null},fetch:async()=>({json:async()=>({api_key_required:false})}),setTimeout:()=>{}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../api_server/static/batch_rating.js'),'utf8'),context);
  return {context,element};
}
const rank={known:true,label:'黄金3',icon:'https://example.com/gold.png',role_label:'输出'};
const match={begin_ts:1792227400000,seconds:600,hero:'测试',role:'dps',role_label:'输出',map_name:'测试地图',metrics:{},stats:{},adjustments:[],warnings:[],rank,score:65,grade:'B',outcome_label:'胜',sample_weight:1,duration_weight:1};
const player={battletag:'Example#1234',tier:'中等马',score:65,grade:'B',rated_matches:12,status:'complete',primary_role:'dps',matches:[match,match],errors:[],averages:{},latest_rank:rank};

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
  assert.equal((html.match(/<td(?:\s[^>]*)?>/g)||[]).length,16);
  for(const text of ['长局增权','接近']) assert.ok(html.includes(text),text);
  const detailed=context.playerDetails(player,true);
  assert.equal((detailed.match(/<th>/g)||[]).length,16);
  for(const text of ['本场记录段位','最后一击占比','Fleta','击杀','助攻','阵亡','KD','伤害/10分','治疗/10分']) assert.ok(detailed.includes(text),text);
  assert.doesNotMatch(detailed,/colgroup/);
});
