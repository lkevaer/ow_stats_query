"""Match context derived only from the recorded scoreboard and party roster."""
from __future__ import annotations

import math
import statistics


def numeric(player: dict, key: str) -> float | None:
    value = player.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError):
        return None


def death_context(team: list[dict], opponents: list[dict], seconds: float, *, is_stomp: bool) -> dict:
    deaths = [numeric(p, "death") for p in team]
    enemy_deaths = [numeric(p, "death") for p in opponents]
    known = len(team) >= 3 and all(d is not None for d in deaths)
    enemy_known = len(opponents) == len(team) and all(d is not None for d in enemy_deaths)
    average = statistics.mean(deaths) if known else None
    enemy_average = statistics.mean(enemy_deaths) if enemy_known else None
    # Raw counts prevent a long, high-death game from looking like a low-death one.
    load = max(average, average * 600 / seconds) if average is not None else None
    sensitivity = .30 + .70 * max(0., min(1., (load - 3) / 3)) if load is not None else 1.
    favorable = bool(known and enemy_known and not is_stomp
                     and enemy_average >= average * 1.5 and enemy_average - average >= 2)
    return {"known": known, "team_average": average, "enemy_average": enemy_average,
            "sensitivity": round(sensitivity, 4), "low_team_deaths": load is not None and load <= 4,
            "enemy_deaths_clearly_higher": favorable}


def survival_points(points: float, death: float, seconds: float, role: str, context: dict) -> float:
    if points < 60:
        points = 60 + (points - 60) * context["sensitivity"]
    rate = death * 600 / seconds
    allowance, slope = (4., 4.) if role == "support" else (6., 2.)
    return max(0., points - min(36., max(0., rate - allowance) * slope))


def support_survival(death: float, others: list[dict], seconds: float, context: dict) -> tuple[float, bool, bool]:
    """Return penalty, whether deaths are high, and a waived highest-death premium."""
    values = [numeric(p, "death") for p in others]
    rate = death * 600 / seconds
    absolute = min(16., max(0., rate - 5) * 2 + max(0., death - 8) * .5)
    if not values or any(d is None for d in values):
        return absolute, rate >= 6, False
    median = statistics.median(values)
    excess = max(0., death - median)
    relative = min(20., excess * 600 / seconds * 2.4 + 4 * excess / max(median, 1)) * context["sensitivity"]
    if median >= 6 and death >= median:
        relative += min(6., (median - 5) * .8)
    # Being highest is not enough: there must also be a substantial death load.
    premium = 0.
    waived = bool(context["enemy_deaths_clearly_higher"] and 0 < excess <= 1 and death - max(values) <= 1)
    if death > max(values) and death >= 5 and not context["low_team_deaths"] and not waived:
        premium = min(10., 5 + excess * 600 / seconds) * context["sensitivity"]
    return min(40., absolute + relative + premium), bool(excess > 0 or rate >= 6), waived


def kd_stats(kills: float | None, deaths: float | None) -> dict:
    if kills is None or deaths is None:
        return {"kd": None, "kd_perfect": False}
    return {"kd": round(kills / deaths, 4) if deaths > 0 else None,
            "kd_perfect": deaths == 0 and kills > 0}


def duration_weight(seconds: float) -> float:
    if seconds < 180:
        return .70 * seconds / 180
    if seconds < 390:
        return .70 + .30 * (seconds - 180) / 210
    if seconds > 900:
        return min(1.10, 1. + (seconds - 900) / 600 * .10)
    return 1.


def match_weights(team: list[dict], opponents: list[dict], seconds: float, stomp: float) -> dict:
    totals = []
    complete = len(team) == len(opponents) and len(team) >= 3
    for players in (team, opponents):
        values = [numeric(p, "finalHit") for p in players]
        totals.append(sum(values) if values and all(v is not None for v in values) else None)
    ratio = min(totals) / max(totals) if complete and all(v is not None for v in totals) and max(totals) > 0 else None
    balanced = bool(ratio is not None and ratio >= .90 and min(totals) >= 10)
    balance = 1.35 if balanced else 1.
    duration = duration_weight(seconds)
    return {"duration_weight": round(duration, 4), "balance_weight": balance,
            "sample_weight": round(duration * balance * stomp, 4),
            "balanced_final_blows": balanced, "final_blow_balance": round(ratio, 6) if ratio is not None else None,
            "opponent_final_blows": totals[1] if complete else None,
            "short_match": seconds < 390, "long_match": seconds > 900}


def party_info(own: dict, team: list[dict]) -> dict:
    """Use the upstream friendBnetIds party links, restricted to this game's team."""
    def identifier(value):
        if value is None or isinstance(value, (dict, list, bool)):
            return ""
        text = str(value).strip()
        return text if text and text not in {"0", "None", "null"} else ""

    own_id = identifier(own.get("bnetId"))
    roster = {identifier(p.get("bnetId")): p for p in team if identifier(p.get("bnetId"))}
    unknown = {"known": False, "size": None, "label": "组队未知"}
    if not own_id or len(roster) != len(team):
        return unknown
    graph = {key: set() for key in roster}
    malformed = set()
    for key, row in roster.items():
        friends = row.get("friendBnetIds")
        if not isinstance(friends, list):
            continue
        for raw in friends:
            friend = identifier(raw)
            if not friend or friend not in roster:
                malformed.add(key)
                continue
            if friend != key:
                graph[key].add(friend)
                graph[friend].add(key)
    members, pending = set(), [own_id]
    while pending:
        key = pending.pop()
        if key not in members:
            members.add(key)
            pending.extend(graph[key] - members)
    if members & malformed:
        return unknown
    if len(members) == 1 and not isinstance(own.get("friendBnetIds"), list):
        return unknown
    size = len(members)
    return {"known": True, "size": size, "label": {1: "单排", 2: "双排", 3: "三排", 4: "四排", 5: "五排"}.get(size, f"{size}人组队")}
