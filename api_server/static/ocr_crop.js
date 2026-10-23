(function(root,factory){const helpers=factory();if(typeof module==='object'&&module.exports)module.exports=helpers;else root.OcrCrop=helpers;})(globalThis,function(){
  'use strict';
  const clamp=value=>Math.max(0,Math.min(1,value));
  function point(rect,x,y){return {x:rect.width?clamp((x-rect.left)/rect.width):0,y:rect.height?clamp((y-rect.top)/rect.height):0};}
  function rectangle(start,end){return {x:Math.min(start.x,end.x),y:Math.min(start.y,end.y),w:Math.abs(end.x-start.x),h:Math.abs(end.y-start.y)};}
  function sourceRegion(selection,width,height){
    const x=Math.floor(clamp(selection.x)*width),y=Math.floor(clamp(selection.y)*height);
    const right=Math.min(width,Math.round(clamp(selection.x+selection.w)*width)),bottom=Math.min(height,Math.round(clamp(selection.y+selection.h)*height));
    return right>x&&bottom>y?{x,y,width:right-x,height:bottom-y}:null;
  }
  return {point,rectangle,sourceRegion};
});
