"""Conservative, evidence-preserving curation of staged JD skill candidates."""
from __future__ import annotations

import re

# Round 1: not a stand-alone technical/PM study task; keep JD evidence intact.
GENERAL_SKILLS = frozenset({
    "问题分析", "沟通协调", "文字表达", "综合素质", "责任心",
    "学习能力", "沟通能力", "团队协作", "逻辑思维", "积极主动",
    "抗压能力", "执行力", "表达能力", "自驱力",
})
BONUS_WORD = re.compile(r"优先|加分|更佳|prefer(?:red)?|bonus", re.I)
ALTERNATIVES = re.compile(r"至少\s*(?:(?:熟练掌握|掌握|熟悉|精通|会用)\s*)?(?:一|1)\s*(?:种|项|门)|任选|任一|之一|二选一|三选一")
NEGATED = re.compile(r"不要求|无需|不需要|非必须|不是必需|不作要求")


def classify_candidate(name: str, category: str, strength: str, quotes: list[str]) -> dict:
    """Rounds 1 and 3: classify actionability and flag ambiguous evidence."""
    actionable = category != "general" and name.strip() not in GENERAL_SKILLS
    evidence = " ｜ ".join(quotes)
    flags = []
    if NEGATED.search(evidence):
        flags.append("negated_or_optional")
    if strength == "must" and BONUS_WORD.search(evidence):
        flags.append("must_has_bonus_word")
    if strength == "must" and ALTERNATIVES.search(evidence):
        flags.append("possible_any_of")
    if strength == "bonus" and not BONUS_WORD.search(evidence):
        flags.append("bonus_needs_context")
    return {"actionable": actionable, "curation_flags": flags}


def canonical_personal_status(rows, aliases: dict) -> dict:
    """Round 2: normalize personal status; explicit canonical keys take priority."""
    selected = {}
    for row in rows:
        raw = row["skill_key"].strip().casefold()
        target = aliases.get(raw, row["skill_key"].strip()).casefold()
        native = raw == target
        order = (int(native), row["updated_at"], raw)
        if target not in selected or order > selected[target][0]:
            selected[target] = (order, dict(row))
    return {key: entry[1] for key, entry in selected.items()}
