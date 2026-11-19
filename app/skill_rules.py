"""Offline, evidence-first keyword candidates. Never writes tags or calls a model.

The rules intentionally optimize for precision; human review is required.
Only verbatim JD lines may be returned as evidence. Terms like '不了解 Python'
remain candidates but must not be interpreted as proof of a requirement.
"""
import re

# Match ordered by longest/specific patterns to avoid broad accidental tags.
RULES = [
    ('Python', 'engineering', r'(?<![\w])Python(?![\w])'),
    ('SQL', 'data', r'(?<![\w])SQL(?![\w])'),
    ('Agent / 智能体', 'llm', r'(?<![a-zA-Z])Agent(?![a-zA-Z])|智能体'),
    ('RAG / 检索增强', 'llm', r'(?<![a-zA-Z])RAG(?![a-zA-Z])|检索增强'),
    ('LLM / 大语言模型', 'llm', r'(?<![a-zA-Z])LLM(?![a-zA-Z])|大语言模型|大模型'),
    ('LangChain', 'llm', r'(?<![a-zA-Z])LangChain(?![a-zA-Z])'),
    ('LlamaIndex', 'llm', r'(?<![a-zA-Z])LlamaIndex(?![a-zA-Z])'),
    ('模型评测', 'evaluation', r'模型评测|模型评价|大模型评测|(?<![a-zA-Z])evals?(?![a-zA-Z])'),
    ('Prompt / 提示词工程', 'llm', r'提示词(?:工程|优化|设计)|(?<![a-zA-Z])Prompt(?:\s*Engineering)?(?![a-zA-Z])'),
    ('产品需求分析', 'product', r'需求(?:调研|分析|拆解)|(?:PRD|产品需求文档)'),
    ('工作流编排', 'engineering', r'工作流(?:设计|编排|开发)|(?<![a-zA-Z])Workflow(?![a-zA-Z])'),
    ('向量数据库', 'data', r'向量数据库|向量检索'),
    ('数据分析', 'data', r'数据分析|数据洞察'),
]

# These classifiers are deliberately conservative. Text may say "加分" in a
# separate section: use only the same line, not adjacent paragraphs.
BONUS = re.compile(r'加分|优先考虑|优先录用|有.{0,18}经验者优先|(?:a\s*)plus\b', re.I)
MUST = re.compile(r'必须|要求|熟悉|掌握|精通|具备|能够|需要|任职资格|任职要求|岗位要求', re.I)
NEGATION = re.compile(r'不要求|无需|不必|非必须|没有要求|不需要', re.I)


def suggest_skills(jd_text: str, existing: set[str] | None = None) -> list[dict]:
    existing = {name.casefold() for name in (existing or set())}
    results: list[dict] = []
    # Lines are meaningful evidence units; limit extremely long paragraphs but
    # never invent substring evidence by paraphrasing.
    lines = [line.strip() for line in jd_text.splitlines() if line.strip()]
    for name, category, expression in RULES:
        rx = re.compile(expression, re.I)
        for line in lines:
            if not rx.search(line):
                continue
            if NEGATION.search(line):
                necessity = 'unspecified'
            elif BONUS.search(line):
                necessity = 'bonus'
            elif MUST.search(line):
                necessity = 'must'
            else:
                necessity = 'unspecified'
            results.append({
                'name': name, 'category': category, 'necessity': necessity,
                'evidence': line[:2000], 'already_linked': name.casefold() in existing,
                'method': 'keyword_candidate',
            })
            break
    return results