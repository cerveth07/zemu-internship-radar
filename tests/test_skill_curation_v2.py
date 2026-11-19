"""The three rounds must remain conservative and evidence preserving."""
from app.skill_curation import classify_candidate, canonical_personal_status
import pytest


@pytest.mark.parametrize('quote', [
    '至少掌握一种编程语言', '至少熟练掌握 1 门编程语言', '至少熟悉一项数据分析工具',
    '至少一种编程语言',
])
def test_alternative_language_requirement_keeps_review_hint(quote):
    assert classify_candidate('Python', 'technical', 'must', [quote])['curation_flags'] == ['possible_any_of']


@pytest.mark.parametrize('quote', ['至少一年 Python 经验', '至少每周到岗三天', '熟练掌握 Python'])
def test_non_alternative_requirements_are_not_flagged(quote):
    assert classify_candidate('Python', 'technical', 'must', [quote])['curation_flags'] == []


def test_round_one_general_hiring_skill_is_not_an_actionable_study_task():
    assert not classify_candidate("沟通协调", "general", "must",
                                  ["具备沟通协调能力"])["actionable"]
    assert classify_candidate("RAG", "technical", "must",
                              ["熟悉 RAG"])["actionable"]


def test_round_two_alias_mastery_preserved_and_canonical_wins():
    rows = [
        {"skill_key": "编程能力", "status": "missing", "note": "旧别名",
         "updated_at": "2026-10-22 20:00:00"},
        {"skill_key": "编程基础", "status": "met", "note": "规范名",
         "updated_at": "2026-10-21 20:00:00"},
    ]
    result = canonical_personal_status(rows, {"编程能力": "编程基础"})
    assert result["编程基础"]["status"] == "met"
    assert result["编程基础"]["note"] == "规范名"


def test_round_three_review_flags_do_not_overwrite_claim_strength():
    assert classify_candidate("Python", "technical", "must",
                              ["熟悉 Python 或 SQL 者优先"])["curation_flags"] == [
                                  "must_has_bonus_word"]
    assert classify_candidate("Python", "technical", "must",
                              ["至少掌握一种编程语言"])["curation_flags"] == [
                                  "possible_any_of"]
    assert classify_candidate("PyTorch", "technical", "must",
                              ["不要求使用 PyTorch"])["curation_flags"] == [
                                  "negated_or_optional"]
    assert classify_candidate("SQL", "technical", "bonus",
                              ["熟悉 SQL"])["curation_flags"] == ["bonus_needs_context"]
