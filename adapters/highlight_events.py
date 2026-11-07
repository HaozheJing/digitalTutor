"""Authenticated producer facts; no achievement inference or model-authored media."""
import datetime as dt,hashlib,json,re,sqlite3
from contextlib import closing
from urllib.parse import urlsplit
import sql_tools as s
import test_mode as tm

TYPES={'seven_day_completion':'highlight-seven-day-completion','first_course_completion':'highlight-course-start','three_day_completion':'celebrate-learning-streak','book_completion':'highlight-book-completion','first_perfect_answer':'highlight-first-perfect-answer','expressive_answer':'highlight-expressive-answer','quiz_ranking':'highlight-quiz-ranking','ranking_progress':'highlight-ranking-progress'}
class InvalidEvent(ValueError):pass
class EventConflict(ValueError):pass

def config():
 p=s.R/'config/highlight-events.json'
 return json.loads(p.read_text()) if p.exists() else {}

def registered_image(url):
 state=tm.state()
 if not state:return False
 with closing(sqlite3.connect(s.DB)) as c:
  if not c.execute("SELECT 1 FROM sqlite_master WHERE name='external_highlight_events'").fetchone():return False
  return any(json.loads(row[0]).get('image',{}).get('url')==url for row in c.execute('SELECT payload FROM external_highlight_events WHERE run_id=? AND uid=?',(state['run_id'],state['uid'])))

def tables(c):
 c.execute('CREATE TABLE IF NOT EXISTS external_highlight_events(run_id TEXT,source TEXT,event_id TEXT,uid TEXT,achievement_id TEXT,payload_hash TEXT,payload TEXT,received_at TEXT,received_business_at TEXT,PRIMARY KEY(run_id,source,event_id),UNIQUE(run_id,source,uid,achievement_id))')

def stamp(value):
 try:
  d=dt.datetime.fromisoformat(value)
  if d.tzinfo is None:raise ValueError()
  return d.astimezone(tm.TZ).isoformat(timespec='seconds')
 except (TypeError,ValueError):raise InvalidEvent('timestamp_requires_timezone') from None

def ingest(value,dry_run=False):
 cfg=config();state=tm.state()
 if not cfg.get('enabled') or not state:raise InvalidEvent('highlight_ingress_disabled')
 fields={'schema_version','event_id','achievement_id','uid','enrollment','achievement_type','achieved_at','expires_at','caption','image','evidence'}
 if not isinstance(value,dict) or set(value)!=fields or value['schema_version']!=1:raise InvalidEvent('invalid_event_schema')
 for key in ('event_id','achievement_id'):
  if not isinstance(value[key],str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',value[key]):raise InvalidEvent('invalid_'+key)
 if value['uid']!=state['uid']:raise InvalidEvent('family_binding_mismatch')
 enrollment=value['enrollment'];binding=__import__('course_service').binding(state['uid'])
 if not isinstance(enrollment,dict) or set(enrollment)!={'class_type','cohort'} or any(not isinstance(enrollment[k],str) or enrollment[k]!=str(binding.get(k)) for k in enrollment):raise InvalidEvent('enrollment_binding_mismatch')
 if value['achievement_type'] not in TYPES:raise InvalidEvent('unknown_achievement_type')
 for key,limit in [('caption',3000),('evidence',2000)]:
  if not isinstance(value[key],str) or not 1<=len(value[key].strip())<=limit:raise InvalidEvent('invalid_'+key)
 image=value['image']
 if not isinstance(image,dict) or set(image)!={'url'} or not isinstance(image['url'],str) or len(image['url'])>2048:raise InvalidEvent('invalid_image')
 url=urlsplit(image['url'])
 if url.scheme!='https' or url.hostname not in cfg.get('allowed_image_hosts',[]) or url.username or url.password or url.fragment or url.port not in (None,443):raise InvalidEvent('untrusted_image_url')
 at,end=stamp(value['achieved_at']),stamp(value['expires_at']);now=state['as_of']
 # Preserve producer bytes in caption and URL. Canonicalize only timestamps.
 body={**value,'achieved_at':at,'expires_at':end};encoded=s.encoded(body);sha=hashlib.sha256(encoded.encode()).hexdigest();source=cfg.get('source','external-achievements')
 if dry_run:
  if at>now or end<=now or not 0<(dt.datetime.fromisoformat(end)-dt.datetime.fromisoformat(at)).total_seconds()<=7*86400:raise InvalidEvent('invalid_event_window')
  return {'status':'validated','event_id':body['event_id']}
 with closing(sqlite3.connect(s.DB)) as c,c:
  c.execute('BEGIN IMMEDIATE');tables(c)
  fresh=json.loads(c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()[0])
  if fresh!=state:raise EventConflict('runtime_binding_changed_retry')
  prior=c.execute('SELECT event_id,payload_hash FROM external_highlight_events WHERE run_id=? AND source=? AND (event_id=? OR (uid=? AND achievement_id=?))',(state['run_id'],source,body['event_id'],body['uid'],body['achievement_id'])).fetchall()
  if prior:
   if len(prior)!=1 or prior[0]!=(body['event_id'],sha):raise EventConflict('event_or_achievement_already_exists_with_different_payload')
   duplicate=True
  else:
   if at>now or end<=now or not 0<(dt.datetime.fromisoformat(end)-dt.datetime.fromisoformat(at)).total_seconds()<=7*86400:raise InvalidEvent('invalid_event_window')
   c.execute('INSERT INTO external_highlight_events VALUES(?,?,?,?,?,?,?,?,?)',(state['run_id'],source,body['event_id'],body['uid'],body['achievement_id'],sha,encoded,s.now(),state['as_of']));duplicate=False
 return {'status':'accepted','event_id':body['event_id'],'duplicate':duplicate,'delivery_status':'not_confirmed','note':'accepted is durable receipt of the event, not WeChat delivery'}

def records(state):
 if not config().get('enabled'):return []
 with closing(sqlite3.connect(s.DB)) as c:
  if not c.execute("SELECT 1 FROM sqlite_master WHERE name='external_highlight_events'").fetchone():return []
  rows=c.execute('SELECT source,payload,received_business_at FROM external_highlight_events WHERE run_id=? AND uid=?',(state['run_id'],state['uid'])).fetchall()
 result=[]
 for source,raw,received_business_at in rows:
  p=json.loads(raw);start=max(dt.datetime.fromisoformat(p['achieved_at']),dt.datetime.fromisoformat(received_business_at))
  # Overnight notifications wait until service opens; no inference of achievement.
  if start.hour>=22:start=(start+dt.timedelta(days=1)).replace(hour=6,minute=30,second=0)
  elif (start.hour,start.minute)<(6,30):start=start.replace(hour=6,minute=30,second=0)
  candidate_at=start.isoformat(timespec='seconds')
  if p['achieved_at']>state['as_of'] or p['expires_at']<state['as_of']:continue
  key=hashlib.sha256(s.encoded([source,p['uid'],p['enrollment'],p['achievement_id']]).encode()).hexdigest()
  result.append({'id':'external-highlight-'+key,'dedupe_key':'external-highlight:'+key,'skill':TYPES[p['achievement_type']],'medal_name':p['achievement_type'],'candidate_at':candidate_at,'expires_at':p['expires_at'],'evidence':p['evidence'],'share_txt':p['caption'],'img_url':p['image']['url'],'source':source,'source_event_id':p['event_id'],'achievement_id':p['achievement_id'],'enrollment':p['enrollment'],'authority':'external_program_confirmed','trigger_status':'external_confirmed'})
 return result

def overlay(snapshot,state):
 cfg=config()
 if not cfg.get('enabled'):return snapshot
 result=dict(snapshot);result['available_highlights']=([] if cfg.get('source_mode')=='external' else list(snapshot['available_highlights']))+records(state)
 return result

def status(event_id):
 state=tm.state();source=config().get('source','external-achievements')
 if not state:raise InvalidEvent('runtime_unavailable')
 with closing(sqlite3.connect(s.DB)) as c:
  if not c.execute("SELECT 1 FROM sqlite_master WHERE name='external_highlight_events'").fetchone():return {'status':'not_found'}
  row=c.execute('SELECT payload FROM external_highlight_events WHERE run_id=? AND source=? AND event_id=?',(state['run_id'],source,event_id)).fetchone()
  if not row:return {'status':'not_found'}
  p=json.loads(row[0]);key=hashlib.sha256(s.encoded([source,p['uid'],p['enrollment'],p['achievement_id']]).encode()).hexdigest();dedupe=state['run_id']+':external-highlight:'+key
  parts=c.execute('SELECT o.id,o.message_type,o.status FROM proactive_receipts r JOIN test_outbox_parts x ON x.action_key=r.delivery_key JOIN test_outbox o ON o.id=x.operation_id WHERE r.dedupe_key=?',(dedupe,)).fetchall()
  tasks=c.execute('SELECT t.id,t.status,t.result FROM service_task_versions v JOIN service_tasks t ON t.id=v.task_id WHERE v.dedupe_key=? AND t.run_id=?',(dedupe,state['run_id'])).fetchall()
  statuses=[x[2] for x in parts];delivery='sent' if len(parts)>=2 and all(x=='sent' for x in statuses) else 'partial' if 'sent' in statuses and any(x not in ('sent','accepted','queued') for x in statuses) else 'unknown' if 'unknown' in statuses else 'failed' if 'failed' in statuses else 'accepted' if 'accepted' in statuses else 'queued' if statuses else 'not_sent'
  return {'status':'received','event_id':event_id,'delivery_status':delivery,'parts':[{'operation_id':x[0],'message_type':x[1],'status':x[2]} for x in parts],'tasks':[{'task_id':x[0],'status':x[1]} for x in tasks],'expired':state['as_of']>p['expires_at']}

if __name__=='__main__':
 import sys
 try:print(s.encoded(ingest(json.loads(sys.argv[2])) if sys.argv[1]=='ingest' else status(sys.argv[2])))
 except (InvalidEvent,EventConflict) as e:print(s.encoded({'error':str(e),'http_status':409 if isinstance(e,EventConflict) else 400}))
