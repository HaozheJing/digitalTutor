"""Stateless family-bound snapshot cursors; no mutation of memory semantics."""
import base64,json
from .context_transport import fingerprint
from .domain import require

def page(rows,family,event_id,limit,cursor=None,memory_id=None,kind=None):
    rows=[r for r in rows if (memory_id is None or r['id']==memory_id) and (kind is None or r.get('kind')==kind)]
    snapshot=fingerprint({'family':family,'event_id':event_id,'memory_id':memory_id,'kind':kind,'rows':rows})
    start=0
    if cursor:
        try:
            state=json.loads(base64.urlsafe_b64decode(cursor.encode()))
            require(state['snapshot']==snapshot,'MEMORY_CURSOR_STALE','记忆或范围已变化，请重新从第一页读取')
            start=state['offset']
            require(isinstance(start,int) and not isinstance(start,bool) and 0<=start<=len(rows),'MEMORY_CURSOR_INVALID','游标位置无效')
        except (ValueError,KeyError,TypeError):require(False,'MEMORY_CURSOR_INVALID','记忆游标无效')
    selected=rows[start:start+limit];end=start+len(selected)
    next_cursor=base64.urlsafe_b64encode(json.dumps({'snapshot':snapshot,'offset':end}).encode()).decode() if end<len(rows) else None
    return {'memories':selected,'has_more':end<len(rows),'matching_count':len(rows),'next_cursor':next_cursor,
            'snapshot_sha256':snapshot,'included_offset':start,'authoritative_for_controls':False}
