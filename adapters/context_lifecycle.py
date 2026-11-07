"""Read-only business checkpoint; never infer delivery success from model text."""
import json,sys
import sql_tools as s
import test_mode as tm

def checkpoint(task_id,request_id=None):
    if task_id.startswith('passive:'):return __import__('passive_context').checkpoint(task_id[8:],request_id)
    state=tm.state()
    if not state:raise ValueError('bound_family_required')
    rows=s.read('SELECT id,run_id,status,kind,skill,reason,result,native_request_id FROM service_tasks WHERE id=? AND run_id=?',(task_id,state['run_id']))
    if not rows:raise ValueError('task_not_in_current_run')
    task=rows[0]
    if task['status']=='running' and (not request_id or task['native_request_id']==request_id):return {'ready':False}
    receipts=s.read('SELECT d.* FROM service_links l JOIN deliveries d ON l.action_key=d.action_key WHERE l.task_id=?',(task_id,))
    # Keep actual text/media provenance and separate receipt states, not just a success flag.
    return {'ready':True,'closed_request_id':request_id,'recovered_previous_attempt':task['status']=='running','family_uid':state['uid'],'run_id':state['run_id'],'task':task,'deliveries':receipts,'materials':__import__('material_cache').index()}
if __name__=='__main__':print(json.dumps(checkpoint(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else None),ensure_ascii=False))
