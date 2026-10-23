'use strict';
(() => {
  let source=null, original=null, selection=null, start=null, recognizing=false;
  const stage=$('cropStage');
  const point=event=>{
    const rect=stage.getBoundingClientRect();
    return OcrCrop.point(rect,event.clientX,event.clientY);
  };
  function updateSelection(){
    const box=$('cropSelection');
    box.classList.toggle('hidden',!selection);
    if(selection) Object.assign(box.style,{left:selection.x*100+'%',top:selection.y*100+'%',width:selection.w*100+'%',height:selection.h*100+'%'});
    const ready=!!selection&&selection.w*source.width>=8&&selection.h*source.height>=8;
    $('recognizeCrop').dataset.ready=String(ready);
    $('recognizeCrop').disabled=!ready||recognizing||polling;
    $('cropNote').textContent=ready?'已框选 '+Math.round(selection.w*source.width)+' × '+Math.round(selection.h*source.height)+' 像素，点击“识别框选区域”。':'尚未框选，当前识别整张图。';
  }
  stage.addEventListener('pointerdown',event=>{
    if(!source||recognizing||polling||event.button!==0) return;
    event.preventDefault();start=point(event);selection=null;stage.setPointerCapture(event.pointerId);updateSelection();
  });
  stage.addEventListener('pointermove',event=>{
    if(!start) return;
    const end=point(event);selection=OcrCrop.rectangle(start,end);
    updateSelection();
  });
  function finishSelection(){start=null;if(selection&&(selection.w*source.width<8||selection.h*source.height<8)) selection=null;updateSelection();}
  stage.addEventListener('pointerup',finishSelection);stage.addEventListener('pointercancel',finishSelection);

  function review(data){
    const candidates=data.candidates||[];
    const uncertain=candidates.filter(candidate=>candidate.needs_review);
    $('ocrNote').textContent='已识别 '+data.battletags.length+' 人'+(uncertain.length?' · '+uncertain.length+' 项请核对':' · 多次识别结果一致');
    $('ocrWarnings').textContent=(data.warnings||[]).join(' ');
    $('ocrReview').replaceChildren();
    for(const candidate of uncertain){
      const row=document.createElement('div');row.className='ocr-review-row';
      const name=document.createElement('strong');name.textContent=candidate.battletag;
      const reason=document.createElement('div');reason.className='note';reason.textContent=candidate.reasons.join('；')+'。以截图为准，可在账号列表中修改。';
      row.append(name,reason);
      for(const alternate of candidate.alternatives||[]){
        const button=document.createElement('button');button.type='button';button.textContent='改为 '+alternate;
        button.onclick=()=>{
          const lines=$('tags').value.split('\n');
          const index=lines.findIndex(line=>line.trim()===candidate.battletag);
          if(index<0){status('该账号已被修改，请直接检查账号列表。');return;}
          lines[index]=alternate;$('tags').value=[...new Set(lines)].join('\n');candidate.battletag=alternate;name.textContent=alternate;
          status('已替换候选，请核对截图后再查询。');
        };
        row.append(button);
      }
      $('ocrReview').append(row);
    }
  }
  async function recognize(encoded,label){
    status('正在增强识别'+label+'，请稍候…');
    $('ocrNote').textContent='正在识别…';
    let data;
    try{data=await api('/api/batch-rating/recognize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image_base64:encoded})});}
    catch(error){$('ocrNote').textContent=$('tags').value?'本次识别失败，账号列表仍是上次结果。':'本次识别失败，可以框选账号列表后重试。';throw error;}
    $('tags').value=data.battletags.join('\n');review(data);
    status('已识别 '+data.battletags.length+' 人'+(data.review_count?'，请先核对标出的疑问项。':'，请核对账号后选择休闲或竞技。'));
  }
  async function run(action){
    if(recognizing||polling) return;
    recognizing=true;busy(true);$('tags').readOnly=true;
    try{await action();}catch(error){status(error.message,true);}finally{recognizing=false;busy(false);$('tags').readOnly=false;}
  }
  async function loadImage(file){
    if(!file||recognizing||polling) return;
    if(file.size>8*1024*1024){status('图片不能超过8 MB',true);return;}
    await run(async()=>{
      const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('图片读取失败'));reader.readAsDataURL(file);});
      const image=new Image();image.src=encoded;
      try{await image.decode();}catch{throw new Error('图片无法读取，请使用PNG、JPG或WebP截图。');}
      source=image;original=encoded;selection=null;start=null;
      $('preview').src=encoded;$('cropEditor').classList.remove('hidden');updateSelection();
      $('tags').value='';$('ocrNote').textContent='';$('ocrWarnings').textContent='';$('ocrReview').replaceChildren();
      await recognize(original,'整张图');
    });
  }
  $('recognizeFull').onclick=()=>{if(source)run(()=>recognize(original,'整张图'));};
  $('recognizeCrop').onclick=()=>{
    if(!source||!selection||$('recognizeCrop').dataset.ready!=='true')return;
    run(async()=>{
      const region=OcrCrop.sourceRegion(selection,source.width,source.height);
      if(!region)throw new Error('框选范围太小，请重新框选。');
      const {x,y,width,height}=region;
      const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
      canvas.getContext('2d').drawImage(source,x,y,width,height,0,0,width,height);
      const encoded=canvas.toDataURL('image/png');
      if(encoded.length>8*1024*1024*4/3+256)throw new Error('选区图片过大，请缩小框选范围。');
      await recognize(encoded,'框选区域');
    });
  };
  $('imageInput').addEventListener('change',event=>loadImage(event.target.files[0]));
  for(const event of ['dragenter','dragover'])$('drop').addEventListener(event,e=>{e.preventDefault();$('drop').classList.add('drag');});
  $('drop').addEventListener('dragleave',()=>$('drop').classList.remove('drag'));
  $('drop').addEventListener('drop',event=>{event.preventDefault();$('drop').classList.remove('drag');loadImage(event.dataTransfer.files[0]);});
  document.addEventListener('paste',event=>{const item=[...(event.clipboardData?.items||[])].find(value=>value.type.startsWith('image/'));if(item){event.preventDefault();loadImage(item.getAsFile());}});
})();
