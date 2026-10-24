from __future__ import annotations

from collections import Counter
import math
import statistics
from .rank import match_rank
from .ocr_candidates import normalize_tags
from .context import death_context, kd_stats, match_weights, numeric, party_info, support_survival, survival_points

VERSION = "team-relative-2026s5-v7"
ROLE_LABELS = {"tank": "坦克", "dps": "输出", "support": "辅助"}
WEIGHTS = {
    "tank": {"damage": .30, "final_blows": .27, "survival": .23, "eliminations": .10, "final_blow_share": .08, "mitigation": .02},
    "dps": {"damage": .25, "final_blows": .40, "survival": .10, "eliminations": .10, "final_blow_share": .15},
    "support": {"survival": .40, "healing": .25, "assists": .10, "damage": .15, "final_blows": .04, "eliminations": .03, "final_blow_share": .03},
}
FIELDS = {"damage": "heroDamage", "final_blows": "finalHit", "survival": "death", "eliminations": "kill", "healing": "cure", "assists": "assist", "mitigation": "resistDamage"}
RELATIVE_NOTICE = "上下等马仅在本次查询名单内相对区分；相对评分不代表绝对水平。名单段位差距过大时，上下等马评级没有比较意义。"
SCOPE_NOTICE = "本页面对于上下等马的评级仅限于账号本身匹配池的近12场游戏数据，不考虑段位高低对于对局水平含金量的影响。因此如果名单段位跨度过大可能会出现低段位的评级比高段位的高的情况，这种情况是正常的。所以最佳适用场景是在自己竞技模式匹配到了之后，通过屏蔽其余队友查看ID的方法，查询队内五人的评级。"
HERO_SCORE_FACTORS = {"207165582859042820": .72, "mercy": .72, "天使": .72}


def role_of(player: dict, heroes: dict) -> str | None:
    hero = heroes.get(str(player.get("heroGuid") or player.get("heroId") or ""), {})
    role = str(hero.get("roleType") or player.get("roleType") or "").lower()
    return {"tank": "tank", "dps": "dps", "damage": "dps", "healer": "support", "support": "support"}.get(role)


def grade(score: float) -> str:
    for cutoff, label in [(100, "SSS"), (90, "S"), (80, "A"), (70, "B"), (60, "C")]:
        if score >= cutoff:
            return label
    return "D"


def calibrate_score(score: float) -> float:
    """Lift the middle range (70 -> 80) while retaining both endpoints and ordering."""
    return 100 * (max(0., min(100., score)) / 100) ** (math.log(.8) / math.log(.7))


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


def _outcome(data: dict, source: dict, side: int) -> int | None:
    raw = data.get("matchRet", source.get("matchRet"))
    if isinstance(raw, bool) or str(raw) not in {"-1", "0", "1", "2"}:
        return None
    outcome = 1 if int(raw) == 1 else 0 if int(raw) == 0 else -1
    return -outcome if side else outcome


def _score_player(data: dict, source: dict, *, own: dict, team: list[dict], opponents: list[dict], side: int,
                  heroes: dict, maps: dict, ranks: list[dict], stomp: float, stomp_reason: str) -> dict:
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
    context = death_context(team, opponents, seconds, is_stomp=stomp < 1)
    if context["low_team_deaths"]:
        warnings.append("全队阵亡较低，已降低阵亡扣分敏感度")
    same_role = [p for p in others if role_of(p, heroes) == role]
    offensive = [p for p in others if role_of(p, heroes) in {"tank", "dps"}]
    valid_finals = [numeric(p, "finalHit") for p in team]
    total_finals = sum(x or 0 for x in valid_finals)
    final_blows = numeric(own, "finalHit")
    share = final_blows / total_finals if final_blows is not None and all(x is not None for x in valid_finals) and total_finals > 0 else None
    fleta = 2 * share if share is not None else None
    support_exempt = role == "support" and fleta is not None and fleta >= .50
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
        if metric == "survival":
            points = survival_points(points, value, seconds, role, context)
            if support_exempt:
                points = max(60., points)
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
        penalty, high_death, waived = support_survival(death, others, seconds, context)
        if support_exempt:
            warnings.append("辅助Fleta≥50%，本场不触发高阵亡惩罚")
        elif penalty > 0:
            adjust(-penalty, "辅助阵亡偏多" if not context["low_team_deaths"] else "辅助阵亡略多，低阵亡局减轻扣分")
        if waived and not support_exempt:
            warnings.append("对手阵亡明显更多，仅多阵亡一次，免除辅助最高阵亡额外扣分")
        if not high_death and all(x is not None for x in other_deaths) and death <= min(other_deaths) and death < max(other_deaths):
            adjust(4, "辅助低阵亡")
        if share is not None and share > 0 and (not high_death or share >= .20):
            curve = min(42, 2 * share + 90 * max(0, share - .15) ** 2)
            if fleta >= .50:
                adjust(min(42., 14. + curve), "辅助Fleta≥50%，高权重进攻加分")
            elif fleta >= .40:
                adjust(min(42., 8. + curve), "辅助Fleta≥40%，较高权重进攻加分")
            else:
                adjust(curve, "辅助最后一击占比递增加分")
    if share is not None and share >= .50:
        adjust(min(28, 18 + (share - .50) * 40), "最后一击占比≥50%，高权重加分")

    outcome = _outcome(data, source, side)
    kills, death = numeric(own, "kill"), numeric(own, "death")
    solo_positive = bool(kills is not None and kills > death and all(
        numeric(p, "kill") is not None and numeric(p, "death") is not None
        and numeric(p, "kill") < numeric(p, "death") for p in others))
    loss_protected = bool(outcome == -1 and fleta is not None and fleta >= .80)
    if loss_protected:
        deductions = max(0., -components["survival"]["contribution"]) + sum(-a["points"] for a in adjustments if a["points"] < 0)
        relief = min(deductions, 16 if solo_positive else 10, deductions * (.45 if solo_positive else .25) + (6 if solo_positive else 2))
        if relief > 0:
            adjust(relief, "败方高Fleta且队内唯一正战绩，减轻生存扣分" if solo_positive else "败方高Fleta，减轻生存扣分")

    hero_guid = str(own.get("heroGuid") or own.get("heroId") or "")
    hero = heroes.get(hero_guid) or {}
    factor = HERO_SCORE_FACTORS.get(hero_guid, HERO_SCORE_FACTORS.get(str(hero.get("name", "")).lower(), 1.))
    score = round(calibrate_score(base + sum(a["points"] for a in adjustments)) * factor, 2)
    # Keep displayed contributions in the same calibrated score units.
    component_scale = ((calibrate_score(base) - calibrate_score(60)) / (base - 60)
                       if abs(base - 60) > 1e-6 else (calibrate_score(60.01) - calibrate_score(59.99)) / .02)
    for component in components.values():
        component["contribution"] = round(component["contribution"] * component_scale * factor, 3)
    running = base
    for adjustment in adjustments:
        after = running + adjustment["points"]
        adjustment["points"] = round((calibrate_score(after) - calibrate_score(running)) * factor, 3)
        running = after
    map_guid = str(data.get("mapGuid") or source.get("mapGuid") or "")
    map_info = (maps or {}).get(map_guid, {})
    weights = match_weights(team, opponents, seconds, stomp)
    return {
        "match_id": str(source.get("matchId") or ""), "begin_ts": source.get("beginTs") or data.get("startTime"),
        "game_mode": source.get("gameMode"), "seconds": seconds, "hero": hero.get("name") or hero_guid,
        "hero_guid": hero_guid, "hero_icon": _asset_url(hero.get("icon") or hero.get("smallIconUrl") or own.get("heroIcon")),
        "map_guid": map_guid, "map_name": map_info.get("name") or data.get("mapName") or source.get("mapName") or map_guid or "未知地图",
        "map_icon": _asset_url(map_info.get("icon")), "role": role, "role_label": ROLE_LABELS[role],
        "score": score, "grade": grade(score), "base_score": round(calibrate_score(base) * factor, 2),
        "final_blow_share": round(share, 6) if share is not None else None,
        "fleta": round(fleta, 6) if fleta is not None else None,
        "team_final_blows": total_finals if share is not None else None, "team_size": len(team),
        "outcome": outcome, "outcome_label": "胜" if outcome == 1 else "平" if outcome == 0 else "负" if outcome is not None else "未知",
        "rank": match_rank(own,source,ranks or []),
        "party": party_info(own, team), "death_context": context, "support_death_exempt": support_exempt,
        **kd_stats(kills, death), "loss_protected": loss_protected, "solo_positive": solo_positive,
        "is_carry": fleta is not None and fleta >= .80,
        "is_svp": outcome == -1 and fleta is not None and fleta > .80,
        "metrics": components, "adjustments": adjustments, "warnings": warnings,
        "stats": {key: numeric(own, key) for key in FIELDS.values()},
        "data_coverage": round(available_weight, 3), **weights,
        "is_stomp": stomp < 1, "stomp_weight": stomp, "stomp_reason": stomp_reason,
    }


def public_battletag(player: dict, name_map: dict) -> str:
    """Return a validated public tag without exposing internal identifiers."""
    candidates = [player.get(key) for key in ("battletag", "battleTag", "name", "bnetId")]
    mapped = name_map.get(str(player.get("bnetId") or "")) if isinstance(name_map, dict) else None
    if isinstance(mapped, str):
        candidates.append(mapped)
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        try:
            return normalize_tags([candidate])[0]
        except ValueError:
            continue
    return ""


def match_roster(data: dict) -> dict:
    """Expose only complete public BattleTags; never substitute internal tokens."""
    teams = [data.get("teammateList") or [], data.get("enemyList") or []]
    tags = []
    name_map = data.get("nameMap") or {}
    for team in teams:
        for player in team:
            if not isinstance(player, dict):
                continue
            tag = public_battletag(player, name_map)
            if tag:
                tags.append(tag)
    try:
        tags = normalize_tags(tags, max_players=12)
    except ValueError:
        tags = []
    complete = len(teams[0]) in (5, 6) and len(teams[0]) == len(teams[1]) and len(tags) == sum(map(len, teams))
    return {"participants": tags, "roster_complete": complete}


def score_match(data: dict, source: dict, *, customer_token: str, bnet_id: str, battletag: str,
                heroes: dict, maps: dict | None = None, ranks: list[dict] | None = None) -> dict:
    own, side = None, 0
    teams = [[p for p in (data.get(key) or []) if isinstance(p, dict)] for key in ("teammateList", "enemyList")]
    for index, players in enumerate(teams):
        matching = [p for p in players if _identity_matches(p, customer_token=customer_token, bnet_id=bnet_id, battletag=battletag)]
        if len(matching) > 1 or (matching and own is not None):
            raise ValueError("对局详情中玩家身份不唯一，未计分")
        if matching:
            own, side = matching[0], index
    if own is None:
        raise ValueError("对局详情没有找到该玩家，未计分")
    stomp, reason = stomp_weight(teams)

    def calculate(player, index):
        # The list snapshot belongs to the queried player, not every participant.
        snapshot = source if player is own else {k: v for k, v in source.items() if k != "rankInfo"}
        return _score_player(data, snapshot, own=player, team=teams[index], opponents=teams[1-index], side=index,
                             heroes=heroes, maps=maps or {}, ranks=ranks or [], stomp=stomp, stomp_reason=reason)

    result = calculate(own, side)
    other_players = []
    name_map = data.get("nameMap") or {}
    for index, team in enumerate(teams):
        for player in team:
            if player is own:
                continue
            tag = public_battletag(player, name_map)
            display_name = tag or str(player.get("name") or "未公开ID").split("#", 1)[0][:64]
            try:
                candidate = calculate(player, index)
            except ValueError as error:
                hero = heroes.get(str(player.get("heroGuid") or player.get("heroId") or ""), {})
                candidate = {"score": None, "grade": None, "hero": hero.get("name") or "未知英雄",
                             "role": role_of(player, heroes), "hero_icon": _asset_url(hero.get("icon")),
                             "stats": {key: numeric(player, key) for key in FIELDS.values()},
                             "metrics": {}, "adjustments": [], "warnings": [str(error)],
                             "seconds": result["seconds"], "data_coverage": 0}
            candidate.update(battletag=display_name, team="ally" if index == side else "opponent")
            other_players.append(candidate)
    rated = [result] + [player for player in other_players if player["score"] is not None]
    scores = [player["score"] for player in rated]
    complete = all(len(team) == 5 for team in teams)
    identities = [str(p.get("bnetId") or p.get("customerToken") or p.get("name") or p.get("battletag") or "") for team in teams for p in team]
    complete = complete and all(identities) and len(set(identities)) == 10 and len(rated) == 10 and all(player["data_coverage"] >= .999 for player in rated)
    highest = max(scores)
    for player in [result, *other_players]:
        player.update(is_mvp=complete and player["score"] == highest,
                      mvp_tied=complete and player["score"] == highest and scores.count(highest) > 1,
                      mvp_compared_players=len(scores) if all(len(t) == 5 for t in teams) else 0,
                      mvp_comparison_complete=complete,
                      mvp_note="按当局完整十人自定义评分比较，同分可并列" if complete else "十人可评分数据不完整，未判定MVP")
    result["match_details"] = {"other_players": other_players, "total_players": sum(map(len, teams)),
                               "rated_players": len(rated), "version": 1}
    result.update(match_roster(data))
    return result


def aggregate_player(battletag: str, matches: list[dict], *, fetched: int, errors: list[dict]) -> dict:
    matches = sorted(matches, key=lambda m: float(m.get("begin_ts") or 0), reverse=True)[:12]
    base = {"battletag": battletag, "requested_matches": 12, "fetched_matches": fetched, "rated_matches": len(matches), "matches": matches, "errors": errors}
    if not matches:
        return {**base, "status": "unrated", "score": None, "grade": None, "tier": "未评级", "confidence": 0, "rank": None}
    weight = sum(m["sample_weight"] for m in matches)
    match_average = sum(m["score"] * m["sample_weight"] for m in matches) / weight
    roles = Counter(m["role"] for m in matches)
    averages = {}
    for metric in FIELDS:
        valid = [m for m in matches if metric in m["metrics"]]
        if valid:
            averages[metric + "_per10"] = round(sum(m["metrics"][metric]["value"] for m in valid) * 600 / sum(m["seconds"] for m in valid), 2)
    final_matches = [m for m in matches if m["fleta"] is not None]
    finals_total = sum(m["team_final_blows"] for m in final_matches)
    fleta = sum(m["fleta"] * m["team_final_blows"] for m in final_matches) / finals_total if finals_total else None
    dominant_count = sum(m.get("fleta") is not None and m["fleta"] > .65 for m in matches)
    smurf = dominant_count >= 7
    outcomes = Counter(m["outcome_label"] for m in matches)
    known_outcomes = sum(outcomes[k] for k in ("胜", "负", "平"))
    win_rate = outcomes["胜"] / known_outcomes if known_outcomes else None
    hot_streak = len(matches) == 12 and outcomes["胜"] >= 10
    summary_adjustments = []
    if smurf:
        summary_adjustments.append({"points": 18., "reason": "近12场至少7场Fleta>65%，SMURF最高权重专项加分"})
    if win_rate is not None:
        win_points = (win_rate - .5) * 3 * min(1., known_outcomes / 12)
        if hot_streak:
            win_points = max(win_points, 4. + outcomes["胜"] - 10)
        if win_points < 0 and outcomes["负"]:
            protected = sum(m["outcome_label"] == "负" and m.get("loss_protected", False) for m in matches)
            win_points *= 1 - .8 * protected / outcomes["负"]
        if win_points:
            summary_adjustments.append({"points": round(win_points, 3), "reason": "近12场至少10胜，手感火热，高权重胜率加分" if hot_streak else "近12场胜率参考，小幅修正"})
    kd_matches = [m for m in matches if numeric(m.get("stats", {}), "kill") is not None and numeric(m.get("stats", {}), "death") is not None]
    kills = sum(numeric(m["stats"], "kill") for m in kd_matches)
    deaths = sum(numeric(m["stats"], "death") for m in kd_matches)
    kd = kd_stats(kills, deaths) if kd_matches else kd_stats(None, None)
    high_kd_count = sum((k := numeric(m["stats"], "kill")) > 0 and k >= 7 * numeric(m["stats"], "death") for m in kd_matches)
    consistent_kd = high_kd_count >= 9
    if consistent_kd:
        summary_adjustments.append({"points": round(8. + (high_kd_count - 9) / 3, 3), "reason": "近12场至少9场KD≥7，持续高KD加分"})
    if kd_matches and kills >= 10 and (kd["kd_perfect"] or kd["kd"] >= 10):
        kd_points = 1.5 if kd["kd_perfect"] else min(1.5, .75 + math.log2(kd["kd"] / 10) * .25)
        summary_adjustments.append({"points": round(kd_points * len(kd_matches) / 12, 3), "reason": "KD≥10，小幅加分"})
    score = round(max(0, min(100, match_average + sum(a["points"] for a in summary_adjustments))), 2)
    ranked_matches=sorted((m for m in matches if (m.get("rank") or {}).get("known")),key=lambda m:float(m.get("begin_ts") or 0),reverse=True)
    latest_rank={**ranked_matches[0]["rank"],"role":ranked_matches[0]["role"],"role_label":ranked_matches[0]["role_label"],"match_begin_ts":ranked_matches[0]["begin_ts"]} if ranked_matches else None
    return {
        **base, "status": "complete" if len(matches) == 12 else "partial", "score": score,
        "grade": "SMURF" if smurf else grade(score), "score_grade": grade(score), "is_smurf": smurf,
        "latest_rank":latest_rank,
        "match_average": round(match_average, 2), "summary_adjustments": summary_adjustments, **kd,
        "hot_streak": hot_streak, "hot_streak_threshold": 10,
        "high_kd_matches": high_kd_count, "high_kd_threshold": 9, "consistent_high_kd": consistent_kd,
        "kd_samples": len(kd_matches), "total_kills": kills if kd_matches else None, "total_deaths": deaths if kd_matches else None,
        "smurf_matches": dominant_count, "smurf_threshold": 7, "smurf_fleta_threshold": .65, "smurf_fleta_operator": ">",
        "mvp_matches": sum(m.get("is_mvp", False) for m in matches),
        "carry_matches": sum(m.get("is_carry", False) for m in matches),
        "svp_matches": sum(m.get("is_svp", False) for m in matches),
        "party_counts": dict(Counter(m["party"]["label"] for m in matches if (m.get("party") or {}).get("known"))),
        "confidence": round(min(1, len(matches) / 12) * statistics.mean(m["data_coverage"] for m in matches), 3),
        "roles": dict(roles), "primary_role": max(roles, key=roles.get), "grade_counts": dict(Counter(m["grade"] for m in matches)),
        "averages": averages, "fleta": round(fleta, 6) if fleta is not None else None,
        "final_blow_share": round(fleta / 2, 6) if fleta is not None else None,
        "wins": outcomes["胜"], "losses": outcomes["负"], "draws": outcomes["平"],
        "win_rate": round(win_rate, 6) if win_rate is not None else None,
        "win_rate_samples": known_outcomes, "stomp_matches": sum(m.get("is_stomp", False) for m in matches),
        "short_matches": sum(m.get("short_match", False) for m in matches),
        "long_matches": sum(m.get("long_match", False) for m in matches),
        "balanced_matches": sum(m.get("balanced_final_blows", False) for m in matches),
        "effective_matches": round(weight, 2), "minutes": round(sum(m["seconds"] for m in matches) / 60, 2),
        "sample_note": "近12场；按时长、双方最后一击与碾压情况汇总，另参考持续高Fleta、高胜率与高KD" if len(matches) == 12 else f"仅有{len(matches)}场可评分，结果暂定；未用其他模式补齐",
    }


def rank_players(players: list[dict]) -> list[dict]:
    rated = sorted((p for p in players if p["score"] is not None), key=lambda p: (-p["score"], p["battletag"]))
    n, outer = len(rated), max(1, len(rated) // 3)
    if n < 2:
        for player in rated:
            player.update(tier=None, rank=None)
        return rated + [p for p in players if p["score"] is None]
    all_tied = n > 0 and rated[0]["score"] == rated[-1]["score"]
    previous_score, previous_tier, previous_rank = None, None, None
    for index, player in enumerate(rated):
        tier = "中等马" if all_tied else "上等马" if index < outer else "下等马" if index >= n - outer else "中等马"
        rank = index + 1
        if previous_score is not None and player["score"] == previous_score:
            tier, rank = previous_tier, previous_rank
        player.update(tier=tier, rank=rank)
        previous_score, previous_tier, previous_rank = player["score"], tier, rank
    return rated + [p for p in players if p["score"] is None]


def grouped_summary(players: list[dict]) -> str:
    if sum(p.get("score") is not None for p in players) < 2:
        return "请输入更多ID以比较（至少两位有有效评分的玩家）。"
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
        "version": VERSION,
        "fleta_definition": "个人最后一击 /（自家全队最后一击总数 × 50%）；Fleta=最后一击占比×2，不截断；零分母不计",
        "reference": [
            "评分偏重最后一击与Fleta，T和C更容易获得高分；不按段位高低修正对局含金量。",
            "T兼顾伤害、生存和最后一击，占比达30%额外加分；C侧重伤害与最后一击，占比达40%额外加分；全位置占比达50%高权重加分。",
            "辅助兼顾生存、治疗、伤害与助攻；Fleta达到40%给予较高权重进攻加分，达到50%给予高权重加分且不触发高阵亡惩罚，使用Fleta本身而非最后一击占比判断。",
            "阵亡按时长、队内差距与双方整体阵亡量综合考量；全队低阵亡时所有位置降低扣分敏感度，辅助仅为全队最高不自动重扣。",
            "辅助Fleta未达到50%时，非碾压局中对手阵亡明显更多、辅助仅多阵亡一次免除最高阵亡额外扣分；全队高阵亡不能自动豁免辅助生存要求。",
            "败方高Fleta减轻生存扣分；队友全为负战绩而本人正战绩时进一步照顾，但不无限抵消高阵亡。",
            "最近12场至少7场Fleta严格大于65%标记SMURF，等于65%不计入，给予综合评分中最高权重的专项加分；不足12场但已有7场达标也可确认，其余仍标记样本不足。",
            "KD使用击杀/阵亡，综合KD达到10可小幅加分；近12场至少9场KD达到7时给予较高权重加分。有击杀且零阵亡记为∞，0击杀0阵亡不算高KD。",
            "一般胜率仅小幅参考，平局进入分母；完整近12场至少10胜时标记手感火热，并提高胜率加分权重。样本不足不推测完整12场胜率。",
            "当局完整十人评分最高者标记MVP，并列最高可同时获得；Fleta达到80%标记Carry，败方Fleta超过80%同时标记SVP；标记本身不额外加分。",
            "组队标记来自当局组队成员记录；缺失或异常时显示未知，不推测组队人数。",
            "小于6.5分钟的对局适当降权，越短权重越低；超过15分钟的对局小幅增权，有上限。双方最后一击总数几乎持平且数量充足时较明显增权；数据缺失或双方为零时不推测。碾压局继续降权，多个因素共同影响最终对局权重。",
            "综合Fleta按最后一击总数汇总；综合分按对局权重汇总再参考持续高Fleta、胜率和KD。关键数据缺失不计分。",
            "评分范围0至100；100分为SSS，S从90分、A从80分、B从70分、C从60分起，其余D。SMURF为额外表现等级；上中下等马仅是本次名单内的相对排序，同分同档。只有一位有效评分玩家时不分上下等马，提示输入更多ID以比较。",
        ],
        "relative_notice": RELATIVE_NOTICE,
        "disclaimer": "自定义队内表现评分，不是网易官方分数，不代表段位；同分同档，无数据不评级",
    }
