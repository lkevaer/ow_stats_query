const test=require('node:test');
const assert=require('node:assert/strict');
const account=require('../api_server/static/rating_account.js');

test('binding cookies preserve Chinese names, switch choice and persistence attributes',()=>{
  const saved=account.cookie({tag:'测试玩家＃12345',autoJoin:false},true);
  assert.match(saved,/Path=\/; Max-Age=31536000; SameSite=Lax; Secure/);
  assert.deepEqual(account.read(saved),{tag:'测试玩家#12345',autoJoin:false});
  assert.deepEqual(account.read('other=value; '+saved),{tag:'测试玩家#12345',autoJoin:false});
});
test('malformed, expired or unrelated cookies do not invent a binding',()=>{
  for(const cookie of ['',account.clearCookie,'ow_rating_self=%bad','ow_rating_self='+encodeURIComponent('{"tag":"bad","autoJoin":true}'),'ow_rating_self_other=hello']){
    assert.deepEqual(account.read(cookie),{tag:'',autoJoin:false});
  }
});
test('accounts in separate browser cookie stores remain independent',()=>{
  const first=account.cookie({tag:'甲#1234',autoJoin:true});
  const second=account.cookie({tag:'乙#5678',autoJoin:false});
  assert.equal(account.read(first).tag,'甲#1234');
  assert.equal(account.read(second).tag,'乙#5678');
  assert.equal(account.read('').tag,'');
});
test('rosters merge only when enabled and remove repeated self before counting ten',()=>{
  const tags=Array.from({length:9},(_,i)=>'队友'+i+'#1234');
  assert.equal(account.roster(tags.join(' '),{tag:'本人#1234',autoJoin:true}).length,10);
  assert.equal(account.roster(tags.join(' '),{tag:'本人#1234',autoJoin:false}).length,9);
  const ten=[...tags,'本人#1234'].join('\n');
  assert.equal(account.roster(ten,{tag:'本人#1234',autoJoin:true}).length,10);
  assert.deepEqual(account.roster('Player＃12345 player#12345',{tag:'PLAYER#12345',autoJoin:true}),['Player#12345']);
  assert.deepEqual(account.roster('',{tag:'本人#1234',autoJoin:true}),['本人#1234']);
});
test('invalid input and effective rosters above ten are rejected without truncation',()=>{
  assert.throws(()=>account.roster('',{tag:'本人#1234',autoJoin:false}),/请输入账号/);
  assert.throws(()=>account.parseTags('正确#1234 错误昵称'),/完整ID/);
  assert.throws(()=>account.cookie({tag:'甲#1234 乙#1234',autoJoin:true}),/一个完整/);
  assert.throws(()=>account.roster(Array.from({length:10},(_,i)=>'队友'+i+'#1234').join('\n'),{tag:'本人#1234',autoJoin:true}),/最多10人/);
});
