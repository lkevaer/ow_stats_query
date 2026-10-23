from __future__ import annotations

from collections import Counter
import math
import statistics
from .rank import match_rank

VERSION = "team-relative-2026s5-v3"
ROLE_LABELS = {"tank": "坦克", "dps": "输出", "support": "辅助"}
WEIGHTS = {
    "tank": {"damage": .30, "final_blows": .27, "survival": .23, "eliminations": .10, "final_blow_share": .08, "mitigation": .02},
    "dps": {"damage": .25, "final_blows": .40, "survival": .10, "eliminations": .10, "final_blow_share": .15},
    "support": {"survival": .40, "healing": .25, "assists": .10, "damage": .15, "final_blows": .04, "eliminations": .03, "final_blow_share": .03},
}
FIELDS = {"damage": "heroDamage", "final_blows": "finalHit", "survival": "death", "eliminations": "kill", "healing": "cure", "assists": "assist", "mitigation": "resistDamage"}
RELATIVE_NOTICE = "上下等马仅在本次查询名单内相对区分；相对评分不代表绝对水平。"
SCOPE_NOTICE = "本页面对于上下等马的评级仅限于账号本身匹配池的近12场游戏数据，不考虑段位高低对于对局水平含金量的影响。因此如果名单段位跨度过大可能会出现低段位的评级比高段位的高的情况，这种情况是正常的。所以最佳适用场景是在自己竞技模式匹配到了之后，通过屏蔽其余队友查看ID的方法，查询队内五人的评级。"
HERO_SCORE_FACTORS = {"207165582859042820": .72, "mercy": .72, "天使": .72}


def role_of(player: dict, heroes: dict) -> str | None:
    hero = heroes.get(str(player.get("heroGuid") or player.get("heroId") or ""), {})
    role = str(hero.get("roleType") or player.get("roleType") or "").lower()
    return {"tank": "tank", "dps": "dps", "damage": "dps", "healer": "support", "support": "support"}.get(role)


def numeric(player: dict, key: str) -> float | None:
    value = player.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError):
        return None


def grade(score: float) -> str:
    for cutoff, label in [(85, "S"), (75, "A"), (60, "B"), (45, "C")]:
        if score >= cutoff:
            return label
    return "D"


def relative_points(value: float, reference: float, *, inverse: bool = False, smoothing: float = 1) -> float:
    ratio = (value + smoothing) / (reference + smoothing)
    if inverse:
        ratio = 1 / ratio
    return max(0, min(100, 60 + 45 * math.tanh(math.log(ratio))))


def _identity_matches(player: dict, *, customer_token: str, bnet_id: str, battletag: str) -> bool:
    return bool((customer_token and str(player.get("customerToken") or "") == customer_token)
                or (bnet_id and str(player.get("bnetId") or "") == bnet_id)
                or str(player.get("name") or player.get("battletag") or "").strip() == battletag)


def stomp_weight(teams: list[list[dict]]) -> tuple[float, str]:
    """Apply the same evidence discount to the winning and losing sides."""
    for team in teams:
        if len(team) < 3:
            continue
        rows = [(numeric(p, "kill"), numeric(p, "assist"), numeric(p, "death")) for p in team]
        if any(k is None or d is None for k, _, d in rows):
            continue
        if all(k == 0 and a == 0 for k, a, _ in rows) and sum(d for _, _, d in rows) >= len(team) * 2:
            return .25, "一方全队0击杀0助攻，碾压局降权"
        if all(k < d for k, _, d in rows) and sum(d for _, _, d in rows) >= len(team) * 2:
            return .45, "一方全队负战绩，碾压局降权"
    if len(teams) == 2 and all(len(t) >= 3 for t in teams):
        totals = []
        for team in teams:
            fields = [[numeric(p, key) for p in team] for key in ("finalHit", "death")]
            if any(v is None for values in fields for v in values):
                return 1., ""
            totals.append(tuple(sum(values) for values in fields))
        for i, (finals, deaths) in enumerate(totals):
            other_finals, other_deaths = totals[1 - i]
            if deaths >= len(teams[i]) * 3 and other_finals >= 10 and finals <= other_finals * .2 and other_deaths <= deaths * .35:
                return .40, "双方击杀与阵亡差距悬殊，碾压局降权"
    return 1., ""


def _asset_url(value) -> str | None:
    return value if isinstance(value, str) and value.startswith("https://") else None


def score_match(data: dict, source: dict, *, customer_token: str, bnet_id: str, battletag: str, heroes: dict, maps: dict | None = None, ranks: list[dict] | None = None) -> dict:
    own, team = None, []
    teams = [[p for p in data.get(key, []) if isinstance(p, dict)] for key in ("teammateList", "enemyList")]
    for players in teams:
        matching = [p for p in players if _identity_matches(p, customer_token=customer_token, bnet_id=bnet_id, battletag=battletag)]
        if len(matching) > 1 or (matching and own is not None):
            raise ValueError("对局详情中玩家身份不唯一，未计分")
        if matching:
            own, team = matching[0], players
    if own is None:
        raise ValueError("对局详情没有找到该玩家，未计分")
    role = role_of(own, heroes)
    if role is None:
        raise ValueError("该英雄位置未知，未使用猜测的位置计分")
    seconds = numeric(data, "gameTimeSec")
    if not seconds or seconds < 30:
        raise ValueError("对局时长缺失或不足 30 秒，未计分")
    others = [p for p in team if p is not own]
    if len(others) < 2:
        raise ValueError("自家队友数据不足，无法进行队内比较")
    warnings, adjustments, components = [], [], {}
    same_role = [p for p in others if role_of(p, heroes) == role]
    offensive = [p for p in others if role_of(p, heroes) in {"tank", "dps"}]
    valid_finals = [numeric(p, "finalHit") for p in team]
    total_finals = sum(x or 0 for x in valid_finals)
    final_blows = numeric(own, "finalHit")
    share = final_blows / total_finals if final_blows is not None and all(x is not None for x in valid_finals) and total_finals > 0 else None
    fleta = 2 * share if share is not None else None
    role_weights = {"tank": 1., "dps": 1.2, "support": .45}
    expected_share = role_weights[role] / sum(role_weights.get(role_of(p, heroes), 1.) for p in team)
    for metric, weight in WEIGHTS[role].items():
        if metric == "final_blow_share":
            if share is None:
                warnings.append("全队最后一击缺失或总数为零，占比与Fleta不计")
                continue
            components[metric] = {"value": round(share, 6), "reference": round(expected_share, 6),
                                  "points": round(relative_points(share, expected_share, smoothing=.02), 3),
                                  "weight": weight, "comparison": "个人最后一击占自家全队总数的比例"}
            continue
        key, value = FIELDS[metric], numeric(own, FIELDS[metric])
        if metric in {"damage", "final_blows", "mitigation", "healing"}:
            peers = same_role or (offensive if role != "support" else others)
            comparison = "自家同位置队友" if same_role else "自家非辅助队友" if role != "support" else "自家其他队友"
        else:
            peers, comparison = others, "自家其他队友"
        values = [v for p in peers if (v := numeric(p, key)) is not None]
        if value is None or not values:
            warnings.append(f"{key} 数据缺失，该项未计分")
            continue
        reference = statistics.mean(values)
        rate, reference_rate = value * 600 / seconds, reference * 600 / seconds
        points = relative_points(rate, reference_rate, inverse=metric == "survival", smoothing=300 if metric in {"damage", "healing"} else 1)
        components[metric] = {"value": round(value, 3), "per10": round(rate, 3), "reference": round(reference, 3),
                              "reference_per10": round(reference_rate, 3), "points": round(points, 3), "weight": weight, "comparison": comparison}
    available_weight = sum(c["weight"] for c in components.values())
    required = {"survival", "healing", "damage"} if role == "support" else {"damage", "final_blows", "survival"}
    if not required.issubset(components) or available_weight < .70:
        raise ValueError("关键统计缺失，不能可靠评分")
    base = sum(c["points"] * c["weight"] for c in components.values()) / available_weight
    for component in components.values():
        component["contribution"] = round((component["points"] - 60) * component["weight"] / available_weight, 3)

    def adjust(points, reason):
        adjustments.append({"points": round(points, 3), "reason": reason})

    if role == "tank" and share is not None and share >= .30:
        adjust(min(10, 2 + (share - .30) * 40), "T位最后一击占比≥30%")
    if role == "dps" and share is not None and share >= .40:
        adjust(min(10, 4 + (share - .40) * 30), "C位最后一击占比≥40%")
    if role == "support":
        death = numeric(own, "death")
        other_deaths = [numeric(p, "death") for p in others]
        high_death = False
        if all(x is not None for x in other_deaths):
            median = statistics.median(other_deaths)
            high_death = death > median
            if high_death:
                penalty = min(25, (death - median) * 600 / seconds * 2.4 + 7 * (death - median) / max(median, 1))
                if death >= max(other_deaths) and death > min(other_deaths):
                    penalty += 10 if death > max(other_deaths) else 5
                adjust(-penalty, "辅助高阵亡" + ("，全队最高" if death >= max(other_deaths) else ""))
            elif death <= min(other_deaths) and death < max(other_deaths):
                adjust(4, "辅助低阵亡")
        if share is not None and share > 0 and (not high_death or share >= .30):
            adjust(min(42, 2 * share + 90 * max(0, share - .15) ** 2), "辅助最后一击占比递增加分")
    if share is not None and share >= .50:
        adjust(min(28, 18 + (share - .50) * 40), "最后一击占比≥50%，高权重加分")

    hero_guid = str(own.get("heroGuid") or own.get("heroId") or "")
    hero = heroes.get(hero_guid) or {}
    factor = HERO_SCORE_FACTORS.get(hero_guid, HERO_SCORE_FACTORS.get(str(hero.get("name", "")).lower(), 1.))
    score = round(max(0, min(100, (base + sum(a["points"] for a in adjustments)) * factor)), 2)
    map_guid = str(data.get("mapGuid") or source.get("mapGuid") or "")
    map_info = (maps or {}).get(map_guid, {})
    stomp, stomp_reason = stomp_weight(teams)
    duration_weight = min(1., seconds / 180)
    outcome = data.get("matchRet", source.get("matchRet"))
    return {
        "match_id": str(source.get("matchId") or ""), "begin_ts": source.get("beginTs") or data.get("startTime"),
        "game_mode": source.get("gameMode"), "seconds": seconds, "hero": hero.get("name") or hero_guid,
        "hero_guid": hero_guid, "hero_icon": _asset_url(hero.get("icon") or hero.get("smallIconUrl") or own.get("heroIcon")),
        "map_guid": map_guid, "map_name": map_info.get("name") or data.get("mapName") or source.get("mapName") or map_guid or "未知地图",
        "map_icon": _asset_url(map_info.get("icon")), "role": role, "role_label": ROLE_LABELS[role],
        "score": score, "grade": grade(score), "base_score": round(base * factor, 2),
        "final_blow_share": round(share, 6) if share is not None else None,
        "fleta": round(fleta, 6) if fleta is not None else None,
        "team_final_blows": total_finals if share is not None else None, "team_size": len(team),
        "outcome": outcome, "outcome_label": "胜" if outcome == 1 else "平" if outcome == 0 else "负" if outcome is not None else "未知",
        "rank": match_rank(own,source,ranks or []),
        "metrics": components, "adjustments": adjustments, "warnings": warnings,
        "stats": {key: numeric(own, key) for key in FIELDS.values()},
        "data_coverage": round(available_weight, 3), "sample_weight": round(duration_weight * stomp, 4),
        "duration_weight": round(duration_weight, 4), "is_stomp": stomp < 1, "stomp_weight": stomp, "stomp_reason": stomp_reason,
    }


def aggregate_player(battletag: str, matches: list[dict], *, fetched: int, errors: list[dict]) -> dict:
    base = {"battletag": battletag, "requested_matches": 12, "fetched_matches": fetched, "rated_matches": len(matches), "matches": matches, "errors": errors}
    if not matches:
        return {**base, "status": "unrated", "score": None, "grade": None, "tier": "未评级", "confidence": 0, "rank": None}
    weight = sum(m["sample_weight"] for m in matches)
    score = round(sum(m["score"] * m["sample_weight"] for m in matches) / weight, 2)
    roles = Counter(m["role"] for m in matches)
    averages = {}
    for metric in FIELDS:
        valid = [m for m in matches if metric in m["metrics"]]
        if valid:
            averages[metric + "_per10"] = round(sum(m["metrics"][metric]["value"] for m in valid) * 600 / sum(m["seconds"] for m in valid), 2)
    final_matches = [m for m in matches if m["fleta"] is not None]
    finals_total = sum(m["team_final_blows"] for m in final_matches)
    fleta = sum(m["fleta"] * m["team_final_blows"] for m in final_matches) / finals_total if finals_total else None
    dominant_count = sum(m.get("final_blow_share") is not None and m["final_blow_share"] >= .50 for m in matches)
    smurf = dominant_count >= 5
    outcomes = Counter(m["outcome_label"] for m in matches)
    known_outcomes = sum(outcomes[k] for k in ("胜", "负", "平"))
    ranked_matches=sorted((m for m in matches if (m.get("rank") or {}).get("known")),key=lambda m:float(m.get("begin_ts") or 0),reverse=True)
    latest_rank={**ranked_matches[0]["rank"],"role":ranked_matches[0]["role"],"role_label":ranked_matches[0]["role_label"],"match_begin_ts":ranked_matches[0]["begin_ts"]} if ranked_matches else None
    return {
        **base, "status": "complete" if len(matches) == 12 else "partial", "score": score,
        "grade": "SMURF" if smurf else grade(score), "score_grade": grade(score), "is_smurf": smurf,
        "latest_rank":latest_rank,
        "smurf_matches": dominant_count, "smurf_threshold": 5,
        "confidence": round(min(1, len(matches) / 12) * statistics.mean(m["data_coverage"] for m in matches), 3),
        "roles": dict(roles), "primary_role": max(roles, key=roles.get), "grade_counts": dict(Counter(m["grade"] for m in matches)),
        "averages": averages, "fleta": round(fleta, 6) if fleta is not None else None,
        "final_blow_share": round(fleta / 2, 6) if fleta is not None else None,
        "wins": outcomes["胜"], "losses": outcomes["负"], "draws": outcomes["平"],
        "win_rate": round(outcomes["胜"] / known_outcomes, 6) if known_outcomes else None,
        "win_rate_samples": known_outcomes, "stomp_matches": sum(m.get("is_stomp", False) for m in matches),
        "effective_matches": round(weight, 2), "minutes": round(sum(m["seconds"] for m in matches) / 60, 2),
        "sample_note": "近12场；碾压局与短局按权重汇总" if len(matches) == 12 else f"仅有{len(matches)}场可评分，结果暂定；未用其他模式补齐",
    }


def rank_players(players: list[dict]) -> list[dict]:
    rated = sorted((p for p in players if p["score"] is not None), key=lambda p: (-p["score"], p["battletag"]))
    n, outer = len(rated), max(1, len(rated) // 3)
    all_tied = n > 0 and rated[0]["score"] == rated[-1]["score"]
    previous_score, previous_tier, previous_rank = None, None, None
    for index, player in enumerate(rated):
        tier = "中等马" if n == 1 or all_tied else "上等马" if index < outer else "下等马" if index >= n - outer else "中等马"
        rank = index + 1
        if previous_score is not None and player["score"] == previous_score:
            tier, rank = previous_tier, previous_rank
        player.update(tier=tier, rank=rank)
        previous_score, previous_tier, previous_rank = player["score"], tier, rank
    return rated + [p for p in players if p["score"] is None]


def grouped_summary(players: list[dict]) -> str:
    lines = []
    for tier in ["上等马", "中等马", "下等马"]:
        members = []
        for player in players:
            if player.get("tier") != tier or player.get("score") is None:
                continue
            name = player["battletag"].rsplit("#", 1)[0]
            fleta = "—" if player.get("fleta") is None else f"{player['fleta'] * 100:.0f}%"
            badge = "·SMURF" if player.get("is_smurf") else ""
            sample = f"·{player['rated_matches']}场" if player["rated_matches"] < 12 else ""
            members.append(f"{name}({player['score']:.1f}/F{fleta}{badge}{sample})")
        if members:
            lines.append("我方" + tier + "：" + "、".join(members))
    missing = [p["battletag"].rsplit("#", 1)[0] for p in players if p.get("score") is None]
    if missing:
        lines.append("未评级：" + "、".join(missing))
    return "\n".join(lines)


def scoring_rules() -> dict:
    return {
        "version": VERSION, "weights": WEIGHTS,
        "fleta_definition": "个人最后一击 /（自家全队最后一击总数 × 50%）；Fleta=最后一击占比×2，不截断；零分母不计",
        "final_blow_share_thresholds": {"tank": .30, "dps": .40, "major_bonus": .50},
        "smurf": "最近12场中至少5场个人最后一击占比≥50%（Fleta≥100%）；不足12场但已有5场达标也可确认",
        "aggregation": "按对局权重平均；短于3分钟按时长降权，单方全队0-0-x或负战绩及明显碾压局双向降权",
        "win_rate": "按已知胜负平计算，平局进入分母；仅供参考，不直接加减个人评分",
        "relative_notice": RELATIVE_NOTICE,
        "disclaimer": "自定义队内表现评分，不是网易官方分数，不代表段位；同分同档，无数据不评级",
    }
