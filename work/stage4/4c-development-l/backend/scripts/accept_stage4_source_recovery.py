"""B08 real-Flash source recovery, using only external input and fault removal.

The model first encounters an actual approved-material read fault. After its
partial reply and durable debt, the runner removes only that fault's active_file.
The ordinary Worker observes the real 30-second host probe and resumes the same
job. No job edits, source-availability edits, clock jumps or new messages wake it.
Program results never substitute for independent review of the actual answers.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

from accept_stage4_passive import (RecordingHandler, drain, ledger, safe, save,
                                    sanitized_database, seed_sources, snapshot)
from digital_tutor.application import Application, Clock
from digital_tutor.hermes_host import HermesHost
from digital_tutor.mock_channel import channel_for
from digital_tutor.runtime_assembly import approval
from digital_tutor.scheduler import Worker
from digital_tutor.service_context import message_source_digest
from digital_tutor.skill_loader import _frontmatter
from digital_tutor.storage import digest, encode


CASES = {
    'knowledge': {
        'tool': 'knowledge.read', 'fault': 'knowledge_unavailable', 'error': 'KNOWLEDGE_UNAVAILABLE',
        'body': 'L2《了不起的狐狸爸爸》第1天主要讲什么？我想陪孩子聊两句，可以怎么开始？',
    },
    'guide': {
        'tool': 'skills.read', 'fault': 'required_guide_unavailable', 'error': 'SKILL_MISSING',
        'body': '我想陪孩子读L2《了不起的狐狸爸爸》第1天。请帮我把这节课的陪读方法讲清楚，便于我和孩子聊几句。',
    },
}
RUNTIME_KINDS = ('runtime_material_wait', 'runtime_message_read', 'runtime_cursor')
EXPECTED_MODEL = 'qwen3.8-flash'
IDLE_SCANS = 20


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def paths(project, run, case):
    return (project / 'state/backend-stage4' / run / case,
            project / 'evaluations/backend-stage4' / run / case)


def fault_hooks(state, case):
    spec = CASES[case]
    return {'before:' + spec['tool']: {'fault': spec['fault'],
            'active_file': str((state / 'source-unavailable.flag').resolve())}}


def load_calls(out):
    return [json.loads(path.read_text(encoding='utf-8')) for path in sorted(out.glob('model-*.json'))]


def open_runtime(project, state, out, case, approved, budget_seconds):
    app = Application(state / 'business.sqlite', Clock(shared_path=state / 'clock.json'),
                      mode='passive', strict_runtime=True)
    families = [row['id'] for row in app.store.connection.execute('SELECT id FROM families ORDER BY id')]
    host = HermesHost(project, state / 'runs', budget_seconds, approved_materials=approved,
                      test_hooks=fault_hooks(state, case))
    handler = RecordingHandler(host, app, families, out)
    handler.calls = load_calls(out)  # Preserve numbering across real process restarts.
    return app, families, handler, Worker(app, handler, lease_seconds=9)


def runtime_records(app, families):
    return {family: {kind: safe(app.store.list(kind, family)) for kind in RUNTIME_KINDS}
            for family in families}


def observation(app, families, handler):
    records = snapshot(app, families)
    return {'observed_at': utc_now(), 'process_id': os.getpid(),
            'business_time': app.clock.iso(),
            'family_versions': {family: app.store.family(family)['version'] for family in families},
            'model_runs': len(handler.calls), 'channel_count': channel_for(app).count(),
            'channel_digest': digest(ledger(app)), 'business_records_digest': digest(records),
            'inbound_sources': {message['id']: message_source_digest(message)
                for family in families for message in app.store.list('message', family)
                if message['direction'] == 'inbound'}}


def export_channel(app, target):
    """Independent channel backup; redact capabilities only in the exported copy."""
    if target.exists():
        raise FileExistsError(target)
    with sqlite3.connect(target) as copy:
        channel_for(app).db.backup(copy)
        for operation_id, payload in copy.execute('SELECT operation_id,payload FROM delivery').fetchall():
            copy.execute('UPDATE delivery SET payload=? WHERE operation_id=?',
                         (encode(safe(json.loads(payload))), operation_id))
        copy.commit()
        copy.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    target.chmod(0o600)


def checkpoint(app, families, handler, target):
    target.mkdir(parents=True, exist_ok=False)
    save(target / 'observation.json', observation(app, families, handler))
    save(target / 'records.json', snapshot(app, families))
    save(target / 'runtime-records.json', runtime_records(app, families))
    save(target / 'channel-ledger.json', ledger(app))
    sanitized_database(app, target / 'database.redacted.sqlite')
    export_channel(app, target / 'channel.sqlite')


def same_business(before, after):
    return all(before[key] == after[key] for key in (
        'family_versions', 'model_runs', 'channel_count', 'channel_digest',
        'business_records_digest', 'inbound_sources'))


def idle_scans(worker, app, families, handler, target):
    target.mkdir(parents=True, exist_ok=False)
    before = observation(app, families, handler)
    records = []
    for index in range(IDLE_SCANS):
        result = worker.run_once()
        records.append({'index': index + 1, 'worker': result,
                        'observation': observation(app, families, handler),
                        'runtime_records': runtime_records(app, families)})
        if result['ran']:
            # Preserve a real unexpected execution; do not repeat it to fill a quota.
            break
    after = observation(app, families, handler)
    checks = {'twenty_scans': len(records) == IDLE_SCANS,
              'zero_runnable_work': all(not item['worker']['ran'] for item in records),
              'zero_new_model': before['model_runs'] == after['model_runs'],
              'zero_new_channel_effect': before['channel_digest'] == after['channel_digest'],
              'zero_family_business_bump': before['family_versions'] == after['family_versions'],
              'business_records_unchanged': before['business_records_digest'] == after['business_records_digest'],
              'no_new_or_changed_inbound': before['inbound_sources'] == after['inbound_sources']}
    value = {'checks': checks, 'passed': all(checks.values()), 'before': before,
             'scans': records, 'after': after}
    save(target / 'scans.json', value)
    return value


def restart_probe(project, args):
    """Child process reopens the same persisted business/channel databases."""
    state, out = paths(project, args.run, args.case)
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    approved = approval(project)
    if approved != manifest['approved_materials']:
        raise RuntimeError('Approved material pin changed before restart probe')
    app, families, handler, worker = open_runtime(project, state, out, args.case, approved,
                                                 manifest['budget_seconds'])
    try:
        result = idle_scans(worker, app, families, handler, out / ('restart-' + args.restart_probe))
        result.update(process_id=os.getpid(), parent_process_id=os.getppid(),
                      restart_kind='new_python_process_same_persisted_databases',
                      fault_active=(state / 'source-unavailable.flag').exists())
        save(out / ('restart-' + args.restart_probe + '.json'), result)
        checkpoint(app, families, handler, out / ('checkpoint-restart-' + args.restart_probe))
        return 0 if result['passed'] else 1
    finally:
        app.close()


def run_restart(project, run, case, phase, args):
    state, out = paths(project, run, case)
    command = [sys.executable, str(Path(__file__).resolve()), '--run', run, '--case', case,
               '--restart-probe', phase]
    save(out / ('restart-request-' + phase + '.json'),
         {'requested_at': utc_now(), 'parent_process_id': os.getpid(),
          'state_root': str(state), 'command': command})
    completed = subprocess.run(command, cwd=project, timeout=args.budget_seconds + 90, check=False)
    result = json.loads((out / ('restart-' + phase + '.json')).read_text(encoding='utf-8'))
    result['process_exit_code'] = completed.returncode
    result['independent_process'] = result['process_id'] != os.getpid()
    return result


def message_debt(app, family, message_id):
    message = app.store.get('message', message_id, family)
    return message, [intent for intent in message['intents']
                     if intent['requires_reply'] and not intent['reply_operation_refs']]


def call_traces(calls):
    return [trace for call in calls for trace in call['native'].get('tool_trace', [])]


def source_probe_evidence(app, family, approved):
    rows = app.store.connection.execute(
        "SELECT id,version,data FROM history WHERE kind='runtime_material_wait' AND family_id=? ORDER BY id,version",
        (family,)).fetchall()
    history = [{'id': row['id'], 'version': row['version'], **json.loads(row['data'])} for row in rows]
    previous = {}; probes = []
    for current in history:
        prior = previous.get(current['id'])
        if current.get('status') == 'available' and prior and prior.get('status') == 'unavailable':
            # observe() records next_probe_at = real host observation time + 30s.
            # This is an audit of its existing timestamps, never a clock mutation.
            at = datetime.fromisoformat(current['next_probe_at']) - timedelta(seconds=30)
            expected = (approved['knowledge_sha256'] if current['tool'] == 'knowledge.read'
                        else digest(approved['files']))
            probes.append({'wait_id': current['id'], 'version': current['version'],
                           'tool': current['tool'], 'scheduled_not_before': prior['next_probe_at'],
                           'observed_at': at.isoformat(),
                           'not_early': at >= datetime.fromisoformat(prior['next_probe_at']),
                           'same_approved_source': current.get('source_fingerprint') == expected,
                           'source_fingerprint': current.get('source_fingerprint')})
        previous[current['id']] = current
    return {'history': history, 'successful_probe_transitions': probes}


def initial_checks(app, family, message_id, job_id, handler, spec, approved):
    message, debt = message_debt(app, family, message_id)
    waits = [wait for wait in app.store.list('runtime_material_wait', family) if wait['job_id'] == job_id]
    operations = {op['id']: op for op in app.store.list('operation', family)}
    partial_ids = {oid for intent in debt for oid in intent.get('partial_reply_operation_refs', [])}
    outcomes = [outcome for outcome in app.store.list('run_outcome', family) if outcome['job_id'] == job_id]
    checks = {
        'real_fault_observed': any(t['tool'] == spec['tool'] and not t['ok'] and
                                  t.get('error') == spec['error'] for t in call_traces(handler.calls)),
        'fault_has_durable_dependency': any(w['status'] == 'unavailable' and w['tool'] == spec['tool']
                                            and w.get('approval_fingerprint') == digest(approved) for w in waits),
        'original_job_waiting_without_retry_timer': app.store.get('job', job_id, family)['status'] == 'waiting'
            and app.store.get('job', job_id, family).get('not_before') is None,
        'original_intents_remain_unanswered': message['interpreted'] and bool(debt),
        'partial_for_each_remaining_intent': bool(debt) and all(intent.get('partial_reply_operation_refs') for intent in debt),
        'partial_actually_sent': bool(partial_ids) and all(oid in operations and
            operations[oid]['stage'] in {'sent', 'delivered'} and channel_for(app).query(oid) in {'sent', 'delivered'}
            for oid in partial_ids),
        'debt_and_resume_condition_persisted': any(o['outcome'] in {'waiting', 'needs_input'}
            and o.get('remaining_work') and o.get('resume_condition') for o in outcomes),
    }
    return checks, {'message': message, 'debt': debt, 'partial_operation_ids': sorted(partial_ids),
                    'material_waits': waits, 'outcomes': outcomes}


def successful_body(trace):
    if not trace.get('ok'):
        return {}
    return trace.get('result', {}).get('result', {})


def valid_knowledge(trace, approved):
    body = successful_body(trace)
    return (trace['tool'] == 'knowledge.read' and body.get('document_id') == 'LESSON-L2-FOX-D01'
            and body.get('complete_body') is True and body.get('content_sha256') == approved['knowledge_sha256']
            and isinstance(body.get('content'), str)
            and hashlib.sha256(body['content'].encode('utf-8')).hexdigest() == approved['knowledge_sha256'])


def valid_skill(trace, approved):
    body = successful_body(trace)
    if trace['tool'] != 'skills.read' or body.get('canonical_skill_id') != 'explain-lesson':
        return False
    content = body.get('content')
    expected = approved['files']['agent/skills/explain-lesson/SKILL.md']
    if (not isinstance(content, str) or body.get('sha256') != expected or
            hashlib.sha256(content.encode('utf-8')).hexdigest() != expected or
            body.get('native_reader') != 'Hermes.skill_view(preprocess=False)'):
        return False
    _, _, required = _frontmatter(content)
    guides = body.get('required_guides', [])
    return ({g['guide_id'] for g in guides} == {name.strip() for name in required}
            and all(g.get('sha256') == approved['files'].get('agent/guides/' + g['guide_id'] + '.md')
                and hashlib.sha256(g.get('content', '').encode('utf-8')).hexdigest() == g.get('sha256') for g in guides))


def await_recovery(worker, app, family, message_id, handler, out, args):
    target = out / 'recovery'; target.mkdir()
    started = time.monotonic()
    before = len(handler.calls)
    polls = []
    while time.monotonic() - started < args.recovery_wait_seconds:
        poll_out = target / ('poll-%03d' % len(polls)); poll_out.mkdir()
        events = drain(worker, app, [family], poll_out, 1, wait_cap=0)
        polls.append({'index': len(polls) + 1, 'at': utc_now(),
                      'elapsed_seconds': round(time.monotonic() - started, 3),
                      'ran': bool(events and events[-1]['worker']['ran']),
                      'runtime_records': runtime_records(app, [family])})
        message, debt = message_debt(app, family, message_id)
        if message['interpreted'] and not debt:
            break
        waits = app.store.list('runtime_material_wait', family)
        # If a resumed model left debt but no recoverable failed source remains,
        # preserve that result instead of manufacturing another wake-up.
        if len(handler.calls) > before and not any(w['status'] in {'unavailable', 'available'} for w in waits):
            break
        if len(polls) == 1 or len(polls) % 5 == 0:
            print(json.dumps({'event': 'awaiting_host_source_probe', 'case': out.name,
                              'elapsed_seconds': polls[-1]['elapsed_seconds'],
                              'model_runs': len(handler.calls)}, ensure_ascii=False), flush=True)
        time.sleep(min(2, max(0, args.recovery_wait_seconds - (time.monotonic() - started))))
    save(target / 'polls.json', polls)
    return polls


def run_case(project, run, case, args, approved):
    state, out = paths(project, run, case)
    state.mkdir(parents=True, exist_ok=False); out.mkdir(parents=True, exist_ok=False)
    state.chmod(0o700); out.chmod(0o700)
    spec = CASES[case]
    inputs = {'families': ['a'], 'case': case, 'business_time': '2026-10-04T10:00:00+08:00',
              'classification': 'synthetic_family_mock_channel', 'material_chain': 'R_same_approved_pin',
              'rounds': [[['a', spec['body']]]],
              'learning_source': {'a': [('unknown', 'unknown')]}}
    oracle = {'not_model_input': True, 'case': case, 'source_only_recovery': True,
              'expected_model': EXPECTED_MODEL, 'same_original_job_and_intents': True,
              'partial_then_final_required': True, 'idle_scans_per_phase': IDLE_SCANS,
              'semantic_review_status': 'not_reviewed',
              'semantic_questions': ['故障期是否只说明实际缺口并保留原问？',
                  '恢复后是否依据实际重读的同一批准正文与规范完整回答原问？',
                  '有没有重复缺口说明、虚构未来交付、降低方法条件或误清其他责任？']}
    save(out / 'inputs.json', inputs); save(out / 'oracle.json', oracle)
    save(state / 'clock.json', {'now': inputs['business_time']})
    flag = state / 'source-unavailable.flag'
    flag.write_text('Isolated source-read fault is active.\n', encoding='utf-8'); flag.chmod(0o600)
    manifest = {'stage': '4B-B08', 'case': case, 'execution_host': 'server', 'real_channel': False,
                'real_model': not args.prepare_only, 'semantic_status': 'not_reviewed',
                'approved_materials': approved, 'state_root': str(state), 'expected_model': EXPECTED_MODEL,
                'budget_seconds': args.budget_seconds, 'recovery_wait_seconds': args.recovery_wait_seconds,
                'test_hooks': fault_hooks(state, case), 'fault_sha256': hashlib.sha256(flag.read_bytes()).hexdigest(),
                'input_sha256': hashlib.sha256((out / 'inputs.json').read_bytes()).hexdigest(),
                'oracle_sha256': hashlib.sha256((out / 'oracle.json').read_bytes()).hexdigest(),
                'source_sha256': {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for directory in ('backend/src', 'backend/scripts', 'agent/prompts')
                    for p in (project / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts}}
    save(out / 'manifest.json', manifest)
    app = Application(state / 'business.sqlite', Clock(shared_path=state / 'clock.json'),
                      mode='passive', strict_runtime=True)
    checks = {}; phase_evidence = {}; handler = None
    try:
        families = list(seed_sources(app, inputs).values()); family = families[0]
        save(out / 'source-rows.json', [dict(row) for row in app.store.connection.execute('SELECT * FROM learning_days')])
        if args.prepare_only:
            result = {'case': case, 'prepared_only': True, 'model_runs': 0, 'gate_passed': False}
            save(out / 'result.json', result)
            return result
        channel = app.open_session(family, 'channel')
        try:
            message = app.ingest_message(channel, {'external_id': case + '-original',
                'body': spec['body'], 'occurred_at': app.clock.iso()})['data']
        finally:
            app.revoke_session(channel)
        original_job_id = 'message:' + message['id']
        original_input = message_source_digest(message)
        app.close(); app = None
        app, families, handler, worker = open_runtime(project, state, out, case, approved, args.budget_seconds)
        initial = out / 'fault-active'; initial.mkdir()
        drain(worker, app, families, initial, args.max_worker_steps, wait_cap=0)
        first_checks, debt_evidence = initial_checks(app, family, message['id'], original_job_id,
                                                   handler, spec, approved)
        checks.update({'initial_' + key: value for key, value in first_checks.items()})
        save(out / 'initial-debt.json', debt_evidence)
        checkpoint(app, families, handler, out / 'checkpoint-initial-debt')
        initial_call_count = len(handler.calls)
        released = False
        if all(first_checks.values()):
            idle = idle_scans(worker, app, families, handler, out / 'idle-fault-active')
            checks['fault_active_twenty_idle_scans'] = idle['passed']
            before = observation(app, families, handler)
            app.close(); app = None
            restarted = run_restart(project, run, case, 'fault-active', args)
            app, families, handler, worker = open_runtime(project, state, out, case, approved, args.budget_seconds)
            checks['fault_active_process_restart_stable'] = (restarted['passed'] and restarted['independent_process']
                and restarted['process_exit_code'] == 0 and same_business(before, observation(app, families, handler)))
            phase_evidence['fault_active_restart'] = restarted
            if checks['fault_active_twenty_idle_scans'] and checks['fault_active_process_restart_stable']:
                if approval(project) != approved or hashlib.sha256(flag.read_bytes()).hexdigest() != manifest['fault_sha256']:
                    raise RuntimeError('Source approval or isolated fault changed unexpectedly')
                before_release = observation(app, families, handler)
                # The sole recovery stimulus: remove the actual external fault marker.
                # Never touch job, material-wait, source flags, input versions or clocks.
                flag.unlink()
                released = True
                save(out / 'fault-removal.json', {'at': utc_now(), 'action': 'unlink_active_file_only',
                    'path': str(flag), 'before': before_release,
                    'after': observation(app, families, handler),
                    'pending_host_probes': runtime_records(app, families)})
                checks['fault_removal_no_business_change'] = same_business(before_release, observation(app, families, handler))
                await_recovery(worker, app, family, message['id'], handler, out, args)
                checkpoint(app, families, handler, out / 'checkpoint-after-recovery')
                idle = idle_scans(worker, app, families, handler, out / 'idle-after-recovery')
                checks['recovered_twenty_idle_scans'] = idle['passed']
                before = observation(app, families, handler)
                app.close(); app = None
                restarted = run_restart(project, run, case, 'after-recovery', args)
                app, families, handler, worker = open_runtime(project, state, out, case, approved, args.budget_seconds)
                checks['recovered_process_restart_stable'] = (restarted['passed'] and restarted['independent_process']
                    and restarted['process_exit_code'] == 0 and same_business(before, observation(app, families, handler)))
                phase_evidence['recovered_restart'] = restarted
        checks['actual_fault_removed_after_durable_debt'] = released
        final_message, debt = message_debt(app, family, message['id'])
        final_job = app.store.get('job', original_job_id, family)
        final_intents = {intent['intent_id']: intent for intent in final_message['intents']}
        original_debt = debt_evidence['debt']
        final_operation_ids = {oid for intent in original_debt for oid in
            final_intents.get(intent['intent_id'], {}).get('reply_operation_refs', [])}
        operations = {op['id']: op for op in app.store.list('operation', family)}
        resumed = handler.calls[initial_call_count:]
        probes = source_probe_evidence(app, family, approved)
        save(out / 'source-probe-evidence.json', probes)
        checks.update({
            'same_original_job_all_native_runs': bool(handler.calls) and all(c['job_id'] == original_job_id for c in handler.calls),
            'same_original_input_no_new_message': [m['id'] for m in app.store.list('message', family)
                if m['direction'] == 'inbound'] == [message['id']] and message_source_digest(final_message) == original_input,
            'real_flash_worker_only': bool(handler.calls) and all(c['job_kind'] == 'service'
                and c['native'].get('model') == EXPECTED_MODEL for c in handler.calls),
            'native_runs_completed': bool(handler.calls) and all(c['native'].get('completed') is True for c in handler.calls),
            'automatic_source_recovery_attempt_recorded': 1 <= final_job.get('source_recovery_attempt', 0) <= 3,
            'actual_scheduled_host_probe_of_same_pin': bool(probes['successful_probe_transitions']) and all(
                probe['not_early'] and probe['same_approved_source'] for probe in probes['successful_probe_transitions']),
            'resumed_native_run_exists': bool(resumed),
            'original_job_and_question_complete': final_job['status'] == 'succeeded' and final_message['interpreted'] and not debt,
            'original_partial_refs_preserved_and_final_added': bool(original_debt) and all(
                bool(final_intents.get(intent['intent_id'], {}).get('reply_operation_refs')) and
                set(intent.get('partial_reply_operation_refs', [])) <= set(final_intents.get(intent['intent_id'], {}).get('partial_reply_operation_refs', []))
                for intent in original_debt),
            'new_final_reply_not_reused_partial': bool(final_operation_ids) and
                final_operation_ids.isdisjoint(debt_evidence['partial_operation_ids']),
            'final_replies_actually_sent': bool(final_operation_ids) and all(oid in operations and
                operations[oid]['stage'] in {'sent', 'delivered'} and channel_for(app).query(oid) in {'sent', 'delivered'} for oid in final_operation_ids),
            'same_pin_after_recovery': approval(project) == approved,
            'no_planner_or_formal_event_created': not app.store.list('plan', family) and not app.store.list('event', family),
            'at_most_one_dispatch_per_operation': all(op['dispatch_count'] <= 1 for op in operations.values()),
            'inactive_capabilities': app.store.connection.execute('SELECT count(*) FROM sessions WHERE active=1').fetchone()[0] == 0,
        })
        final_runs = {operations[oid]['run_id'] for oid in final_operation_ids if oid in operations}
        final_calls = [call for call in resumed if call['native'].get('run_id') in final_runs]
        checks['every_final_run_reread_same_approved_body_and_native_guides'] = bool(final_calls) and len(final_calls) == len(final_runs) and all(
            any(valid_knowledge(t, approved) for t in call['native'].get('tool_trace', [])) and
            any(valid_skill(t, approved) for t in call['native'].get('tool_trace', [])) for call in final_calls)
        save(out / 'transition-evidence.json', {'original_job_id': original_job_id,
            'original_message_id': message['id'], 'initial_model_runs': initial_call_count,
            'initial_debt': debt_evidence, 'final_message': final_message, 'final_job': final_job,
            'final_operation_ids': sorted(final_operation_ids), 'phases': phase_evidence})
        traces = call_traces(handler.calls)
        save(out / 'tool-trace.json', traces)
        save(out / 'sources-read.json', [t for t in traces if t['tool'].startswith(('knowledge.', 'skills.'))])
        save(out / 'records.json', snapshot(app, families))
        save(out / 'runtime-records.json', runtime_records(app, families))
        save(out / 'channel-ledger.json', ledger(app))
        sanitized_database(app, out / 'database.redacted.sqlite')
        export_channel(app, out / 'channel.sqlite')
        result = {'case': case, 'program_checks': checks, 'program_passed': all(checks.values()),
                  'semantic_status': 'not_reviewed', 'gate_passed': False,
                  'model_runs': len(handler.calls), 'api_calls': sum(c['native'].get('api_calls') or 0 for c in handler.calls),
                  'original_job_id': original_job_id, 'original_message_id': message['id'],
                  'unanswered_intent_ids': [intent['intent_id'] for intent in debt],
                  'independent_channel_count': channel_for(app).count()}
        save(out / 'result.json', result)
        return result
    except Exception as error:
        save(out / 'runner-error.json', {'type': type(error).__name__, 'message': str(error),
             'at': utc_now(), 'checks_so_far': checks, 'historical_evidence_preserved': True})
        if app is not None and handler is not None:
            checkpoint(app, families, handler, out / 'checkpoint-runner-error')
        raise
    finally:
        if app is not None:
            app.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--case', choices=['all', *CASES], default='all')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--budget-seconds', type=int, default=300)
    parser.add_argument('--max-worker-steps', type=int, default=4)
    parser.add_argument('--recovery-wait-seconds', type=int, default=120)
    parser.add_argument('--restart-probe', choices=['fault-active', 'after-recovery'], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', args.run):
        parser.error('Use a new simple run identifier')
    if not 1 <= args.budget_seconds <= 900 or not 1 <= args.max_worker_steps <= 10 or not 35 <= args.recovery_wait_seconds <= 600:
        parser.error('Invalid bounded execution budget')
    project = Path(__file__).resolve().parents[2]
    if args.restart_probe:
        if args.case == 'all' or args.prepare_only:
            parser.error('Internal restart probe requires one existing case')
        return restart_probe(project, args)
    selected = list(CASES) if args.case == 'all' else [args.case]
    for case in selected:
        if any(path.exists() for path in paths(project, args.run, case)):
            parser.error('Historical state/evidence exists; choose a new run, never overwrite it')
    approved = approval(project)
    results = []
    for case in selected:
        result = run_case(project, args.run, case, args, approved)
        results.append(result)
        print(json.dumps(safe(result), ensure_ascii=False), flush=True)
    summary = {'stage': '4B-B08', 'cases': results, 'program_only': True,
               'real_model_calls_authorized': not args.prepare_only,
               'semantic_status': 'not_reviewed', 'gate_passed': False}
    save(project / 'evaluations/backend-stage4' / args.run / ('summary-' + args.case + '.json'), summary)
    return 0 if args.prepare_only or all(result.get('program_passed') for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
