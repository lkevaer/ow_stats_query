"""Backend-only hero adjustments; these are not frontend rule announcements."""
from __future__ import annotations


_HERO_KEYS = {
    "207165582859042819": "tracer", "猎空": "tracer", "tracer": "tracer",
    "207165582859042857": "genji", "源氏": "genji", "genji": "genji",
    "207165582859043334": "echo", "回声": "echo", "echo": "echo",
}
_NEGATIVE_METRIC_SCALE = .75


def mobile_dps_profile(hero_guid: str, hero: dict, role: str) -> str | None:
    if role != "dps":
        return None
    return _HERO_KEYS.get(hero_guid) or _HERO_KEYS.get(str(hero.get("name") or "").strip().casefold())


def soften_mobile_penalty(points: float, metric: str, profile: str | None) -> float:
    # Reduce only the deficit below neutral. Very poor performance stays negative.
    if profile and metric in {"damage", "survival"} and points < 60:
        return 60 + (points - 60) * _NEGATIVE_METRIC_SCALE
    return points


def metric_weights(base_weights: dict[str, float], profile: str | None) -> dict[str, float]:
    weights = dict(base_weights)
    if profile == "tracer":
        # Relative ten-percent increase of this metric, not ten percentage points
        # and not a multiplier of the player's final score.
        weights["final_blows"] *= 1.10
    return weights
