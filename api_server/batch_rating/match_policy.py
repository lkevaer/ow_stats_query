"""Conservative single-game rewards and complete-game participation checks."""
from __future__ import annotations

import math
import statistics

from .context import numeric

CONTRIBUTION_NOTICE = "数据不能反映出全部贡献，单局评分高低只是基于队友区分，与敌方无关。"
EMPTY_QUERY_NOTICE = "暂未获得可用战绩，可能是网易大神搜索接口维护中或服务器异常，建议部署到本地查询。"


def completed_scoreboard(data: dict, own: dict, team: list[dict], opponents: list[dict], seconds: float) -> bool:
    """Require an ended, non-short game and complete recorded teams/statistics.

    Objective time is not participation time. If explicit early-leave or played
    time fields exist, respect them; never infer damage=0 from a missing field.
    """
    if seconds < 390 or len(team) not in {5, 6} or len(opponents) != len(team):
        return False
    result = data.get("matchRet")
    if isinstance(result, bool) or str(result) not in {"-1", "0", "1", "2"}:
        return False
    identities = []
    for row in [*team, *opponents]:
        identity = str(row.get("bnetId") or row.get("customerToken") or row.get("name") or row.get("battletag") or "")
        if not identity or any(numeric(row, key) is None for key in ("heroDamage", "death", "kill", "assist", "finalHit", "cure")):
            return False
        identities.append(identity)
    if len(set(identities)) != len(identities):
        return False
    for record in (data, own):
        for key in ("completed", "isComplete", "isCompleted", "isFinished", "isEnd"):
            if key in record and str(record[key]).lower() in {"false", "0"}:
                return False
        for key in ("isLeaver", "isLeave", "isQuit", "leftEarly", "earlyLeave", "isDisconnected"):
            if str(record.get(key, "")).lower() in {"true", "1", "yes"}:
                return False
        for key in ("gameStatus", "matchStatus", "status"):
            if str(record.get(key, "")).lower() in {"running", "in_progress", "aborted", "cancelled", "unfinished"}:
                return False
    for key in ("playedTimeSec", "playTimeSec", "playedSeconds", "timePlayedSec", "participationTimeSec"):
        if key in own:
            played = numeric(own, key)
            if played is None or played < seconds * .9:
                return False
    return True


def zero_death_bonus(own: dict, others: list[dict], seconds: float) -> float:
    if numeric(own, "death") != 0:
        return 0.
    activity = ("kill", "assist", "heroDamage", "cure", "resistDamage", "finalHit")
    if not any((numeric(own, field) or 0) > 0 for field in activity):
        return 0.
    deaths = [numeric(row, "death") for row in others]
    average_rate = statistics.mean(deaths) * 600 / seconds if deaths and all(value is not None for value in deaths) else 0.
    return (6. + min(12., max(0., average_rate - 1.) * 1.5)) * min(1., seconds / 390)


def compress_positive_adjustments(adjustments: list[dict], outcome: int | None) -> float:
    """Keep individual bonuses monotone while preventing additive score saturation."""
    total = sum(max(0., item["points"]) for item in adjustments)
    if total == 0:
        return 1.
    budget = 12. if outcome == -1 else 20.
    scale = budget * math.tanh(total / budget) / total
    for item in adjustments:
        if item["points"] > 0:
            item["points"] = round(item["points"] * scale, 6)
    return scale
