"""Write JSON Schemas for the AI results the app accepts (docs/agent/schemas).

The schemas are generated from the same pydantic models the import endpoints use, so a
result that matches them passes shape validation; the app additionally checks verbatim
evidence and package versions against the local database. Run after changing a model:
    python tools/export_agent_schemas.py          (write)
    python tools/export_agent_schemas.py --check  (exit 1 when stale)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'docs' / 'agent' / 'schemas'
INTERNAL = {'dry_run', 'acknowledge_stale', 'acknowledge_career_stale_token'}


def strip_internal(schema):
    schema = json.loads(json.dumps(schema))
    for part in [schema, *schema.get('$defs', {}).values()]:
        for name in INTERNAL:
            part.get('properties', {}).pop(name, None)
            if name in part.get('required', []):
                part['required'].remove(name)
    return schema


def schemas():
    from app.ai_review import BulkReviewImport, ReviewImport
    from app.analysis import AnalysisImport
    from app.main import PriorityResultRow
    from app.requirements import RequirementImport
    row = PriorityResultRow.model_json_schema()
    priorities = {
        'title': 'PriorityResult', 'type': 'object', 'additionalProperties': False,
        'required': ['package_id', 'priorities'],
        'properties': {
            'package_id': {'type': 'string', 'minLength': 36, 'maxLength': 36,
                           'description': '原数据包的 package_id，逐字复制'},
            'priorities': {'type': 'array', 'minItems': 1, 'maxItems': 1000, 'items': row}}}
    found = {
        'requirements-result': RequirementImport.model_json_schema(),
        'findings-result': AnalysisImport.model_json_schema(),
        'priorities-result': priorities,
        'review-result': ReviewImport.model_json_schema(),
        'review-bulk-result': BulkReviewImport.model_json_schema(),
    }
    result = {}
    for name, schema in found.items():
        schema = strip_internal(schema)
        schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
        schema['$id'] = f'https://github.com/cerveth07/ai-internship-radar/docs/agent/schemas/{name}.schema.json'
        result[name] = schema
    return result


def main():
    check = '--check' in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    stale = []
    for name, schema in schemas().items():
        path = OUT / f'{name}.schema.json'
        text = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
        if path.exists() and path.read_text(encoding='utf-8') == text:
            continue
        stale.append(name)
        if not check:
            path.write_bytes(text.encode('utf-8'))
    if check and stale:
        print('Schemas are stale: ' + ', '.join(stale) + '. Run python tools/export_agent_schemas.py')
        return 1
    print('schemas up to date' if check else f'wrote {len(stale)} schema file(s) to {OUT}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
