"""Due-time catch-up and newly observed asset wakes inside the existing workflow.

No forecast, alternate sender or new recurring model loop. Durable keys survive
restart and the original execution/contact/receipt gates still own delivery.
"""
import hashlib,json
import sql_tools as s

PERIODIC={'support-zero-completion','rebuild-reading-rhythm','close-week-reading-gap','plan-weekend-makeup'}

def ensure(c,state):
    import service_workflow as wf,completion_planner as cp
    if not state.get('auto_highlights') or not wf.enabled(state):return
    entries=wf.catalog(c,state)
    for entry in entries:
        failed_daily_s01=entry['skill']=='remind-reading' and c.execute(
            "SELECT 1 FROM service_tasks WHERE run_id=? AND day=? AND kind='plan' AND status='blocked' AND id LIKE 'service-%-plan'",
            (state['run_id'],state['as_of'][:10])).fetchone()
        if entry['skill'] not in PERIODIC and not failed_daily_s01:continue
        due=entry['timing']['at']
        if state['as_of']<due:continue
        for item in entry['opportunities']:
            if item['status']!='eligible' or state['as_of']>item['time_window']['latest']:continue
            if c.execute('SELECT 1 FROM service_task_versions WHERE uid=? AND dedupe_key=?',(state['uid'],item['dedupe_key'])).fetchone():continue
            if c.execute('SELECT 1 FROM contact_constraints WHERE day=?',(item.get('day',state['as_of'][:10]),)).fetchone():continue
            if wf.constraint_blocks(c,state,entry['skill'],at=state['as_of']):continue
            wf.add(c,state,item,entry,due,'到点按最新事实补齐尚无任务的已授权检查；执行时仍判断家庭安排',{'kind':'host_due_recheck','observed_at':state['as_of']},item['id'])
    c.execute('CREATE TABLE IF NOT EXISTS service_observed_assets(run_id TEXT,asset_id TEXT,plan_id TEXT,observed_at TEXT,PRIMARY KEY(run_id,asset_id))')
    unseen=[]
    for entry in entries:
        if entry['kind']!='highlight':continue
        for item in entry['opportunities']:
            if item['status']!='eligible' or not item['time_window']['earliest']<=state['as_of']<=item['time_window']['latest']:continue
            if c.execute('SELECT 1 FROM service_observed_assets WHERE run_id=? AND asset_id=?',(state['run_id'],item['id'])).fetchone():continue
            if c.execute('SELECT 1 FROM service_task_versions WHERE uid=? AND dedupe_key=?',(state['uid'],item['dedupe_key'])).fetchone():continue
            unseen.append(item)
    if not unseen:return
    ident='service-observed-'+hashlib.sha256((state['run_id']+':'+'|'.join(sorted(x['id'] for x in unseen))).encode()).hexdigest()[:32]
    cp.insert(c,state,ident,'plan','plan-today',state['as_of'],state['as_of'][:10]+'T23:59:59+08:00',None,'新高光已实际出现，增量判断既有事项；未送达不算覆盖')
    c.executemany('INSERT INTO service_observed_assets VALUES(?,?,?,?)',[(state['run_id'],x['id'],ident,state['as_of']) for x in unseen])
