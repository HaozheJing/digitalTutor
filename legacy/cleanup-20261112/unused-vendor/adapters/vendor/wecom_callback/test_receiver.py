import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from receiver import Inbox, make_handler


class CallbackTests(unittest.TestCase):
    def test_persist_duplicate_redact_auth_and_invalid_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inbox.sqlite'
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(
                {'route_secret': 'test-route', 'expected_token': 'test-token'}, Inbox(path)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}/wecom/test-route'
            def post(body, endpoint='/messages'):
                return urlopen(Request(base+endpoint, data=body,
                                      headers={'Content-Type': 'application/json'}), timeout=3)
            try:
                payload = {'orgId': 'test', 'botId': 'bot', 'messageId': 'm1',
                           'token': 'test-token', 'payload': {'text': '你好'}}
                raw = json.dumps(payload).encode()
                for endpoint in ('/messages', '/messages/?provider=1'):
                    with post(raw, endpoint) as response:
                        self.assertEqual(json.load(response)['errcode'], 0)
                with closing(sqlite3.connect(path)) as db:
                    row = db.execute('SELECT deliveries,body_json,status FROM callbacks').fetchone()
                    self.assertEqual(row[0], 2)
                    self.assertNotIn('test-token', row[1])
                    self.assertIn('你好', row[1])
                    self.assertEqual(row[2], 'received_token_verified')
                for body, status in [(b'{', 400), (b'[]', 400), (b'{"token":"wrong"}', 403)]:
                    with self.assertRaises(HTTPError) as error:
                        post(body)
                    self.assertEqual(error.exception.code, status)
                    error.exception.close()
                with self.assertRaises(HTTPError) as error:
                    post(b'{}', '/messages?token=test-token')
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
                receipt = {'token': 'test-token', 'requestId': 'r1', 'errorCode': 0}
                with post(json.dumps(receipt).encode(), '/send-results') as response:
                    self.assertEqual(response.status, 200)
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM callbacks').fetchone()[0], 2)
                # A storage failure must not acknowledge and lose a callback.
                path.unlink()
                with self.assertRaises(HTTPError) as error:
                    post(raw)
                self.assertEqual(error.exception.code, 503)
                error.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
