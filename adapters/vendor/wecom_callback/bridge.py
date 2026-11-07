"""Consume verified inbox events and run the existing native Service in passive mode."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT/'candidates/service-wecom-v1'
STATE = ROOT/'var/wecom-autoreply'
CONFIG = ROOT/'config/wecom-callback.json'
sys.path.insert(0, str(CANDIDATE/'backend/src'))


def read_config():
    return json.loads(CONFIG.read_text())


def valid_routes(cfg):
    from digital_tutor.wecom_transport import selected_route, family_id
    if not cfg.get('auto_reply_enabled'):
        return []
    if not cfg.get('auto_reply_not_before'):
        raise ValueError('ACTIVATION_TIME_REQUIRED')
    routes = cfg.get('allowed_routes', [])
    for route in routes:
        selected_route(cfg, 'wecom:'+family_id(route))
    return routes


def classify(cfg, payload, received_at, auth_status):
    if auth_status != 'received_token_verified':
        return 'unverified', None
    if received_at < cfg['auto_reply_not_before']:
        return 'before_activation', None
    if payload.get('orgId') != cfg.get('expected_org_id'):
        return 'wrong_org', None
    # Observed provider callbacks omit isSelf for personal-WeChat senders.
    # Require consistent external sender IDs; an absent flag alone is not inbound.
    personal_sender = ('isSelf' not in payload and payload.get('contactType') == 1
        and bool(payload.get('imContactId')) and payload.get('imContactId') != payload.get('imBotId')
        and bool(payload.get('externalUserId'))
        and payload.get('externalUserId') == payload.get('customerExternalUserId'))
    if payload.get('isSelf') is not False and not personal_sender:
        return 'self_or_unknown_direction', None
    if payload.get('imRoomId'):
        return 'group_not_enabled', None
    if payload.get('source') in (1,2,3,4,5,6,7,8,9,11,12,13,14,15):
        return 'noncustomer_source', None
    matches=[r for r in cfg.get('allowed_routes', [])
             if all(payload.get(k)==r[k] for k in ('orgId','imBotId','chatId','imContactId'))]
    if len(matches)!=1:
        return 'not_allowlisted', None
    if payload.get('messageType') not in (2,6,7):
        return 'unsupported_media', None
    if not payload.get('messageId') or not isinstance(payload.get('payload'),dict):
        return 'invalid_message', None
    if payload['messageType'] in (2,6):
        return 'eligible', matches[0]
    text=payload.get('payload',{}).get('pureText') or payload.get('payload',{}).get('text')
    if not isinstance(text,str) or not text.strip() or len(text)>30000 or not payload.get('messageId'):
        return 'invalid_message', None
    return 'eligible', matches[0]


def app_open():
    from digital_tutor.application import Application
    from test_context import current
    return Application(Path(current()['path'])/'business.sqlite', mode='passive', strict_runtime=True,
                       service_start=0, service_end=24)


def initialize():
    STATE.mkdir(parents=True,exist_ok=True);STATE.chmod(0o700)
    os.environ.update(DIGITAL_TUTOR_WECOM_CONFIG=str(CONFIG), DIGITAL_TUTOR_CONTEXT_MODE='daily_native',
                      DIGITAL_TUTOR_STEERING='1', DIGITAL_TUTOR_WORKFLOW='1')
    with closing(sqlite3.connect(read_config()['database'])) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS bridge_events (
            callback_id INTEGER PRIMARY KEY, disposition TEXT NOT NULL, processed_at TEXT NOT NULL)''')


def consume(cfg):
    import media
    from test_context import current, prior_message
    session=current()
    from digital_tutor.wecom_transport import family_id
    from digital_tutor.mock_channel import channel_for
    from digital_tutor.outbox import reconcile
    with closing(sqlite3.connect(cfg['database'],timeout=5)) as inbox:
        rows=inbox.execute('''SELECT id,kind,body_json,first_received_at,status FROM callbacks
            WHERE id NOT IN (SELECT callback_id FROM bridge_events) ORDER BY id LIMIT 100''').fetchall()
        app=app_open()
        media_pending=False
        try:
            for rid,kind,raw,at,auth_status in rows:
                payload={k.strip():v for k,v in json.loads(raw).items()}
                if kind=='send_result':
                    if auth_status!='received_token_verified':
                        disposition='unverified_receipt'
                    else:
                        import replyer
                        matched=channel_for(app).observe_receipt(payload)
                        if replyer.enabled():matched=replyer.observe_receipt(payload) or matched
                        disposition='receipt_applied' if matched else 'unmatched_receipt'
                else:
                    disposition,route=classify(cfg,payload,at,auth_status)
                    if prior_message(session,rid,payload):disposition,route='previous_test_context',None
                    if disposition=='eligible':
                        import replyer
                        if replyer.enabled():
                            if payload['messageType']!=7:
                                disposition='replyer_text_only'
                            else:
                                try:
                                    replyer.receive(payload,route,rid)
                                    disposition='replyer_queued'
                                except (ValueError,TypeError,KeyError,OverflowError):
                                    disposition='invalid_timestamp'
                            with inbox:inbox.execute('INSERT INTO bridge_events VALUES(?,?,?)',
                                (rid,disposition,datetime.now(timezone.utc).isoformat()))
                            continue
                        # Preserve arrival order while perception runs out of process.
                        # Receipts are still consumed below, even behind a pending image.
                        if media_pending:continue
                        observation = None
                        if payload['messageType'] in (2,6):
                            observation=media.prepare(payload,rid)
                            if observation is None:
                                media_pending=True
                                continue
                        family=family_id(route)
                        app.create_family(family,family,'wecom:'+family,mock=False)
                        external='wecom-in-'+hashlib.sha256(json.dumps([route,payload['messageId']],sort_keys=True).encode()).hexdigest()
                        stamp=payload.get('timestamp')
                        if stamp is None:
                            disposition='missing_timestamp'
                        else:
                            try:
                                occurred=datetime.fromtimestamp(float(stamp)/1000,timezone.utc).isoformat()
                            except (ValueError,TypeError,OverflowError):
                                disposition='invalid_timestamp'
                            else:
                                token=app.open_session(family,'channel')
                                try:
                                    body,metadata=(media.inbound(observation,external) if observation is not None else
                                        (payload['payload'].get('pureText') or payload['payload']['text'],{'media_status':'text'}))
                                    app.ingest_message(token,{'external_id':external,
                                        'body':body,
                                        'occurred_at':occurred,'source_metadata':{'source_id':external,
                                        'source_alias':'wecom-live','source_row':rid,
                                        'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),
                                        'speaker_role':'family_member_unknown',**metadata}})
                                    disposition='ingested'
                                except Exception as exc:
                                    disposition='ingest_error:'+getattr(exc,'code',type(exc).__name__)
                                finally:
                                    app.revoke_session(token)
                with inbox:
                    inbox.execute('INSERT INTO bridge_events VALUES(?,?,?)',
                                  (rid,disposition,datetime.now(timezone.utc).isoformat()))
            for family in [r[0] for r in app.store.connection.execute('SELECT id FROM families')]:
                for operation in app.store.list('operation',family):
                    if operation['stage'] in ('dispatching','accepted','unknown'):
                        reconcile(app,family,operation['id'])
        finally:
            app.close()
        return media_pending


def run_once():
    import replyer
    if replyer.enabled():
        replyer.run_once()
        return
    from digital_tutor.hermes_host import HermesHost
    from digital_tutor.scheduler import Worker
    app=app_open()
    try:
        from workbench import active, atomic
        from test_context import current
        session=current()
        selected=active()
        atomic(STATE/'last-prompt.json',{'version':selected['version'],'context_id':session['id'],'at':datetime.now(timezone.utc).isoformat()})
        host=HermesHost(Path(selected['candidate']),Path(session['path'])/'runs',budget_seconds=180,context_mode='daily_native')
        result=Worker(app,host,name='wecom-native',lease_seconds=30).run_once()
        print(json.dumps(result),flush=True)
    finally:
        app.close()


def main():
    os.umask(0o077);initialize()
    if '--prepare-media' in sys.argv:
        import media
        if valid_routes(read_config()):media.run_one(read_config())
        return
    if '--run-once' in sys.argv:
        if valid_routes(read_config()):run_once()
        return
    process=None;log=None;next_launch=0;media_process=None
    try:
        while True:
            from test_context import pending, apply
            if media_process is not None and media_process.poll() is not None:
                media_process=None
            if process is not None and process.poll() is not None:
                log.close();log=None;process=None;next_launch=time.monotonic()+5
            if pending():
                if process is not None or media_process is not None:
                    (STATE/'status.json').write_text(json.dumps({'state':'reset_pending','auto_reply_active':True,'at':datetime.now(timezone.utc).isoformat()}))
                    time.sleep(1)
                    continue
                apply();next_launch=0
            cfg=read_config()
            if not valid_routes(cfg):
                state={'state':'awaiting_platform_configuration','auto_reply_active':False}
            else:
                media_pending=consume(cfg)
                if media_pending and media_process is None:
                    media_process=subprocess.Popen([sys.executable,str(Path(__file__)),'--prepare-media'],
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                if process is not None and process.poll() is not None:
                    log.close();log=None;process=None;next_launch=time.monotonic()+5
                if process is None and not media_pending and time.monotonic()>=next_launch:
                    app=app_open()
                    try:
                        import replyer
                        # Queue.claim performs all eligibility checks in the subprocess.
                        has_work=replyer.has_work() if replyer.enabled() else app.store.connection.execute("SELECT 1 FROM records WHERE kind='job' AND json_extract(data,'$.kind')='service' AND json_extract(data,'$.status') IN ('ready','waiting','leased','failed') LIMIT 1").fetchone()
                    finally:app.close()
                    if has_work:
                        log=open(STATE/'worker.log','a')
                        process=subprocess.Popen([sys.executable,str(Path(__file__)),'--run-once'],stdout=log,stderr=log)
                state={'state':'understanding_media' if media_pending else ('running' if process else 'listening'),'auto_reply_active':True}
            state['at']=datetime.now(timezone.utc).isoformat()
            (STATE/'status.json').write_text(json.dumps(state))
            time.sleep(1)
    finally:
        if media_process and media_process.poll() is None:
            media_process.terminate();media_process.wait(timeout=10)
        if process and process.poll() is None:
            process.terminate();process.wait(timeout=20)
        if log:log.close()


if __name__=='__main__':
    main()
