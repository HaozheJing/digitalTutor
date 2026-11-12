"""Project selected original contracts without creating new authority."""
from copy import deepcopy


def completion_recovery_contracts(events):
    return {
        'source': 'host_selected_original_event_records',
        'authority': 'read_only_contract_projection_not_evidence_or_send_permission',
        'rules': (
            '完成按原completion与有值的completion_condition核对；null不产生额外完成标准。'
            'waiting的reason/resume_condition是模型留下的恢复说明，不是修改原完成合同的入口。'
            '新收到的有效来源可以描述较早发生的事实；来源到达时间、快照覆盖时间、'
            '学习发生时间、提醒时刻与原截止分别核对，不互相替代。'
            '提醒/上次核查时刻本身不规定新完成证据的最早覆盖时间；'
            '仅原合同或可信来源合同实际要求的时间条件才适用。'
            '是否完成、是否按时和其他未知属性分别报告。'
            '这不免除证据可信性、当前有效性、原课程/课节匹配及来源自身观察/覆盖校验；'
            '也不取消原明示日期条件。'
        ),
        'events': [{
            'event_id': event['id'], 'version': event['version'],
            'completion': deepcopy(event['completion']),
            'completion_condition': event.get('completion_condition'),
            'requirement_revision': event.get('requirement_revision'),
            'due_at': event.get('due_at'),
            'requested_time': deepcopy(event.get('requested_time')),
            'source_identity': deepcopy(event.get('source_identity')),
            'waiting': deepcopy(event.get('waiting')),
            'waiting_authority': 'model_recovery_note_not_additional_completion_predicate',
        } for event in events],
    }
