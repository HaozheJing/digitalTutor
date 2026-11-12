"""Durable receive-only callback inbox. No model invocation or outbound sending."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from urllib.parse import urlsplit

MAX_BODY = 1024 * 1024


def redact(value):
    if isinstance(value, dict):
        return {k: ('[REDACTED]' if k.lower().replace('_', '') in
                    {'token', 'apikey', 'secret', 'authorization', 'password'} else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class Inbox:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS callbacks (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, event_key TEXT NOT NULL,
                body_sha256 TEXT NOT NULL, body_json TEXT NOT NULL,
                first_received_at TEXT NOT NULL, last_received_at TEXT NOT NULL,
                deliveries INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL,
                UNIQUE(kind,event_key,body_sha256))''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=3)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def save(self, kind, payload, token_verified):
        clean = redact(payload)
        body = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256(body.encode()).hexdigest()
        identity = [payload.get('orgId'), payload.get('botId') or payload.get('imBotId'),
                    payload.get('messageId') if kind == 'message' else
                    (payload.get('externalRequestId') or payload.get('requestId'))]
        event_key = json.dumps(identity, ensure_ascii=False, separators=(',', ':'))
        status = 'received_token_verified' if token_verified else 'received_unverified'
        if not identity[-1]:
            status = 'quarantined_missing_identity'
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('''INSERT INTO callbacks
                (kind,event_key,body_sha256,body_json,first_received_at,last_received_at,status)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(kind,event_key,body_sha256)
                DO UPDATE SET deliveries=deliveries+1,last_received_at=excluded.last_received_at''',
                (kind,event_key,digest,body,now,now,status))


def make_handler(config, inbox):
    routes = {'/wecom/' + config['route_secret'] + '/messages': 'message',
              '/wecom/' + config['route_secret'] + '/send-results': 'send_result'}

    class Handler(BaseHTTPRequestHandler):
        server_version = 'CallbackReceiver'

        def log_message(self, *_):
            pass  # Never log capability URLs, tokens, or message bodies.

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def respond(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path.rstrip('/')
            if path == '/healthz' or path in routes:
                self.respond(200, {'errcode': 0, 'errmsg': 'ok', 'mode': 'receive_only'})
            else:
                self.respond(404, {'errcode': 404, 'errmsg': 'not found'})

        def do_POST(self):
            # Providers may append query parameters or a trailing slash.
            # Authentication still uses the token in the JSON body.
            kind = routes.get(urlsplit(self.path).path.rstrip('/'))
            if not kind:
                return self.respond(404, {'errcode': 404, 'errmsg': 'not found'})
            if self.headers.get('Transfer-Encoding'):
                return self.respond(400, {'errcode': 400, 'errmsg': 'unsupported encoding'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                size = 0
            if not 0 < size <= MAX_BODY:
                return self.respond(413, {'errcode': 413, 'errmsg': 'invalid body size'})
            if self.headers.get('Content-Type', '').split(';')[0].lower() != 'application/json':
                return self.respond(415, {'errcode': 415, 'errmsg': 'application/json required'})
            try:
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError('incomplete body')
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError('object required')
            except (ValueError, UnicodeError, TimeoutError, RecursionError):
                return self.respond(400, {'errcode': 400, 'errmsg': 'invalid JSON'})
            expected = config.get('expected_token')
            if expected and not hmac.compare_digest(str(payload.get('token', '')), expected):
                return self.respond(403, {'errcode': 403, 'errmsg': 'unauthorized'})
            if config.get('expected_org_id') and payload.get('orgId') != config['expected_org_id']:
                return self.respond(403, {'errcode': 403, 'errmsg': 'unauthorized'})
            try:
                inbox.save(kind, payload, bool(expected))
            except (sqlite3.Error, OSError, RecursionError):
                return self.respond(503, {'errcode': 503, 'errmsg': 'storage unavailable'})
            # Both callback response conventions are present in the provider docs.
            self.respond(200, {'errcode': 0, 'errmsg': 'ok', 'errorCode': 0, 'errorMsg': 'ok'})

    return Handler


def main():
    os.umask(0o077)
    config = json.loads(Path(os.environ['WECOM_CALLBACK_CONFIG']).read_text())
    server = ThreadingHTTPServer(('127.0.0.1', config.get('port', 9010)),
                                make_handler(config, Inbox(config['database'])))
    server.serve_forever()


if __name__ == '__main__':
    main()
