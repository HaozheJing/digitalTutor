"""Request chronology uses execution timestamps, never random run identifiers."""
import json
from pathlib import Path


def report(root, sent_run_ids=()):
    """sent_run_ids must be ordered by actual confirmed outbound occurrence."""
    rows=[]
    for path in Path(root).glob('*/api-requests.jsonl'):
        events=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        responses={x['sequence']:x for x in events if x['event']=='response'}
        for x in events:
            if x['event']!='request':continue
            rows.append({**x,'run_id':x.get('run_id',path.parent.name),
                'usage':responses.get(x['sequence'],{}).get('usage')})
    # Auxiliary intake calls happen before the loop observer is installed.
    for path in Path(root).glob('*/responsibility-review.json'):
        r=json.loads(path.read_text(encoding='utf-8'))
        for n,attempt in enumerate(r.get('attempts',[r])):
            if attempt.get('dispatch_started'):
                rows.append({'run_id':path.parent.name,'role':'service','request_kind':'intake_review',
                    'sequence':n,'started_at_ns':attempt.get('started_at_ns'),'usage':attempt.get('usage')})
    # Never mix DB sequence numbers with epoch timestamps in one sort key.
    ordered=sorted((r for r in rows if r.get('started_at_ns') is not None),
                   key=lambda r:(r['started_at_ns'],r['sequence']))
    service=[r for r in ordered if r.get('role')=='service' and r.get('request_kind')=='primary']
    first_sent_run=next(iter(sent_run_ids),None)
    sent=[r for r in service if r['run_id']==first_sent_run]
    tokens=[(r.get('usage') or {}).get('prompt_tokens',(r.get('usage') or {}).get('input_tokens')) for r in rows]
    known=[n for n in tokens if isinstance(n,int)]
    def first(items):
        return {k:items[0].get(k) for k in ('run_id','job_id','role','sequence','started_at_ns','usage')} if items else None
    return {'first_service_request':first(service),'first_confirmed_send_run_request':first(sent),
        'peak_input_tokens':max(known,default=None),'cumulative_known_input_tokens':sum(known),
        'requests':len(rows),'requests_missing_usage':sum(not isinstance(n,int) for n in tokens),
        'chronology_unknown':sum(r.get('started_at_ns') is None for r in rows),
        'order_rule':'actual_start_timestamp; unknown order never inferred from UUID',
        'ordered_requests':ordered}
