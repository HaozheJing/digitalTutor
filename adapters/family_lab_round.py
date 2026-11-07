"""Create a clean round; retain every previous session and immutable receipt."""
import json,sqlite3,time
from pathlib import Path
from family_lab import encode
from family_lab_setup import prepare,write

def prepare_round(home,request):
    home=Path(home);target=home/'rounds'/request['id']
    result=prepare(target,'/home/ubuntu/digitalTutor-dsh-manual-20261029','simulation',request['as_of'],discover_legacy_contacts=False)
    db=sqlite3.connect(target/'control.sqlite');db.row_factory=sqlite3.Row
    run=dict(db.execute('SELECT * FROM runs WHERE active=1').fetchone());run['mode']=request['mode']
    families=[dict(x) for x in db.execute('SELECT * FROM families')]
    bindings=[dict(x) for x in db.execute('SELECT * FROM bindings')];db.close()
    for f in families:
        cfg=Path(f['workspace'])/'config/family-lab.json';v=json.loads(cfg.read_text())
        v.update(control_db=str(home/'control.sqlite'),public_media_directory=str(home/'public-media'))
        write(cfg,v)
    return run,families,bindings

def activate(lab,request,prepared):
    run,families,bindings=prepared
    from family_lab_scenario import build_schedule,scenario_key
    learning_script=build_schedule(families)
    with lab.transaction() as c:
        old=c.execute('SELECT * FROM runs WHERE active=1').fetchone()
        if old['id']!=request['from_run'] or not old['paused']:raise ValueError('round_changed_during_prepare')
        if c.execute("SELECT 1 FROM outbox WHERE run_id=? AND status IN ('submitting','accepted','unknown')",(old['id'],)).fetchone():raise ValueError('receipts_not_settled')
        from family_lab_contacts import setting
        roster=setting(c,'contact_roster',{}) if run['mode']=='wechat' else {}
        allowed=setting(c,'contact_authorized_routes',[])
        for binding in bindings:
            saved=roster.get(binding['family_id'])
            if saved:
                if saved['route'] not in allowed:raise ValueError('saved_contact_not_authorized')
                binding.update(nickname=saved['nickname'],route=encode(saved['route']),mode='wechat')
                if c.execute("SELECT 1 FROM outbox WHERE route=? AND status IN ('submitting','accepted','unknown')",(binding['route'],)).fetchone():raise ValueError('saved_contact_has_unresolved_receipts')
        c.execute('UPDATE runs SET active=0 WHERE id=?',(old['id'],));c.execute('UPDATE bindings SET enabled=0 WHERE run_id=?',(old['id'],))
        c.execute("UPDATE outbox SET status='cancelled',error='round_archived_no_replay' WHERE run_id=? AND status='queued'",(old['id'],))
        for table,rows in [('runs',[run]),('families',families),('bindings',bindings)]:
            for row in rows:c.execute('INSERT INTO '+table+'('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
        c.execute('INSERT INTO settings VALUES(?,?)',(scenario_key(run['id']),encode(learning_script)))
        lab.audit(c,'new_round_old_evidence_retained',{'previous':old['id'],'request':request['id']},run['id'])
    return run['id']
