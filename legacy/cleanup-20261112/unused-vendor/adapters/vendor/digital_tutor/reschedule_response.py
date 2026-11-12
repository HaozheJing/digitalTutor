"""Current delivered proposal to source-bound response.

The host validates provenance. The model remains responsible for acceptance meaning.
"""
from typing import Literal
from types import SimpleNamespace
from pydantic import Field
from .domain import Strict, ID, Positive, require, timestamp
from .storage import digest
from .report_contract import message_source_digest

ORIGIN = 'delivered_change_response_v1'


class ChangeResponse(Strict):
    request_id: ID
    expected_family_version: Positive
    event_id: ID
    change_id: ID
    proposal_operation_id: ID
    source_message_id: ID
    source_version: Positive
    source_intent_id: ID
    source_excerpt: str = Field(min_length=1, max_length=10000)
    reviewed_intent_ids: list[ID] = Field(min_length=1, description='已核对本条完整消息的全部已解释意图ID，不能只复核其中一个问题')
    interpretation: Literal['accept', 'decline', 'unclear', 'unrelated'] = Field(
        description='完整来信相对原提议的全局解释，须考虑全部意图、否定和限定；unrelated不生成接受凭证或回复权限')
    reason: str = Field(min_length=1, max_length=2000)


def proposal_snapshot(app, family, event_id, change_id):
    event = app.event(family, event_id)
    require(event['status'] not in {'done', 'cancelled'}, 'CHANGE_NOT_APPLICABLE', '已结束事项不可由残留提议改约')
    change = event.get('proposed_change') or {}
    require(change.get('contract_kind') == 'source_bound_reschedule_v1',
            'CHANGE_CONTRACT_REQUIRED', '实际提议必须具有完整来源、原约定和新步骤合同')
    require(change.get('change_id') == change_id and change.get('status') == 'proposed',
            'CHANGE_SUPERSEDED', '仅引用当前尚未生效的原事项提议')
    return {'event_id': event_id, 'change_id': change_id, 'proposal': change,
            'proposal_digest': digest(change), 'requirement_revision': event['requirement_revision']}


def delivered_proposal(app, family, event_id, change_id, operation_id):
    snapshot = proposal_snapshot(app, family, event_id, change_id)
    operation = app.store.get('operation', operation_id, family)
    require(operation.get('stage') in {'sent', 'delivered'},
            'PROPOSAL_NOT_SENT', '未知、已准备或已发起都不是提议实际送达')
    require(operation.get('recipient') == app.store.family(family)['recipient']
            and operation.get('proposal_snapshot') == snapshot,
            'CHANGE_SUPERSEDED', '提议操作必须绑定本家庭仍有效的完整提议快照')
    outbound = next((m for m in app.store.list('message', family)
                     if m.get('direction') == 'outbound' and m.get('operation_id') == operation_id), None)
    require(outbound is not None, 'PROPOSAL_NOT_SENT', '缺少实际外发消息与操作关系')
    return snapshot, operation, outbound


def sequence(app, family, message_id):
    row = app.store.connection.execute('SELECT seq FROM message_index WHERE family_id=? AND message_id=?',
                                       (family, message_id)).fetchone()
    require(row is not None, 'SOURCE_ORDER_UNKNOWN', '缺少原消息接收顺序')
    return row[0]


def register_change_response(app, token, args):
    cmd = ChangeResponse.model_validate(args)
    def action(family, binding):
        app.check_version(family, cmd.expected_family_version)
        require(binding.get('run_id') and binding.get('job_kind') == 'service',
                'REPLY_SCOPE', '改约回应必须属于本次实际来信处理运行')
        allowed = binding.get('reply_scope', {}).get(cmd.source_message_id, [])
        require(cmd.interpretation == 'unrelated' or '*' in allowed or cmd.source_intent_id in allowed,
                'REPLY_SCOPE', '不能借另一来信或意图登记接受')
        message = app.store.get('message', cmd.source_message_id, family)
        require(message['direction'] == 'inbound' and message['version'] == cmd.source_version,
                'EVIDENCE_SUPERSEDED', '必须读取当前入站来源')
        require(message['body'] == cmd.source_excerpt, 'SOURCE_EXCERPT_INCOMPLETE', '保存完整原话而非肯定片段')
        intent = next((i for i in message.get('intents', []) if i['intent_id'] == cmd.source_intent_id), None)
        require(message.get('interpreted') and intent, 'REPLY_SCOPE', '须先解释完整来信')
        require(len(cmd.reviewed_intent_ids) == len(set(cmd.reviewed_intent_ids)) and
                set(cmd.reviewed_intent_ids) == {i['intent_id'] for i in message['intents']},
                'CHANGE_RESPONSE_COVERAGE', '解释须覆盖本条消息全部意图，不能借无关问答跳过否定改约')
        require(cmd.interpretation == 'unrelated' or (intent['requires_reply'] and not intent['reply_operation_refs']),
                'REPLY_SCOPE', '实质回应须是当前未答意图；历史无关复核只记录来源，不取得答复权限')
        from .service_context import require_reply_messages_read
        require_reply_messages_read(app, family, SimpleNamespace(reply_to=[SimpleNamespace(
            message_id=cmd.source_message_id, intent_id=cmd.source_intent_id)]), binding)
        snapshot, operation, outbound = delivered_proposal(app, family, cmd.event_id, cmd.change_id, cmd.proposal_operation_id)
        require(sequence(app, family, message['id']) > sequence(app, family, outbound['id']),
                'RESPONSE_PRECEDES_PROPOSAL', '旧来信不能因时间戳或肯定措辞成为新提议的接受')
        require(outbound.get('received_at') and
                timestamp(message['occurred_at']) >= timestamp(outbound['received_at']),
                'RESPONSE_PRECEDES_PROPOSAL', '延迟收到的旧消息不能作为后来送达提议的回应')
        source_seq = sequence(app, family, message['id'])
        source_digest = message_source_digest(message)
        require(cmd.interpretation == 'unrelated' or all(r.get('source_seq', 0) < source_seq
                    or (r.get('source_message_ref', {}).get('id') == message['id']
                        and r.get('source_seq') == source_seq and r.get('source_message_digest') != source_digest)
                    for r in app.store.list('change_response', family) if r.get('change_id') == cmd.change_id
                    and r.get('interpretation') != 'unrelated'),
                'RESPONSE_SUPERSEDED', '较旧或同一条来源不能覆盖已经登记的后续回应')
        identity = 'change-response:' + digest([family, cmd.change_id, message['id'], cmd.source_intent_id, source_digest])
        require(not any(r['id'] == identity for r in app.store.list('change_response', family)),
                'RESPONSE_ALREADY_RECORDED', '该回应已经登记；重复调用使用原request_id')
        record = {'event_id': cmd.event_id, 'change_id': cmd.change_id,
                  'proposal_operation_id': operation['id'], 'proposal_snapshot': snapshot,
                  'source_message_ref': {'id': message['id'], 'version': message['version']},
                  'source_message_digest': message_source_digest(message),
                  'source_intent_id': cmd.source_intent_id, 'source_excerpt': message['body'],
                  'reviewed_intent_ids': sorted(cmd.reviewed_intent_ids),
                  'source_seq': source_seq,
                  'interpretation': cmd.interpretation, 'reason': cmd.reason,
                  'registered_at': app.clock.iso(), 'origin_run_id': binding['run_id']}
        # A later response does not silently retain an unconsumed older acceptance.
        for old in app.store.list('evidence', family):
            if (cmd.interpretation != 'unrelated' and old.get('source_origin') == ORIGIN
                    and old.get('change_id') == cmd.change_id and old.get('valid', True)):
                app.store.put('evidence', old['id'], family, {**old, 'valid': False,
                    'superseded_by_response_id': identity}, old['version'])
        saved = app.store.put('change_response', identity, family, record)
        evidence = None
        if cmd.interpretation == 'accept':
            evidence = app.store.put('evidence', identity + ':accept', family, {
                'kind': 'accept_change', 'source_origin': ORIGIN, 'trusted': True, 'valid': True,
                'event_id': cmd.event_id, 'change_id': cmd.change_id,
                'response_ref': {'id': saved['id'], 'version': saved['version']}, **record})
        return {'response': saved, 'evidence': evidence,
                'response_record_ref': {'record_kind': 'change_response', 'id': saved['id'],
                                        'version': saved['version'], 'usable_as_report_evidence': False},
                'acceptance_evidence_ref': ({'kind': 'evidence', 'id': evidence['id'],
                                             'version': evidence['version']} if evidence else None),
                'effective_change_applied': False}
    return app.write(token, {'service'}, 'register_change_response', cmd.model_dump(), action)


def validate_change_evidence(app, family, evidence, for_consumption=False):
    if evidence.get('source_origin') != ORIGIN:
        return
    require(evidence.get('trusted') and evidence.get('kind') == 'accept_change'
            and evidence.get('interpretation') == 'accept', 'EVIDENCE_INVALID', '必须是受控来源接受凭证')
    response = app.store.get('change_response', evidence['response_ref']['id'], family)
    require(response['version'] == evidence['response_ref']['version'],
            'EVIDENCE_SUPERSEDED', '原回应记录已改变')
    from .scheduling import _versioned_record
    message = _versioned_record(app, family, 'message', evidence['source_message_ref'])
    require(message_source_digest(message) == evidence['source_message_digest']
            and response.get('source_message_digest') == evidence['source_message_digest']
            and response.get('proposal_snapshot') == evidence['proposal_snapshot'],
            'EVIDENCE_SUPERSEDED', '接受凭证须保留原回应及原话历史')
    operation = app.store.get('operation', evidence['proposal_operation_id'], family)
    outbound = next((m for m in app.store.list('message', family)
                     if m.get('direction') == 'outbound' and m.get('operation_id') == operation['id']), None)
    require(operation.get('stage') in {'sent', 'delivered'} and outbound
            and operation.get('recipient') == app.store.family(family)['recipient']
            and operation.get('proposal_snapshot') == evidence['proposal_snapshot'],
            'PROPOSAL_NOT_SENT', '原接受须对应本家庭实际送达的原提议')
    require(
            sequence(app, family, message['id']) > sequence(app, family, outbound['id']) and
            timestamp(message['occurred_at']) >= timestamp(outbound['received_at']),
            'RESPONSE_PRECEDES_PROPOSAL', '原回应须发生并接收于提议实际送达之后')
    event = app.event(family, evidence['event_id'])
    consumed = next((h for h in event.get('schedule_history', [])
                     if h.get('change_id') == evidence['change_id'] and h.get('acceptance_evidence_ref') ==
                     {'kind': 'evidence', 'id': evidence['id'], 'version': evidence['version']}), None)
    if consumed and not for_consumption:
        # Inside commit_progress the Event is already saved but its receipt is
        # sealed only after settlement. Host-written history validates that past
        # consumption here; dispatch separately requires the sealed receipt.
        require(consumed.get('applied_change') == evidence['proposal_snapshot']['proposal']
                and consumed.get('commit_request_id')
                and consumed.get('applied_commitment', {}).get('schedule_change_id') == evidence['change_id'],
                'COMMITMENT_INVALID', '已消费接受必须对应原子提交的完整改约历史')
        return
    require(not consumed and evidence.get('valid'), 'EVIDENCE_SUPERSEDED', '接受已消费或已被后续回应取代')
    current = app.store.get('message', message['id'], family)
    require(message_source_digest(current) == evidence['source_message_digest'],
            'EVIDENCE_SUPERSEDED', '接受原文或意图已更正')
    snapshot, _, _ = delivered_proposal(app, family, evidence['event_id'],
        evidence['change_id'], evidence['proposal_operation_id'])
    require(snapshot == evidence['proposal_snapshot'], 'CHANGE_SUPERSEDED', '当前可消费提议已变化')
    responses = app.store.list('change_response', family)
    for later in app.store.list('message', family):
        if later.get('direction') != 'inbound' or sequence(app, family, later['id']) <= evidence['source_seq']:
            continue
        # New input is not automatically a withdrawal. It must be explicitly
        # reviewed before an older acceptance is consumed as still current.
        reviewed = [r for r in responses if r.get('change_id') == evidence['change_id']
                    and r.get('source_message_ref', {}).get('id') == later['id']
                    and r.get('source_message_digest') == message_source_digest(later)]
        require(reviewed and all(r.get('interpretation') == 'unrelated'
                and later.get('interpreted') and set(r.get('reviewed_intent_ids', [])) ==
                {i['intent_id'] for i in later.get('intents', [])}
                and r.get('source_message_digest') == message_source_digest(later) for r in reviewed),
                'CHANGE_RESPONSE_REVIEW_REQUIRED', '接受后的新来信尚未核实是否撤回或改变原提议')


SPECS = {'planning.register_change_response': ({'service'}, ChangeResponse,
    '解释完整入站的全部意图对已实际送达原提议的回应；保存完整来源和接受/拒绝/不明确/无关。'
    'unrelated允许本轮重新读取后的历史消息复核，不增加该消息答复权限。'
    '只有接受产生待报告消费的有据凭证；报告使用返回的acceptance_evidence_ref，'
    'response_record_ref本体不是evidence，不自行拼接引用。登记本身不改约、不结清原意图或学习目标。')}
HANDLERS = {'planning.register_change_response': register_change_response}
