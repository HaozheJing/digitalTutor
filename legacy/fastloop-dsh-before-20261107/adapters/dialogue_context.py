"""Original dialogue across midnight; nightly checkpoints never discard pending text."""
import datetime as dt
import json
import preference_memory as m
import sql_tools as s

PAGE_CHARS = 60000
ANCHOR_ROWS = 60


def window(day, offset=0, snapshot=None, max_chars=None, max_rows=None):
    max_chars = PAGE_CHARS if max_chars is None else max_chars
    if type(offset) is not int or offset < 0: raise ValueError('invalid_dialogue_offset')
    s.validate_day(day)
    with m.database() as (c, sc):
        checkpoint=c.execute('SELECT seq FROM memory_checkpoints WHERE scope=?',(sc,)).fetchone()[0]
        forgotten={r[0] for r in c.execute('SELECT source_id FROM preference_forgotten WHERE scope=?',(sc,))}
        processed={}
        for r in c.execute("SELECT kind,source_id,body FROM memory_journal WHERE scope=? AND seq<=? AND kind IN ('message','delivery') ORDER BY seq",(sc,checkpoint)):
            processed[(r['kind'],r['source_id'])]=json.loads(r['body'])
        state=c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()
        state=json.loads(state[0]) if state else {}
        boundary=state.get('context_delivery_rowid',0)
        messages=[dict(r) for r in c.execute('SELECT rowid AS row_number,source_id,speaker,body,created_at FROM messages WHERE visible=1 ORDER BY rowid') if r['source_id'] not in forgotten]
        deliveries=[dict(r) for r in c.execute('SELECT rowid AS row_number,action_key,body,status,created_at FROM deliveries WHERE rowid>? ORDER BY rowid',(boundary,))]
        rows=[]
        for kind,items,pk in [('message',messages,'source_id'),('delivery',deliveries,'action_key')]:
            for index,item in enumerate(items):
                old=processed.get((kind,item[pk]),{})
                pending=not old or any(old.get(k)!=v for k,v in item.items() if k!='row_number')
                local_day=m.stamp(item['created_at']).astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
                if pending or local_day==day or index>=len(items)-ANCHOR_ROWS:
                    rows.append({'kind':kind,**{k:v for k,v in item.items() if k!='row_number'}})
        rows.sort(key=lambda r:(m.stamp(r['created_at']),r['kind'],r.get('source_id',r.get('action_key',''))))
        fingerprint=m.digest([sc,day,checkpoint,rows])
        if snapshot is not None and snapshot!=fingerprint: raise ValueError('dialogue_snapshot_changed_reload_conversation_window_read' if __import__('tool_surface').enabled('context') else 'dialogue_snapshot_changed_reload_daily_context')
        if offset and snapshot is None: raise ValueError('dialogue_snapshot_required')
        if offset>len(rows): raise ValueError('invalid_dialogue_offset')
        # Pages proceed newest to oldest. Never truncate the text of a row.
        available=list(reversed(rows))[offset:];selected=[];size=0
        for row in available:
            length=len(s.encoded(row))
            if selected and (size+length>max_chars or (max_rows is not None and len(selected)>=max_rows)): break
            selected.append(row);size+=length
        selected.reverse();end=offset+len(selected)
        return {'recent_conversation':[{k:v for k,v in r.items() if k!='kind'} for r in selected if r['kind']=='message'],
                'previous_deliveries':[{k:v for k,v in r.items() if k!='kind'} for r in selected if r['kind']=='delivery'],
                'conversation_window':{'snapshot':fingerprint,'offset':offset,'next_offset':end if end<len(rows) else None,
                    'has_more':end<len(rows),'total_rows':len(rows),'returned_rows':len(selected),'checkpoint':checkpoint,
                    'selection':'all_today_and_unconsolidated_plus_recent_60_per_kind',
                    'guidance':'原文按时间保留；delivery只有sent才代表已发送，其他状态不能说已收到。has_more时用conversation_window_read传business_day、snapshot和next_offset读剩余原文；不得把本页当完整历史。当前原话优先于旧记忆。遗忘来源不重新装载。'},
                'business_day':day}


def apply(value,day):
    if m.enabled(): value.update(window(day))
    return value
