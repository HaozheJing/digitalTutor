"""Real text transport, bound to host-configured test conversations only."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError

from .mock_channel import MockChannel
from .storage import encode, digest, new_id
from .receipt_state import merge_stage


def config():
    return json.loads(Path(os.environ['DIGITAL_TUTOR_WECOM_CONFIG']).read_text())


def family_id(route):
    identity = [route[k] for k in ('orgId', 'imBotId', 'chatId', 'imContactId')]
    return 'wecom-' + hashlib.sha256(encode(identity).encode()).hexdigest()[:32]


def selected_route(cfg, recipient):
    if cfg.get('auto_reply_enabled') is not True or not cfg.get('expected_token'):
        raise ValueError('WECOM_AUTO_REPLY_NOT_CONFIGURED')
    url = urlsplit(cfg.get('api_base_url', ''))
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('WECOM_HTTPS_BASE_URL_REQUIRED')
    routes = [r for r in cfg.get('allowed_routes', []) if recipient == 'wecom:' + family_id(r)]
    if len(routes) != 1 or routes[0]['orgId'] != cfg.get('expected_org_id'):
        raise ValueError('WECOM_RECIPIENT_NOT_ALLOWED')
    return routes[0]


def bootstrap_allowed(recipient):
    selected_route(config(), recipient)
    return True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_send(url, body):
    request = Request(url, data=json.dumps(body, ensure_ascii=False).encode(),
                      headers={'Content-Type': 'application/json'})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=20) as response:
        return json.loads(response.read(65536))


class WecomChannel(MockChannel):
    """Reuse the durable ledger, but never call the simulated send method."""
    def __init__(self, path):
        super().__init__(path)
        self.db.execute('''CREATE TABLE IF NOT EXISTS provider_requests (
            operation_id TEXT PRIMARY KEY, request_id TEXT, recipient TEXT NOT NULL,
            error_code TEXT, message_id TEXT)''')

    def send(self, operation, outcome=None):
        cfg = config()
        route = selected_route(cfg, operation['recipient'])
        if operation.get('action_kind') not in {'reply', 'clarify_existing'} or not operation.get('reply_to') or operation.get('media'):
            raise ValueError('WECOM_TEXT_REPLY_ONLY')
        body = {'externalRequestId': operation['id'], 'imBotId': route['imBotId'],
                'imContactId': route['imContactId'], 'messageType': 7,
                'payload': {'text': operation['body']}}
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                prior = self.db.execute('SELECT payload,stage FROM delivery WHERE operation_id=?', (operation['id'],)).fetchone()
                if prior:
                    if json.loads(prior[0]) != operation:
                        raise ValueError('WECOM_OPERATION_PAYLOAD_CHANGED')
                    self.db.execute('COMMIT')
                    return prior[1]  # A crash/timeout never causes a new HTTP send.
                invocation = new_id('transport')
                self.db.execute('INSERT INTO delivery VALUES(?,?,?)', (operation['id'], encode(operation), 'unknown'))
                self.db.execute('INSERT INTO provider_requests(operation_id,recipient) VALUES(?,?)',
                                (operation['id'], operation['recipient']))
                self.db.execute('INSERT INTO transport_invocation VALUES(?,?,?,?,?,?,NULL,NULL)',
                                (invocation,operation['id'],self.now(),digest(operation),'real_http','pending'))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
        stage, request_id, error = 'unknown', None, None
        try:
            result = http_send(cfg['api_base_url'].rstrip('/') + '/api/v2/message/send?' +
                               urlencode({'token': cfg['expected_token']}), body)
            code = result.get('errcode')
            if code == 0 and isinstance(result.get('requestId'), str) and result['requestId']:
                stage, request_id = 'accepted', result['requestId']
            elif isinstance(code, (str, int)) and str(code) in {'1','2','6','-2','-3','-4','-5','-6','400101'}:
                stage, error = 'failed', str(code)
            else:
                error = 'unconfirmed_response'
        except Exception:
            error = 'transport_unknown'  # Never persist URLs containing the API token.
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                current = self.db.execute('SELECT stage FROM delivery WHERE operation_id=?',(operation['id'],)).fetchone()[0]
                merged = merge_stage(current, stage)
                self.db.execute('UPDATE delivery SET stage=? WHERE operation_id=?',(merged,operation['id']))
                self.db.execute('UPDATE provider_requests SET request_id=COALESCE(?,request_id),error_code=? WHERE operation_id=?',
                                (request_id,error,operation['id']))
                self.db.execute('UPDATE transport_invocation SET status=?,response_stage=?,observed_stage=? WHERE id=?',
                                ('completed',stage,merged,invocation))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
        return merged

    def observe_receipt(self, payload):
        cfg = config()
        with self.lock:
            external, request_id = payload.get('externalRequestId'), payload.get('requestId')
            rows = self.db.execute('SELECT operation_id,recipient,request_id FROM provider_requests WHERE operation_id=? OR request_id=?',
                                   (external, request_id)).fetchall()
            if len(rows) != 1:
                return False
            operation_id, recipient, known_request = rows[0]
            route = selected_route(cfg, recipient)
            if any(payload.get(k) != route[k] for k in ('orgId','imBotId','imContactId')):
                return False
            if external and external != operation_id:
                return False
            if known_request and request_id and known_request != request_id:
                return False
            error, send = payload.get('errorCode'), payload.get('sendCode')
            # Real success callbacks omit errorCode but include sendCode=0 and
            # the provider message ID. Do not infer success from missing fields alone.
            if send == 0 and (error == 0 or ('errorCode' not in payload and payload.get('messageId'))):
                observed = 'sent'  # Provider confirmation is not a read/delivered receipt.
            elif error in (1001, 1002) or send in (1001, 1002):
                observed = 'unknown'
            elif isinstance(error, int) and error != 0:
                observed = 'failed'
            elif isinstance(send, int) and send != 0:
                observed = 'failed'
            else:
                return False
            self.db.execute('BEGIN IMMEDIATE')
            try:
                current = self.db.execute('SELECT stage FROM delivery WHERE operation_id=?',(operation_id,)).fetchone()[0]
                self.db.execute('UPDATE delivery SET stage=? WHERE operation_id=?',
                                (merge_stage(current, observed),operation_id))
                self.db.execute('UPDATE provider_requests SET request_id=COALESCE(request_id,?),message_id=? WHERE operation_id=?',
                                (request_id,payload.get('messageId'),operation_id))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
            return True
