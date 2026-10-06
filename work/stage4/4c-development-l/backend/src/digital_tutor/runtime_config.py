"""Host-only mode gates and a cross-process business clock."""
from datetime import datetime
import json
from pathlib import Path
from .domain import SHANGHAI, timestamp, require


class Clock:
    def __init__(self, fixed=None, shared_path=None):
        self.fixed = timestamp(fixed) if fixed else None
        self.shared_path = str(Path(shared_path).resolve()) if shared_path else None

    def now(self):
        if self.shared_path:
            # A missing or malformed authority fails closed; never silently use wall time.
            value = json.loads(Path(self.shared_path).read_text(encoding='utf-8'))
            return timestamp(value['now']).astimezone(SHANGHAI)
        return (self.fixed or datetime.now(SHANGHAI)).astimezone(SHANGHAI)

    def iso(self):
        return self.now().isoformat()


def validate_mode(mode):
    require(mode in {'passive', 'daily', 'full'}, 'INVALID_CONFIG', 'Unknown runtime mode')
    return mode


def allowed_kind(app, kind):
    mode = getattr(app, 'mode', 'full')
    return kind == 'service' or mode == 'full' or (mode == 'daily' and kind == 'daily')


def lease_clock(app):
    # Legacy deterministic fixtures retain their clock; strict Worker always uses wall time.
    return app.runtime_clock if app.runtime_clock is not None else app.clock


def reply_scope(app, family):
    scope = {}
    for message in app.store.list('message', family):
        if message['direction'] != 'inbound':
            continue
        pending = ['*'] if not message['interpreted'] else [
            intent['intent_id'] for intent in message['intents']
            if intent['requires_reply'] and not intent['reply_operation_refs']]
        if pending:
            scope[message['id']] = pending
    if getattr(app, 'strict_runtime', False):
        from .request_responsibility import filter_reply_scope
        scope = filter_reply_scope(app, family, scope)
    return scope
