const test=require('node:test');
const assert=require('node:assert/strict');
const crop=require('../api_server/static/ocr_crop.js');

test('pointer coordinates clamp to the displayed image',()=>{
  const rect={left:20,top:40,width:200,height:100};
  assert.deepEqual(crop.point(rect,120,90),{x:.5,y:.5});
  assert.deepEqual(crop.point(rect,-20,300),{x:0,y:1});
});
test('reverse drag selects the same rectangle',()=>{
  const one={x:.1,y:.2},two={x:.6,y:.7};
  assert.deepEqual(crop.rectangle(one,two),crop.rectangle(two,one));
});
test('display selection maps to full-resolution source pixels',()=>{
  assert.deepEqual(crop.sourceRegion({x:.25,y:.2,w:.5,h:.5},4000,2000),{x:1000,y:400,width:2000,height:1000});
});
test('rounded crop stays inside the source and rejects empty selection',()=>{
  assert.deepEqual(crop.sourceRegion({x:.95,y:.9,w:.5,h:.5},100,100),{x:95,y:90,width:5,height:10});
  assert.equal(crop.sourceRegion({x:.5,y:.5,w:0,h:0},100,100),null);
});
