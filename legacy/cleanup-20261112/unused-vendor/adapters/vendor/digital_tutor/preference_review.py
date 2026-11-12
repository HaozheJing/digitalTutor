"""Route explicit contact-time preferences to existing plans, without inventing a time."""
import re
from .storage import digest
from .report_contract import message_source_digest

def request_review(app,family,source,memory,binding):
    text=source['body']
    if memory['kind']!='stated_preference' or not re.search(r'联系|找我|打扰',text) or not re.search(r'\d{1,2}[:：点时]|[一二三四五六七八九十两]+点|早上|上午|下午|晚上',text):return None
    events=[e for e in app.store.list('event',family) if e.get('status') not in ('done','cancelled') and e.get('next_action')]
    if not events:return None
    report_id='contact-preference-'+digest([source['id'],memory['id'],memory['version']])
    key=app.report_key(family,report_id,1)
    if any(r['id']==key for r in app.store.list('report',family)):return key
    refs=[{'kind':'message','id':source['id'],'version':source['version']}]
    payload={'report_id':report_id,'revision':1,'event_ids':[e['id'] for e in events],
      'facts':['家长原话：'+text],
      'suggested_update':'核对原话中的联系时间，更新受影响原事项的next_action.execute_at；仅一般偏好不改原合同截止，不新建目标。与已确认约定冲突则保留并澄清。无需向家庭叙述内部更新。',
      'remaining_work':None,'evidence_refs':refs,'supersedes_revision':None}
    app.store.put('report',key,family,{'payload':payload,'event_ids':payload['event_ids'],'report_id':report_id,'revision':1,'resolution':None,'created_at':app.clock.iso(),'origin_run_id':binding.get('run_id'),'schema_version':2,'source_snapshots':[{'ref':refs[0],'message_digest':message_source_digest(source)}],'source_kind':'explicit_contact_preference_review'})
    for event in events:app.store.connection.execute('INSERT INTO report_links VALUES(?,?,?,0)',(key,event['id'],family))
    app.store.put('job','progress:'+key,family,{'kind':'progress','report_key':key,'status':'ready'})
    return key
