"""Wire attempts and confirmed usage, including failures; never infer zero billing."""
import json

def account(result,work):
    review_path=work/'responsibility-review.json';ledger=work/'api-requests.jsonl'
    if not review_path.exists() and not ledger.exists():return result
    review=json.loads(review_path.read_text(encoding='utf-8')) if review_path.exists() else {}
    rows=[]
    if ledger.exists():
        for line in ledger.read_text(encoding='utf-8').splitlines():
            try:rows.append(json.loads(line))
            except ValueError:pass
    requests={r['sequence'] for r in rows if r.get('event')=='request'}
    responses={r['sequence']:r for r in rows if r.get('event')=='response'}
    usages=[responses[n]['usage'] for n in requests if n in responses and responses[n].get('usage')]
    attempts=[r for r in review.get('attempts',[review]) if r.get('dispatch_started')]
    unknown=len(requests)-len(usages)+sum(not bool(r.get('usage')) for r in attempts)
    usages.extend(r['usage'] for r in attempts if r.get('usage'))
    result['native_usage']=dict(result.get('usage',{}));result['native_api_calls']=result.get('api_calls',0)
    if review:result['responsibility_review']={k:review.get(k) for k in ('status','model','temperature','thinking','usage','seconds','current_messages','dispatch_started','response_received')}
    prompt=sum(u.get('prompt_tokens',0) or 0 for u in usages)
    completion=sum(u.get('completion_tokens',0) or 0 for u in usages)
    cache=sum((u.get('prompt_tokens_details') or {}).get('cached_tokens',0) or 0 for u in usages)
    calls=len(requests)+len(attempts)
    result.update(api_calls=calls,usage_includes_intake_review=True,usage_complete=unknown==0,
        unknown_usage_requests=unknown,confirmed_api_responses=len(responses)+sum(bool(r.get('response_received')) for r in attempts),
        usage={'session_prompt_tokens':prompt,'session_completion_tokens':completion,
            'session_input_tokens':max(0,prompt-cache),'session_output_tokens':completion,
            'session_cache_read_tokens':cache,'session_api_calls':calls})
    return result
