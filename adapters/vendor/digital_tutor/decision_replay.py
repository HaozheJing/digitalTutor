"""Prepare same-decision snapshots. Current/future teacher answers stay outside runtime."""
from copy import deepcopy
from datetime import timedelta
from .domain import require, timestamp


def build_decision_pack(pack, teacher_reference, customer_source_ids, teacher_source_ids):
    require(customer_source_ids and teacher_source_ids,'DECISION_INPUT','Specify reviewed customer/teacher decision groups')
    customers={row['source_id']:row for row in pack['events']}
    teachers={row['source_id']:row for row in teacher_reference if row['speaker_role']=='teacher'}
    require(set(customer_source_ids)<=set(customers) and set(teacher_source_ids)<=set(teachers),
            'DECISION_INPUT','Unknown decision source')
    current=[deepcopy(customers[sid]) for sid in customer_source_ids]
    answers=[teachers[sid] for sid in teacher_source_ids]
    decision=min(timestamp(row['occurred_at']) for row in answers)
    require(all(timestamp(row['arrival_at'])<=decision for row in current),
            'DECISION_FUTURE_INPUT','Decision cannot include a future customer response')
    first=min((timestamp(row['occurred_at']),row['source_metadata']['source_row']) for row in current)
    merged={row['source_id']:row for row in pack.get('history',[])+pack['events']+teacher_reference}
    # Teacher prompts before the current customer group are now historical;
    # hide all teacher responses in/after this group, including the target answer.
    history=[deepcopy(row) for row in merged.values()
             if (timestamp(row['occurred_at']),row['source_metadata']['source_row'])<first]
    history.sort(key=lambda row:(timestamp(row['occurred_at']),row['source_metadata']['source_row']))
    for row in current:
        row['original_arrival_at']=row['arrival_at']
        row['arrival_at']=decision.isoformat()
    result={**deepcopy(pack),'history':history,'events':current,
        'start':decision.isoformat(),'end':(decision+timedelta(seconds=1)).isoformat(),
        'evaluation_protocol':'matched_decision_snapshot','external_model_use_authorized':False,
        'evaluation_contract':{
            'visible_teacher_history':'strictly_before_current_customer_group',
            'current_and_future_teacher_answers_hidden':True,
            'customers_observed_as_one_snapshot_at_teacher_decision':True,
            'physical_message_arrival_or_latency_test':False,
            'new_input_hash_requires_separate_review':True}}
    return result
