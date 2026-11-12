"""Independent mock-channel terminal fences; absence is never a proof."""
import json
from .storage import digest, encode
from .domain import require


def identity(operation):
    return {key: operation.get(key) for key in (
        'id', 'event_id', 'part_id', 'recipient', 'requirement_revision',
        'purpose_key', 'content_digest', 'action_kind')}


def initialize(channel):
    channel.db.execute('CREATE TABLE IF NOT EXISTS terminal_fence(operation_id TEXT PRIMARY KEY, identity_digest TEXT NOT NULL, proof TEXT NOT NULL)')
    channel.db.execute('CREATE TABLE IF NOT EXISTS isolated_transport_fault(fault_key TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE, mode TEXT NOT NULL)')


def proof(channel, operation):
    with channel.lock:
        return _proof(channel,operation)


def _proof(channel, operation):
    row=channel.db.execute('SELECT identity_digest,proof FROM terminal_fence WHERE operation_id=?',(operation['id'],)).fetchone()
    if not row or row[0]!=digest(identity(operation)):
        return None
    delivered=channel.db.execute('SELECT stage FROM delivery WHERE operation_id=?',(operation['id'],)).fetchone()
    if delivered and delivered[0] in {'sent','delivered','accepted','unknown'}:
        return None
    return json.loads(row[1])


def fence(channel, operation, origin):
    """Only called inside the channel's current preflight/send transaction."""
    require(origin in {'preflight_rejected','terminal_not_executed'},'INVALID_PROOF','Unsupported terminal fact')
    require(channel.db.in_transaction,'INVALID_PROOF','Terminal fence must be atomic')
    require(not channel.db.execute('SELECT 1 FROM delivery WHERE operation_id=?',(operation['id'],)).fetchone(),
            'PROOF_CONFLICT','Existing channel result cannot be rewritten as nonexecution')
    value={'kind':'terminal_not_executed','origin':origin,'operation_id':operation['id'],
           'identity_digest':digest(identity(operation)),'at':channel.now(),
           'original_attempt_fenced':True}
    channel.db.execute('INSERT OR IGNORE INTO terminal_fence VALUES(?,?,?)',
                       (operation['id'],value['identity_digest'],encode(value)))
    return proof(channel,operation)


def selected_fault(channel, operation):
    row=channel.db.execute('SELECT mode FROM isolated_transport_fault WHERE operation_id=?',(operation['id'],)).fetchone()
    if row:return row[0]
    config=getattr(channel,'isolated_fault',None)
    if not config or operation.get('retry_of') or operation.get('action_kind')!='deliver_feedback':return None
    require(config.get('mode') in {'preflight_rejected','terminal_not_executed','unresolved','sent_response_unknown'},
            'FAULT_SCOPE','Unsupported private channel fault')
    if config.get('part_id') and config['part_id']!=operation.get('part_id'):return None
    sent=channel.db.execute("SELECT count(*) FROM delivery WHERE stage IN ('sent','delivered') AND json_extract(payload,'$.event_id')=?",
                            (operation.get('event_id'),)).fetchone()[0]
    if sent!=config.get('after_sent_parts',1):return None
    key=config.get('fault_id','single_delivery_fault')
    channel.db.execute('INSERT OR IGNORE INTO isolated_transport_fault VALUES(?,?,?)',(key,operation['id'],config['mode']))
    row=channel.db.execute('SELECT mode FROM isolated_transport_fault WHERE operation_id=?',(operation['id'],)).fetchone()
    return row[0] if row else None


def preflight(channel, operation):
    with channel.lock:
        channel.db.execute('BEGIN IMMEDIATE')
        try:
            mode=selected_fault(channel,operation)
            value=proof(channel,operation)
            if value is None and mode=='preflight_rejected':
                value=fence(channel,operation,mode)
            channel.db.execute('COMMIT')
            return 'failed' if value else None
        except BaseException:
            channel.db.execute('ROLLBACK')
            raise


def send_result(channel, operation):
    """Called after actual invocation entry and inside its delivery transaction."""
    if channel.db.execute('SELECT 1 FROM terminal_fence WHERE operation_id=?',(operation['id'],)).fetchone():
        require(proof(channel,operation) is not None,'PROOF_CONFLICT','Fenced payload mismatch')
        return 'failed'
    mode=selected_fault(channel,operation)
    if mode=='terminal_not_executed':
        fence(channel,operation,mode)
        return 'unknown'  # response lost; original-operation query reveals the fence
    if mode=='unresolved':return 'unknown'
    if mode=='sent_response_unknown':
        channel.db.execute('INSERT OR IGNORE INTO delivery VALUES(?,?,?)',(operation['id'],encode(operation),'sent'))
        return 'unknown'
    return None
