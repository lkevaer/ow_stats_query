"""Local neural OCR, with independent image passes and row-level retry."""
from __future__ import annotations

import os
from pathlib import Path
import threading

from PIL import Image, ImageFilter, ImageOps, ImageStat

from .ocr_candidates import candidates_from_spans, parse_line, vote_candidates

_ENGINE = None
_LOCK = threading.Lock()
ENGINE_NAME = "RapidOCR PP-OCRv6"


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr import ModelType, OCRVersion, RapidOCR
        model_dir=Path(os.getenv("OW_OCR_MODEL_DIR") or Path(os.getenv("OW_API_ROOT",str(Path.cwd()))) / "data" / "ocr_models")
        _ENGINE=RapidOCR(params={
            "Det.ocr_version":OCRVersion.PPOCRV6,"Det.model_type":ModelType.SMALL,
            "Rec.ocr_version":OCRVersion.PPOCRV6,
            "Rec.model_type":ModelType.MEDIUM,
            "Global.model_root_dir":str(model_dir.resolve()),
            "Global.log_level":"warning", "Global.text_score":.35, "Global.max_side_len":2800,
            "EngineConfig.onnxruntime.intra_op_num_threads":4,
            "EngineConfig.onnxruntime.inter_op_num_threads":1,
        })
    return _ENGINE


def image_variants(image: Image.Image):
    # Tiling preserves small lettering in a full-resolution 4K screenshot.
    scale=min(2.5,2600/max(image.size))
    scaled=image.resize((max(1,round(image.width*scale)),max(1,round(image.height*scale))),Image.Resampling.LANCZOS) if scale!=1 else image.copy()
    gray=ImageOps.grayscale(scaled)
    if ImageStat.Stat(gray).mean[0]<128:
        gray=ImageOps.invert(gray)
    enhanced=ImageOps.autocontrast(gray,cutoff=.5).filter(ImageFilter.UnsharpMask(radius=1,percent=115,threshold=3)).convert("RGB")
    return [(scaled,scale),(enhanced,scale)]


def image_tiles(image: Image.Image):
    if max(image.size)<=2200:
        return [(image,(0,0))]
    step,size=1488,2000
    positions=[(x,y) for y in range(0,image.height,step) for x in range(0,image.width,step)]
    if len(positions)>24:
        raise ValueError("截图过长，请先框选账号列表再识别")
    return [(image.crop((x,y,min(x+size,image.width),min(y+size,image.height))),(x,y)) for x,y in positions]


def recognize_neural(image: Image.Image) -> dict:
    with _LOCK:
        engine=_get_engine()
        spans=[]
        for tile_index,(tile,(ox,oy)) in enumerate(image_tiles(image)):
            if ImageStat.Stat(ImageOps.grayscale(tile)).stddev[0]<.4:
                continue
            for variant_index,(variant,scale) in enumerate(image_variants(tile)):
                result=engine(variant,use_det=True,use_cls=False,use_rec=True)
                if result.boxes is None or not result.txts:
                    continue
                for box,text,confidence in zip(result.boxes,result.txts,result.scores):
                    xs,ys=box[:,0],box[:,1]
                    clipped=(min(xs)<=2 and ox>0) or (max(xs)>=variant.width-2 and ox+tile.width<image.width)
                    spans.append({"text":text,"confidence":float(confidence),"pass":f"{tile_index}:{variant_index}","clipped":bool(clipped),
                                  "box":[float(min(xs))/scale+ox,float(min(ys))/scale+oy,float(max(xs))/scale+ox,float(max(ys))/scale+oy]})
        records=candidates_from_spans(spans)
        candidates,_=vote_candidates(records)
        # Reread only disputed rows; do not rerun a detector on a tiny text crop.
        for index,candidate in enumerate([c for c in candidates if c["needs_review"]][:12]):
            x1,y1,x2,y2=candidate["box"]
            pad=max(3,(y2-y1)*.25)
            crop=image.crop((max(0,int(x1-pad)),max(0,int(y1-pad)),min(image.width,round(x2+pad)),min(image.height,round(y2+pad))))
            # Use a fixed readable height for a short text line.
            retry=crop.resize((max(1,round(crop.width*64/max(crop.height,1))),64),Image.Resampling.LANCZOS)
            retry=ImageOps.autocontrast(ImageOps.grayscale(retry),cutoff=.5)
            if ImageStat.Stat(retry).mean[0]<128: retry=ImageOps.invert(retry)
            result=engine(retry.convert("RGB"),use_det=False,use_cls=False,use_rec=True)
            for text,confidence in zip(result.txts or [],result.scores or []):
                for parsed in parse_line(text):
                    records.append({**parsed,"confidence":float(confidence),"pass":f"row:{index}","box":candidate["box"],"clipped":candidate.get("clipped",False)})
        candidates,corrections=vote_candidates(records)
        return {"candidates":candidates,"battletags":[c["battletag"] for c in candidates],"corrections":corrections,
                "text":"\n".join(s["text"] for s in spans if s["pass"].endswith(":0")),"engine":ENGINE_NAME,"language":"zh-Hans+en",
                "review_count":sum(c["needs_review"] for c in candidates),"warnings":[],"passes":2}


def warmup():
    from PIL import ImageDraw,ImageFont
    image=Image.new("RGB",(480,80),"white")
    font=ImageFont.truetype(str(Path(__file__).resolve().parents[2]/"overstats"/"res"/"simhei.ttf"),32)
    ImageDraw.Draw(image).text((14,18),"测试玩家#1234",font=font,fill="black")
    result=recognize_neural(image)
    if "测试玩家#1234" not in result["battletags"]:
        raise RuntimeError("中文OCR模型自检未识别测试账号")
    print("Local Chinese OCR model verified.")


if __name__=="__main__":
    warmup()
