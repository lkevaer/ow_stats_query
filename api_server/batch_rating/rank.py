"""Display upstream rank snapshots without using rank in the rating formula."""
from __future__ import annotations

import math
import re


def _name(value):
    text=str(value or "").strip()
    return "" if text.lower() in {"none","null","undefined","unranked","未定义","未定级"} else text


def match_rank(player: dict, source: dict, registry: list[dict]) -> dict | None:
    mode=str(source.get("gameMode") or "").lower()
    if not mode.startswith(("sport","competitive")) or "fight" in mode or "stadium" in mode:
        return None
    empty={"known":False,"label":"段位未公开/未定级","icon":None,"score":None,"division":None}
    for info in (player.get("rankInfo"),source.get("rankInfo")):
        if not isinstance(info,dict):
            continue
        name=_name(info.get("rankName")) or _name(info.get("rank_name"))
        try:
            raw_score=info.get("rankScore") or info.get("score") or 0
            score=float(raw_score) if not isinstance(raw_score,bool) else 0
            score=score if math.isfinite(score) and score>0 else None
        except (TypeError,ValueError):
            score=None
        try:
            division=int(info.get("rankSubTier") or info.get("rank_sub_tier") or 0)
            division=division if 1<=division<=5 else None
        except (TypeError,ValueError):
            division=None
        if division is None and re.search(r"[1-5]$",name):
            division=int(name[-1])
        if not name and score is None:
            continue
        normalized=re.sub(r"\s*[1-5]$","",name).lower()
        entry=next((row for row in registry if normalized and normalized in {str(row.get("key","")).lower(),str(row.get("name","")).lower()}),None)
        if entry is None and score is not None and not name:
            for row in registry:
                bounds=row.get("level")
                if isinstance(bounds,list) and len(bounds)==2:
                    try:
                        if float(bounds[0])<=score<=float(bounds[1]):
                            entry=row
                            break
                    except (TypeError,ValueError):
                        continue
        label=str((entry or {}).get("name") or name or "")
        if not label:
            continue
        if division and not label.endswith(str(division)):
            label+=str(division)
        icon=(entry or {}).get("icon") or info.get("rankIcon") or info.get("icon")
        return {"known":True,"label":label,"icon":icon if isinstance(icon,str) and icon.startswith("https://") else None,
                "score":score,"division":division,"recorded_at":info.get("ts")}
    return empty
