"""通用 AI 分析 Prompt 构建模块。

从「锐评」功能中抽取的通用组件：
- 助攻判读补充规则（全局适用，避免机械解读助攻指标）
- 身份设定（资深电竞数据分析师）
- 输出要求（严格中文 + 纯 JSON）
- JSON 模板（score / summary / attribute_scores / evaluation / extra 等通用字段）
- 人格化设定（可选 ANALYSIS_PERSONA_PROMPT）

被一个模块使用：
1. overstats.src.modules.dashen_match.service  — 单场对局锐评
"""
from __future__ import annotations

from typing import Any, Dict


# ============================================================
# 1. 助攻判读补充规则
# ============================================================
ASSIST_RULE_TEXT = """【助攻判读补充规则】
不要把"助攻低"或"没有助攻"机械地当成负面结论。对于坦克位和输出位，助攻不是通用核心指标。很多英雄的技能机制、收割定位、爆发击杀方式或单点作战方式，本来就不容易稳定获得助攻。除非该英雄本身明显依赖团队增益、控制、挂状态或持续参与混战，且同场其他数据也能证明其协同性偏低，否则禁止写出无意义的"零助攻/助攻少所以表现差"评价。"""


# ============================================================
# 2. 身份设定
# ============================================================
IDENTITY_TEXT = """【身份】
请扮演一位资深的电竞数据分析师，根据提供的比赛数据，输出一份犀利、简明扼要的分析报告。"""


# ============================================================
# 3. 输出要求
# ============================================================
OUTPUT_REQUIREMENTS_TEXT = """【输出要求】
1. 必须严格使用中文，只输出纯 JSON，不要 markdown，不要解释，不要前后缀。
2. 分析必须专业、简明、直接，结论要清晰，不能硬夸，不能胡编。
3. 所有判断都必须以给定数据为准，不得虚构不存在的对局细节。
4. 如果 JSON 字符串内部需要双引号，请转义为 \\\\\\"，避免输出非法 JSON。"""


# ============================================================
# 4-a. 单场锐评 JSON 模板
# ============================================================
MATCH_REVIEW_TEMPLATE = """【任务】
1. 客观给焦点玩家评分，只能填 S/A/B/C/D。
2. 用一句话指出焦点玩家最大优点或最大问题。
3. 只写一个最关键的胜负手。
4. 必须明确点出真正的 MVP 或背锅位，不能强行偏袒焦点玩家。
5. 必须严格比较三路：坦克位、输出位、辅助位。
6. 要写出最突出的个人表现、关键数据差距，或者同英雄对位 Diff。
7. attribute_scores 的数值必须原样保留，不允许改动。

【输出 JSON 模板】
{{
  "player_id": "{target_id}",
  "score": "S/A/B/C/D",
  "general_summary": "一句话核心总结",
  "key_to_win_loss": "决定胜负的唯一核心点",
  "red_black_list": {{
    "mvp_or_potg": "真正的 MVP 或背锅位及依据",
    "role_comparison": [
      "坦克位：[双方对比结论]",
      "输出位：[双方对比结论]",
      "辅助位：[双方对比结论]"
    ],
    "outstanding_performance": "最突出的个人表现、关键数据差距或同英雄对位 Diff"
  }},
  "summary": "一句话总结本场整体观感",
  "attribute_scores": {{
    "anti_pressure": {anti_pressure},
    "teamwork": {teamwork},
    "aggressiveness": {aggressiveness},
    "match_quality": {match_quality}
  }},
  "evaluation": "基于四项属性分的针对性评价",
  "extra": "100字以内的人格化鼓励/安慰/小结",
  "carry_index_data": []
}}

【原始比赛数据如下】
{match_summary_text}
{separator_line}
{detailed_stats_text}
{separator_line}"""

# ============================================================
# 5. 辅助函数
# ============================================================
def _get_persona_text(config_obj: Any, target_id: str, score_bundle: Dict[str, int]) -> str:
    """从配置读取人格化设定（ANALYSIS_PERSONA_PROMPT），并填充变量。

    若配置为空或出错，返回空字符串。
    """
    try:
        text = str(getattr(config_obj, "ANALYSIS_PERSONA_PROMPT", "") or "").strip()
    except Exception:
        return ""
    if not text:
        return ""
    try:
        return text.format(
            target_id=target_id,
            **{k: v for k, v in score_bundle.items()},
        )
    except Exception:
        # 占位符可能不匹配，原样返回
        return text


def _build_separator_line() -> str:
    return "-" * 50


# ============================================================
# 6. 对外接口：一个主要函数
# ============================================================
def build_match_review_prompt(
    config_obj: Any,
    target_id: str,
    match_summary_text: str,
    detailed_stats_text: str,
    score_bundle: Dict[str, int],
) -> str:
    """构建单场锐评的完整 Prompt。

    参数：
    - config_obj: 配置对象，需提供 ANALYSIS_PERSONA_PROMPT
    - target_id: 焦点玩家 ID（battletag / full_id）
    - match_summary_text: 总结性表格（玩家列表 + 12项指标）
    - detailed_stats_text: 详细对局数据（各玩家的英雄详情）
    - score_bundle: {anti_pressure, teamwork, aggressiveness, match_quality} 四项 0-100 分

    返回：
    - 完整的 Prompt 字符串（不含头尾空白）
    """
    persona = _get_persona_text(config_obj, target_id, score_bundle)
    template = MATCH_REVIEW_TEMPLATE.format(
        target_id=target_id,
        anti_pressure=score_bundle.get("anti_pressure", 0),
        teamwork=score_bundle.get("teamwork", 0),
        aggressiveness=score_bundle.get("aggressiveness", 0),
        match_quality=score_bundle.get("match_quality", 0),
        match_summary_text=match_summary_text or "（无总结数据）",
        detailed_stats_text=detailed_stats_text or "（无详细数据）",
        separator_line=_build_separator_line(),
    )
    parts = [p for p in [persona, ASSIST_RULE_TEXT, IDENTITY_TEXT, OUTPUT_REQUIREMENTS_TEXT, template] if p]
    return "\n".join(parts).strip()
