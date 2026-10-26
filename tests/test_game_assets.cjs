const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const assets=require('../api_server/static/game_assets.js');

test('every hero, map and rank resolves to an existing local file without calling a CDN',()=>{
  for(const [group,kind] of [['heroes','avatar'],['maps','map-icon'],['ranks','rank-icon']]){
    assert.ok(assets.catalog[group].length>0);
    for(const item of assets.catalog[group]){
      const url=assets.url(kind,item.sources[0],item.name);
      assert.match(url,/^\/static\/game-icons\/[a-z0-9/.-]+$/);
      assert.ok(fs.existsSync(path.join(__dirname,'../api_server',url.slice(1))),item.name);
    }
  }
});
test('D.Va punctuation, case variants, blood support and rank divisions resolve to real local portraits',()=>{
  const dva=assets.url('avatar',null,'D.Va');
  assert.equal(assets.url('avatar',null,'D.VA'),dva);
  assert.equal(assets.url('avatar',null,'dva'),dva);
  assert.notEqual(dva,assets.defaults.avatar);
  assert.notEqual(assets.url('avatar',null,'血律'),assets.defaults.avatar);
  assert.equal(assets.url('rank-icon',null,'黄金3'),assets.url('rank-icon',null,'黄金'));
  assert.notEqual(assets.url('rank-icon',null,'黄金3'),assets.defaults['rank-icon']);
});
test('missing and malicious image resources get packaged placeholders without retry loops',()=>{
  for(const kind of ['avatar','map-icon','rank-icon']){
    assert.equal(assets.url(kind,'https://unknown.invalid/icon.png','未知'),assets.defaults[kind]);
    assert.equal(assets.url(kind,'javascript:alert(1)','未知'),assets.defaults[kind]);
    const image={dataset:{assetKind:kind},src:'broken',onerror:()=>{}};
    assets.failed(image);
    assert.equal(image.src,assets.defaults[kind]);
    assert.equal(image.dataset.fallbackApplied,'true');
    assert.equal(image.onerror,null);
  }
});
