"""BattleTag extraction and spatial voting; never invent a nickname correction."""
from __future__ import annotations

from collections import defaultdict
import re
import unicodedata

MAX_PLAYERS = 10
_DIGITS = str.maketrans({"S":"5", "s":"5", "O":"0", "o":"0", "I":"1", "l":"1", "L":"1", "B":"8", "Z":"2"})
_SUFFIX = r"[0-9SOoIlsLBZ]{3,12}"
_TAG = re.compile(r"([^#\s,，;；|]{1,32})#+(" + _SUFFIX + r")(?![0-9A-Za-z])")
_UI_LABELS = {"屏蔽列表", "好友列表", "玩家列表", "最近玩家", "队友名单", "战网昵称", "等级", "伤害", "治疗", "击杀", "助攻", "阵亡", "完成", "取消屏蔽"}


def normalize_tags(tags: list[str]) -> list[str]:
    result, seen = [], set()
    for raw in tags:
        tag = unicodedata.normalize("NFKC", str(raw)).strip()
        if not re.fullmatch(r"[^#\s]{1,32}#[0-9]{3,12}", tag):
            raise ValueError(f"BattleTag 格式无效：{tag[:48]}，应为 昵称#数字")
        if tag.casefold() not in seen:
            result.append(tag)
            seen.add(tag.casefold())
    if not result:
        raise ValueError("没有识别到完整 BattleTag，请框选账号列表重试，或输入 昵称#数字")
    if len(result) > MAX_PLAYERS:
        raise ValueError(f"每批最多 {MAX_PLAYERS} 位玩家，请分批查询")
    return result


def parse_line(text: str, *, infer_separator: bool = False) -> list[dict]:
    text = unicodedata.normalize("NFKC", text).replace("♯", "#").replace("﹟", "#")
    text = re.sub(r"^\s*(?:[•·☐□✓√]+|\[[xX✓√ ]\])\s*", "", text)
    text = re.sub(r"^\s*\d{1,2}[.)、]\s+", "", text)
    text = re.sub(r"^\s*(?:屏蔽列表|好友列表|玩家列表|战网昵称|队友名单)\s*[:：]\s*", "", text)
    # Preserve boundaries between complete IDs before removing OCR-inserted spaces.
    chunks = re.split(r"[,，;；\t]|\s{2,}|(?<=[0-9])\s+(?=[^#\s]+#)|(?<=[0-9])\s+(?=[A-Za-z\u4e00-\u9fff][^#]*$)", text)
    found = []
    for chunk in chunks:
        compact = re.sub(r"\s+", "", chunk)
        inferred = False
        if infer_separator and "#" not in compact:
            match = re.fullmatch(r"(.+?)\s+([0-9]{3,12})", chunk.strip())
            if match:
                name = re.sub(r"\s+", "", match[1])
                if name not in _UI_LABELS:
                    compact, inferred = name + "#" + match[2], True
        for match in _TAG.finditer(compact):
            name, suffix = match.groups()
            if name in _UI_LABELS:
                continue
            corrected = suffix.translate(_DIGITS)
            corrections = []
            if corrected != suffix:
                corrections.append({"original":match.group(), "corrected":name+"#"+corrected, "reason":"编号中的易混字母按数字校正，请核对"})
            if inferred:
                corrections.append({"original":chunk.strip(), "corrected":name+"#"+corrected, "reason":"补充疑似漏识别的#，请核对"})
            found.append({"battletag":name+"#"+corrected, "corrections":corrections, "inferred":inferred})
    return found


def extract_tags(text: str) -> tuple[list[str], list[dict]]:
    tags, corrections, previous = [], [], ""
    for line in text.splitlines():
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", line))
        if re.fullmatch(r"#+" + _SUFFIX, compact) and previous and "#" not in previous and previous not in _UI_LABELS:
            line = previous + compact
        for candidate in parse_line(line):
            if candidate["battletag"] not in tags:
                tags.append(candidate["battletag"])
            corrections.extend(candidate["corrections"])
        previous = compact
    return normalize_tags(tags), corrections


def _bounds(span):
    x1,y1,x2,y2 = span["box"]
    return x1,y1,x2,y2,max(1,y2-y1)


def _same_region(left, right):
    x1,y1,x2,y2,h = _bounds(left)
    a1,b1,a2,b2,k = _bounds(right)
    overlap = max(0,min(x2,a2)-max(x1,a1))
    return abs((y1+y2-b1-b2)/2) < min(h,k)*.65 and overlap > min(x2-x1,a2-a1)*.35


def candidates_from_spans(spans: list[dict]) -> list[dict]:
    """Rejoin a split name/#number only when the boxes share a row and column."""
    passes = defaultdict(list)
    for span in spans:
        passes[span["pass"]].append(span)
    records = []
    for pass_id, rows in passes.items():
        lines = []
        for span in sorted(rows,key=lambda s:((s["box"][1]+s["box"][3])/2,s["box"][0])):
            x1,y1,x2,y2,h = _bounds(span)
            closest = next((line for line in reversed(lines) if abs(line["cy"]-(y1+y2)/2)<min(line["h"],h)*.5),None)
            if closest is None:
                closest = {"cy":(y1+y2)/2,"h":h,"spans":[]}
                lines.append(closest)
            closest["spans"].append(span)
        for line in lines:
            groups = [[]]
            for span in sorted(line["spans"],key=lambda s:s["box"][0]):
                group=groups[-1]
                if group:
                    last=group[-1]
                    if span["box"][0]-last["box"][2] > max(20,line["h"]*2.5) or parse_line("".join(s["text"] for s in group)):
                        groups.append([])
                groups[-1].append(span)
            for group in groups:
                text=" ".join(s["text"] for s in group)
                box=[min(s["box"][0] for s in group),min(s["box"][1] for s in group),max(s["box"][2] for s in group),max(s["box"][3] for s in group)]
                weight=sum(max(1,len(s["text"])) for s in group)
                confidence=sum(s["confidence"]*max(1,len(s["text"])) for s in group)/weight
                for candidate in parse_line(text,infer_separator=True):
                    records.append({**candidate,"box":box,"confidence":confidence,"pass":pass_id,"clipped":any(s.get("clipped",False) for s in group)})
    # Missing '#' is recoverable only near a column containing actual BattleTags.
    anchors=[r for r in records if not r["inferred"]]
    return [r for r in records if not r["inferred"] or any(abs(r["box"][0]-a["box"][0])<max(30,_bounds(a)[4]*2) and abs(r["box"][1]-a["box"][1])<_bounds(a)[4]*15 for a in anchors)]


def vote_candidates(records: list[dict]) -> tuple[list[dict],list[dict]]:
    regions=[]
    for record in records:
        region=next((g for g in regions if _same_region(g[0],record)),None)
        if region is None:
            region=[]
            regions.append(region)
        region.append(record)
    selected, corrections = [], []
    for region in sorted(regions,key=lambda g:(g[0]["box"][1],g[0]["box"][0])):
        votes=defaultdict(dict)
        for record in region:
            old=votes[record["battletag"]].get(record["pass"])
            if old is None or record["confidence"]>old["confidence"]:
                votes[record["battletag"]][record["pass"]]=record
        order=sorted(votes,key=lambda tag:(-sum(not r.get("clipped",False) for r in votes[tag].values()),-len(votes[tag]),-sum(r["confidence"] for r in votes[tag].values())/len(votes[tag]),tag))
        tag=order[0]
        chosen=list(votes[tag].values())
        confidence=sum(r["confidence"] for r in chosen)/len(chosen)
        reasons=[]
        if len(order)>1: reasons.append("多次识别有分歧")
        if len(chosen)<2: reasons.append("仅一次识别读到该账号")
        if confidence<.94: reasons.append("文字较难辨认")
        if all(r.get("clipped",False) for r in chosen): reasons.append("文字贴近切片边缘，可能不完整")
        changes=[c for r in chosen for c in r["corrections"]]
        if changes: reasons.append("编号或分隔符经过校正")
        if tag not in {s["battletag"] for s in selected}:
            selected.append({"battletag":tag,"confidence":round(confidence,4),"needs_review":bool(reasons),"reasons":reasons,
                             "alternatives":order[1:4],"agreement":len(chosen),"clipped":all(r.get("clipped",False) for r in chosen),"box":[round(v,1) for v in chosen[0]["box"]]})
        for change in changes:
            if change not in corrections: corrections.append(change)
    normalize_tags([s["battletag"] for s in selected])
    return selected,corrections
