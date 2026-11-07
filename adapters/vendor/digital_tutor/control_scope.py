"""Read-only interpretation of persisted control scope; never migrates old data."""


def legacy_invalid_scope(control):
    value=control.get('event_id')
    return isinstance(value,str) and not value.strip()


def control_event_id(control):
    # Historical empty strings were admitted as family-like commands but failed
    # to pause any event. Interpret them conservatively until explicitly released.
    return None if legacy_invalid_scope(control) else control.get('event_id')


def control_active(app, control):
    from .domain import timestamp
    return control['status'] == 'active' and (not control.get('valid_until') or
        timestamp(control['valid_until']) > app.clock.now())
