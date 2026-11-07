"""Run-bound native experiment bridge. No model client, business queue or timer."""
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
import sql_tools as s


def manifest():
    path = s.R / 'config/experiment.json'
    if not path.exists():
        if os.environ.get('TUTOR_EXPERIMENT_ID'):
            raise ValueError('experiment_manifest_missing')
        return None
    value = json.loads(path.read_text(encoding='utf8'))
    expected_hash = os.environ.get('TUTOR_EXPERIMENT_MANIFEST_SHA256')
    if expected_hash and hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError('experiment_manifest_changed')
    root = s.R.resolve()
    if (value.get('schema_version') != 1 or value.get('simulation') is not True
            or Path(value.get('workspace', '')).resolve() != root
            or Path(s.DB).resolve() != root / 'runtime/live.sqlite'
            or not re.fullmatch(r'exp-[a-f0-9]{32}', value.get('experiment_run_id', ''))
            or (os.environ.get('TUTOR_EXPERIMENT_ID') and os.environ['TUTOR_EXPERIMENT_ID'] != value['experiment_run_id'])):
        raise ValueError('experiment_scope_mismatch')
    for name in ['test-wechat.json', 'live-source.json', 'wecom-callback.json', 'wecom-media.json']:
        if (root / 'config' / name).exists():
            raise ValueError('experiment_external_channel_forbidden')
    return value


def verify():
    value = manifest()
    if value is None:
        return None
    actual = set()
    for folder in ['adapters', 'app', 'skills', 'knowledge', 'assets', 'config', 'runtime/fixtures']:
        for p in (s.R / folder).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p != s.R / 'config/experiment.json':
                actual.add(str(p.relative_to(s.R)))
    if actual != set(value['frozen_files']):
        raise ValueError('experiment_frozen_file_set_changed')
    for name, digest in value['frozen_files'].items():
        p = s.R / name
        if not p.resolve().is_relative_to(s.R.resolve()) or p.is_symlink() or not p.is_file():
            raise ValueError('experiment_frozen_path_invalid')
        if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError('experiment_configuration_changed:' + name)
    if os.environ.get('TUTOR_MEDIA_BINDING_SHA256'):
        __import__('experiment_media').configuration(s.R,'service')
    if os.environ.get('TUTOR_VOICE_BINDING_SHA256'):
        __import__('voice_understanding').configuration(s.R)
    import test_mode as tm
    profile_hash = os.environ.get('TUTOR_EXPERIMENT_PROFILE_SHA256')
    profile_path = s.R / 'runtime/dsh-home/profiles/web/cordis.patch.yml'
    if profile_hash and (not profile_path.is_file() or hashlib.sha256(profile_path.read_bytes()).hexdigest() != profile_hash):
        raise ValueError('experiment_native_profile_changed')
    state = tm.state()
    if not state or state['run_id'] != value['business_run_id'] or state['uid'] != value['family_uid']:
        raise ValueError('experiment_business_scope_changed')
    return value


def simulation_config():
    value = manifest()
    return {'simulation': True, 'experiment_run_id': value['experiment_run_id']} if value else None


def simulated_send(ident, body, message_type):
    value = verify()
    if value is None:
        raise ValueError('experiment_not_enabled')
    # Faults are frozen per run, not editable midway through an external request.
    outcome = value.get('transport_outcome', 'sent')
    request_id = 'sim-' + ident
    receipt = {'simulation': True, 'experiment_run_id': value['experiment_run_id'],
               'externalRequestId': ident, 'requestId': request_id,
               'messageType': message_type, 'sendCode': 0 if outcome == 'sent' else 1,
               'messageId': 'sim-message-' + ident}
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.execute('CREATE TABLE IF NOT EXISTS experiment_receipts(operation_id TEXT PRIMARY KEY,body TEXT,outcome TEXT,applied INTEGER DEFAULT 0)')
        c.execute('INSERT OR IGNORE INTO experiment_receipts VALUES(?,?,?,0)', (ident, s.encoded(receipt), outcome))
    if outcome == 'unknown':
        raise TimeoutError('simulated_unknown_transport')
    if outcome == 'failed':
        return {'errcode': 1, 'simulation': True}
    return {'errcode': 0, 'requestId': request_id, 'simulation': True}


def receive():
    value = verify()
    if value is None:
        return None
    import wechat_test as w
    with closing(sqlite3.connect(s.DB)) as c, c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='experiment_receipts'").fetchone():
            return {'simulation': True, 'applied': 0, 'more': False}
        rows = c.execute("SELECT operation_id,body FROM experiment_receipts WHERE applied=0 AND outcome='sent'").fetchall()
        for ident, body in rows:
            w.observe(c, json.loads(body))
            c.execute('UPDATE experiment_receipts SET applied=1 WHERE operation_id=?', (ident,))
    return {'simulation': True, 'applied': len(rows), 'more': False}


def snapshot():
    value = verify()
    import test_mode as tm, service_workflow as wf, message_hooks as hooks
    state = tm.state()
    if not state:
        raise ValueError('bound_run_required')
    wf.initialize()
    with closing(sqlite3.connect(s.DB)) as c:
        c.execute('BEGIN')
        def rows(sql, args=()): return wf.rows(c, sql, args)
        try: owner = wf.owner(c)
        except ValueError: owner = {'type': 'idle', 'id': None}
        tasks = wf.task_rows(c, state)
        facts = wf.facts(c, state, [x['skill'] for x in tasks])
        has_hooks = c.execute("SELECT 1 FROM sqlite_master WHERE name='message_hook_inbound'").fetchone()
        batch = rows("SELECT m.source_id,m.body,i.received_at,i.handled FROM message_hook_inbound i JOIN messages m ON m.source_id=i.source_id WHERE i.scope=? AND i.handled=0 ORDER BY m.rowid", (state['run_id'],)) if has_hooks else []
        data = {'schema_version': 1, 'generated_at': s.now(), 'business_time': state['as_of'],
                'wall_clock': s.now(), 'family_uid': state['uid'], 'run_id': state['run_id'],
                'experiment_run_id': value['experiment_run_id'] if value else None,
                'mode': owner['type'], 'owner': owner, 'batch': batch,
                'source_message_ids': [x['source_id'] for x in batch],
                'message_cursor': hooks.cursor(c), 'quiet_until': hooks.deadline(c) if has_hooks else 0,
                'facts': facts, 'tasks': tasks,
                'deliveries': rows('SELECT * FROM deliveries ORDER BY rowid'),
                'parts': rows('SELECT p.action_key,o.* FROM test_outbox_parts p JOIN test_outbox o ON o.id=p.operation_id ORDER BY o.rowid'),
                'context_token': None, 'generation_token': None,
                'authority_note': 'Display only. Native workflow_context and host begin issue action authority.',
                'simulation': bool(value)}
        if value:
            data['assembly'] = {k: value.get(k) for k in ['config_version', 'scenario_version', 'code_commit', 'harness_version', 'model', 'components', 'history_mode']}
            data['assembly']['frozen_files'] = value['frozen_files']
            data['assembly']['skill_reads'] = rows('SELECT * FROM service_skill_reads WHERE run_id=?', (state['run_id'],))
            data['tool_trace'] = rows('SELECT * FROM tool_audit WHERE run_id=? ORDER BY started_at', (state['run_id'],))
            data['receipts'] = rows('SELECT * FROM experiment_receipts') if c.execute("SELECT 1 FROM sqlite_master WHERE name='experiment_receipts'").fetchone() else []
    session = s.R / 'runtime/service-session.json'
    data['session_id'] = json.loads(session.read_text()).get('sessionId') if session.exists() else None
    data['preference_memory'] = __import__('preference_memory').status()
    if value:
        data['native_evidence'] = native_evidence(data['session_id'])
    data['snapshot_id'] = 'snapshot-' + hashlib.sha256(s.encoded(data).encode()).hexdigest()
    return data


def native_evidence(session_id):
    """Allowlisted persisted native metadata, never assistant reasoning text."""
    if not session_id: return {'status': 'not_started'}
    paths = list((s.R / 'runtime/dsh-home/sessions').glob('*/' + session_id + '/session.v4.jsonl.zstd'))
    if len(paths) != 1: return {'status': 'session_log_unavailable'}
    try:
        from compression import zstd
        with zstd.open(paths[0], 'rt') as file:
            rows = [json.loads(line) for line in file if line.strip()]
        config = json.loads((s.R / 'config/frozen-experiment-input.json').read_text())['config']
        metadata = next((x for x in rows if x['type'] == 'session'), {})
        prompts = [s.encoded(x['data']['message']) for x in rows if x['type'] == 'system/message']
        def strings(item):
            if isinstance(item, str): return [item]
            if isinstance(item, list): return [v for x in item for v in strings(x)]
            if isinstance(item, dict): return strings(list(item.values()))
            return []
        prompt_texts = ['\n'.join(strings(x['data']['message'])) for x in rows if x['type'] == 'system/message']
        usage = [x['data']['usage'] for x in rows if x['type'] == 'assistant/message' and x['data'].get('usage')]
        return {'status': 'available', 'session_id': metadata.get('id'), 'agent_preset': metadata.get('agentPreset'),
                'cwd': metadata.get('cwd'), 'models': [x['data'] for x in rows if x['type'] == 'request/context'],
                'system_prompt_sha256': [hashlib.sha256(p.encode()).hexdigest() for p in prompts],
                'persona_observed': any(config['persona'] in p for p in prompt_texts),
                'usage': usage, 'tool_names': [x['data']['name'] for x in rows if x['type'] == 'tool/call'],
                'completed_turns': sum(x['type'] == 'turn/end' for x in rows)}
    except (ImportError, OSError, EOFError, ValueError, KeyError):
        return {'status': 'session_log_not_readable_yet'}


def inbound(data):
    value = verify()
    if value is None or data.get('experiment_run_id') != value['experiment_run_id'] or data.get('run_id') != value['business_run_id'] or data.get('family_uid') != value['family_uid']:
        raise ValueError('experiment_request_scope_mismatch')
    request = data.get('request_id', '')
    body = data.get('body')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', request) or not isinstance(body, str) or (not body.strip() and data.get('audio') is None and data.get('image') is None) or len(body) > 30000:
        raise ValueError('invalid_experiment_message')
    ident = value['experiment_run_id'] + ':' + request
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        if data.get('audio') is not None and data.get('image') is not None:raise ValueError('one_media_type_per_message')
        if data.get('image') is not None:
            import image_inputs
            asset=image_inputs.register(c,s.R,{'run_id':value['business_run_id'],'uid':value['family_uid']},ident,image=data['image'])
            body=image_inputs.marker(asset)+('\n附带文字：'+body if body.strip() else '')
        if data.get('audio') is not None:
            import voice_understanding as voice
            asset=voice.register(c,s.R,{'run_id':value['business_run_id'],'uid':value['family_uid']},ident,audio=data['audio'])
            body=voice.marker(asset)+('\n附带文字：'+body if body.strip() else '')
        old = c.execute('SELECT body FROM messages WHERE source_id=?', (ident,)).fetchone()
        if old:
            if old[0] != body: raise ValueError('immutable_request_changed')
            return {'status': 'duplicate', 'source_message_id': ident, 'simulation': True}
        c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,1)', (ident, 'user', body, s.now(), 'experiment_inbound', hashlib.sha256(body.encode()).hexdigest()))
        __import__('live_runtime').enqueue(c, 'inbound:' + ident, 'passive', {'source_message_id': ident})
    return {'status': 'recorded', 'source_message_id': ident, 'simulation': True, 'quiet_seconds': 60}


if __name__ == '__main__':
    command = sys.argv[1]
    result = inbound(json.loads(sys.stdin.read(12000001) if command=='inbound-stdin' else sys.argv[2])) if command in ('inbound','inbound-stdin') else {'verify': verify, 'snapshot': snapshot, 'receive': receive}[command]()
    print(json.dumps(result, ensure_ascii=False))
