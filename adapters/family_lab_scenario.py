"""Admin-only learning script; publish only occurred facts via normal ingress.

Future outcomes live in the central control DB, never in model-visible fixtures.
No model, sender, or service decision is called by this producer.
"""
import json
from pathlib import Path

VERSION='first-week-three-steady-v1'
DONE={'F01':[1,2,3,4,5],'F02':[1,2,3,4,5],'F03':[1,2,3,5],'F04':[1],'F05':[1]}
TIMES={'F01':'17:20','F02':'17:35','F03':'18:10','F04':'18:40','F05':'19:10'}
def scenario_key(run):return 'learning_scenario:'+run

def build_schedule(families):
    events=[]
    for f in families:
        data=json.loads((Path(f['workspace'])/'runtime/fixtures/mock_uid_00001_first_month.json').read_text())
        fid=f['family_id']
        for lesson in data['learning_daily']:
            no=lesson['lesson_no']
            if no not in DONE[fid] or lesson.get('completed_at'):continue
            enrollment=lesson.get('enrollment_id') or data['enrollments'][0]['id']
            event_id='scenario:'+VERSION+':'+fid+':'+str(no)
            events.append({'family_id':fid,'payload':{'schema_version':1,'event_type':'lesson.completed','event_id':event_id,'completion_id':event_id,'uid':fid,'enrollment_id':enrollment,'lesson_id':lesson['id'],'completed_at':lesson['date']+'T'+TIMES[fid]+':00+08:00'}})
    return {'version':VERSION,'events':sorted(events,key=lambda e:(e['payload']['completed_at'],e['family_id']))}

def script(lab,run):
    rows=lab.read('SELECT value FROM settings WHERE key=?',(scenario_key(run),))
    return json.loads(rows[0]['value']) if rows else {'events':[]}

def next_time(lab,run):
    times=[e['payload']['completed_at'] for e in script(lab,run['id'])['events'] if e['payload']['completed_at']>run['as_of']]
    return min(times) if times else None

def publish_due(lab,cfg,business_at):
    run=lab.current()
    if run['id']!=cfg['run_id'] or run['paused'] or business_at!=run['as_of']:return 0
    published=0
    for event in script(lab,run['id'])['events']:
        p=event['payload']
        if event['family_id']!=cfg['family_id'] or p['completed_at']>business_at:continue
        existing=lab.read("SELECT 1 FROM ingress WHERE run_id=? AND family_id=? AND kind='completion' AND json_extract(body,'$.lesson_id')=?",(run['id'],cfg['family_id'],p['lesson_id']))
        if existing:continue
        lab.ingest(run['id'],cfg['family_id'],p['event_id'],'completion',p,p['completed_at']);published+=1
    return published
