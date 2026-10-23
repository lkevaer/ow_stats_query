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
