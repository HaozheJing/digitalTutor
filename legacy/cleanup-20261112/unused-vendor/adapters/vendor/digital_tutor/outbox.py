"""Durable dispatch admission, reconciliation, and explicit crash injection."""
from .domain import BusinessError, Send
from .jobs import validate_lease
from .mock_channel import channel_for
from .send_policy import check_send
from .receipt_state import merge_stage


class InjectedCrash(BaseException):
    """Deliberately bypass ordinary error cleanup to emulate a killed worker."""


def dispatch(app, family, operation_id, failpoint=None):
    from .wechat import _save_confirmed_message
    with app.store.transaction():
        op=app.store.get('operation',operation_id,family)
        if op['stage']!='queued':
            return op
        try:
            if op.get('lease'):
                validate_lease(app,family,op['lease'])
            # Check meaningful controls before generic freshness to retain useful reasons.
            hook=getattr(app,'before_dispatch_check',None)
            if hook: hook(op)
            check_send(app,family,Send.model_validate(op['request']),exclude_operation=op['id'],capability=op.get('send_capability'))
            from .delivery_retry import check_attempt
            check_attempt(app,family,Send.model_validate(op['request']),op.get('send_capability'),op)
            app.check_version(family,op['prepared_family_version'])
        except BusinessError as exc:
            op.update(stage='cancelled',cancel_reason=exc.code)
            saved=app.store.put('operation',op['id'],family,op,op['version'])
            app.store.bump(family)
            return saved
        op.update(stage='dispatching',dispatch_count=1,admitted_at=app.clock.iso())
        op=app.store.put('operation',op['id'],family,op,op['version'])
    # Admission is the last cancellable point. Do not hold a DB transaction over I/O.
    from .runtime_hooks import crash_boundary
    crash_boundary(app,'before_transport',operation_id=op['id'])
    if failpoint=='before_transport':
        raise InjectedCrash()
    try:
        channel=channel_for(app)
        from .transport_proof import preflight
        stage=preflight(channel,op) or channel.send(op,getattr(app,'mock_send_outcome','sent'))
    except Exception:
        stage='unknown'
    crash_boundary(app,'after_transport',operation_id=op['id'])
    if failpoint=='after_transport':
        raise InjectedCrash()
    with app.store.transaction():
        current=app.store.get('operation',op['id'],family)
        current.update(stage=merge_stage(current['stage'],stage))
        if current['stage']=='failed':
            from .transport_proof import proof
            value=proof(channel_for(app),current)
            if value:current['not_executed_proof']=value
        current['receipts'].append({'stage':stage,'observed_at':app.clock.iso(),'source':'mock_transport'})
        current=app.store.put('operation',op['id'],family,current,current['version'])
        if current['stage'] in {'sent','delivered'}:
            _save_confirmed_message(app,family,current)
        app.store.bump(family)
    crash_boundary(app,'after_receipt',operation_id=op['id'])
    return current


def reconcile(app, family, operation_id):
    """Queries original channel operation even after stop/cutoff; never sends."""
    from .wechat import _save_confirmed_message
    with app.store.transaction():
        op=app.store.get('operation',operation_id,family)
        if op['stage'] not in {'dispatching','unknown','accepted'}:
            return op
    observed=channel_for(app).query(operation_id)
    if observed is None and getattr(app,'strict_runtime',False):
        # No channel fact is not a new business input. Keep original uncertainty.
        return op
    with app.store.transaction():
        op=app.store.get('operation',operation_id,family)
        if op['stage'] not in {'dispatching','unknown','accepted'}:
            return op
        stage=merge_stage(op['stage'],observed or 'unknown')
        if op['stage']==stage and (getattr(app,'strict_runtime',False) or op.get('reconcile_required')==(observed is None)):
            return op
        op.update(stage=stage,reconcile_required=observed is None)
        if stage=='failed':
            from .transport_proof import proof
            value=proof(channel_for(app),op)
            if value:op['not_executed_proof']=value
        op['receipts'].append({'stage':observed or 'unknown','observed_at':app.clock.iso(),'source':'original_operation_query'})
        op=app.store.put('operation',op['id'],family,op,op['version'])
        if op['stage'] in {'sent','delivered'}:
            _save_confirmed_message(app,family,op)
        app.store.bump(family)
        return op


def drain(app):
    rows=app.store.connection.execute("SELECT id,family_id FROM records WHERE kind='operation'").fetchall()
    results=[]
    for row in rows:
        op=app.store.get('operation',row['id'],row['family_id'])
        if op['stage']=='queued':
            results.append(dispatch(app,row['family_id'],row['id']))
        elif op['stage'] in {'dispatching','unknown','accepted'}:
            results.append(reconcile(app,row['family_id'],row['id']))
    return results
