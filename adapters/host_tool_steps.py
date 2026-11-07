"""Fixed experiment orchestration; no arbitrary model action or provider bypass."""
import json,sys,sqlite3
from contextlib import closing
import sql_tools as s
import tool_surface as surface

def enabled():
    return surface.enabled('host_steps') and __import__('service_workflow').enabled()

def task_facts(row,facts):
    if not enabled():return facts
    import observability as audit,business as b,task_context,proactive
    result=dict(facts)
    if row['kind']=='s07':
        result['action_candidates']=audit.invoke('action_candidates',{'day':row['day']},lambda:task_context.candidates(row['day']),origin='host')
    if row.get('candidate_id') and row['kind'] not in ('s01','highlight'):
        result['preflight']=audit.invoke('check_proactive',{'candidate_id':b.token(row['candidate_id'])},lambda:b.project(proactive.check(row['candidate_id'])),origin='host')
    return result

def event_materials(event_id):
    if not enabled():return []
    import observability as audit,voice_understanding as voice,material_cache
    rows=s.read('SELECT kind,status,payload FROM events WHERE id=?',(event_id,))
    if not rows or rows[0]['status']!='dispatched':raise ValueError('dispatched_event_required')
    event=rows[0];payload=json.loads(event['payload'])
    if event['kind']!='passive':
        if event['kind']=='memory_plan' and not s.read('SELECT 1 FROM memory_reviews WHERE event_id=?',(event_id,)):
            audit.invoke('memory_review',{'event_id':event_id},lambda:s.memory_review(event_id,'宿主接收历史原话；语义记忆仍由夜间流程整理，不补发历史消息。'),origin='host')
        return []
    sources=payload.get('source_message_ids') or [payload.get('source_message_id')]
    if not s.read("SELECT 1 FROM sqlite_master WHERE name='voice_inputs'"):return []
    state=__import__('test_mode').state();result=[]
    for source in dict.fromkeys(sources):
        for row in s.read('SELECT id FROM voice_inputs WHERE source_id=? AND run_id=? AND uid=?',(source,state['run_id'],state['uid'])):
            args={'asset_id':row['id']}
            value=audit.invoke('understand_audio',args,lambda:material_cache.capture('understand_audio',args,voice.understand(row['id'])),origin='host')
            if len(s.encoded(value))>6000:
                value={'asset_id':row['id'],'material_ref':value['material_ref'],'status':value.get('status'),'needs_audio_resend':value.get('needs_audio_resend'),'current_speaker':value.get('current_speaker'),'read_required':True,'guidance':'完整派生识别较长，回复前使用evidence_read分页读取，不能仅凭语音占位标记猜内容。'}
            result.append(value)
    return result

def finish_event(event_id):
    if not enabled():return {'status':'not_enabled'}
    rows=s.read('SELECT kind,status FROM events WHERE id=?',(event_id,))
    if not rows or rows[0]['kind']=='memory_consolidation':return {'status':'not_owned'}
    if rows[0]['status'] not in ('dispatched','awaiting_delivery'):return {'status':rows[0]['status'],'completion_scope':'current_message_event','business_issue_resolved':'not_established'}
    import business,observability as audit,test_mode as tm
    try:
        return audit.invoke('finish_event',{'event_id':event_id,'day':tm.clock().date().isoformat()},lambda:business.finish(event_id,tm.clock().date().isoformat()),origin='host')
    except ValueError:
        # A missing/unknown delivery or incomplete internal plan keeps the old
        # retry/recovery contract. Never manufacture completion from final prose.
        return {'status':'incomplete','reason':'persisted_event_completion_requirements_not_met'}

def inspected_image(value):
    if not enabled() or value.get('status')!='ready' or not value.get('asset_id'):return value
    import festival_service as festival,observability as audit
    asset=festival.read_asset(value['asset_id'])
    inspection=({'asset_id':value['asset_id'],'sha256':asset['sha256'],'inspection':asset['inspection'],'reused':True}
                if asset.get('inspection') else audit.invoke('inspect_festival_image',{'asset_id':value['asset_id']},lambda:festival.inspect_festival_image(value['asset_id']),origin='host'))
    ok=bool(inspection.get('inspection'))
    return {**value,'requires_inspection':False,'inspection_completed':ok,'inspection_result':inspection,
            'next':'根据实际检查结果判断是否采用；不合适则重新生成。' if ok else '检查未完成，不能发送本素材。'}

if __name__=='__main__':
    assert sys.argv[1]=='finish-event'
    print(s.encoded(finish_event(sys.argv[2])))
