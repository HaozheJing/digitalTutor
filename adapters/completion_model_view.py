"""Compact only the model view of completion planning; retain authoritative audit state."""
from copy import deepcopy

FINAL = {'done', 'blocked', 'cancelled', 'expired', 'no_contact'}

def for_model(value):
    result = deepcopy(value)
    facts = result.get('workflow_facts')
    if isinstance(facts, dict) and facts.get('learning') == result.get('learning'):
        facts.pop('learning', None)
        facts['learning_location'] = '本次上下文顶层learning；同一份实时学情只装载一次'
    ledger = result.get('ledger')
    if isinstance(ledger, dict):
        for case in ledger.get('cases', []):
            timeline = case.get('timeline', [])
            history = case.get('history', [])
            if timeline and len(history) == len(timeline) and all(
                all(h.get(k) == t.get(k) for k in ('kind', 'at', 'body'))
                for h, t in zip(history, timeline)):
                case.pop('history', None)
            for entry in timeline:
                body = entry.get('body', {})
                owner = (body.get('source') or {}).get('owner', {})
                if entry.get('kind') in {'planned', 'scheduled'} and owner.get('type') != 'dialogue':
                    # Past internal planning prose is not a new family instruction.
                    for key in ('reason', 'source', 'skill_version'):
                        body.pop(key, None)
        for plan in ledger.get('plans', []):
            body = plan.get('body')
            if isinstance(body, dict):
                plan['decisions'] = [{k: d[k] for k in ('code', 'decision', 'task_id', 'run_at') if k in d}
                                     for d in body.get('decisions', [])]
                plan.pop('body', None)
        ledger['history_access'] = '当前事项状态、目标、家庭反馈、约束和任务回执保持完整；历史规划仅列决策，完整审计可按需用service_case_list读取。'
    versions = result.get('workflow_versions')
    if isinstance(versions, list):
        fields = ('id', 'case_id', 'skill', 'day', 'due_at', 'expires_at', 'status', 'revision',
                  'dedupe_key', 'needs_revalidation', 'candidate_id', 'result')
        result['workflow_versions'] = [{k: row[k] for k in fields if k in row}
                                      if row.get('status') in FINAL else row for row in versions]
    return result
