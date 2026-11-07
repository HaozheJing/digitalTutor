from contextlib import closing
import io, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from reply_lab import Fault
from reply_lab_native import pending_handoff, execute_when_idle


class HandoffTests(unittest.TestCase):
    def test_retry_only_explicit_busy_before_operation(self):
        busy=lambda: HTTPError('http://local',409,'busy',{},io.BytesIO(b'{"error":"agent_busy"}'))
        call=Mock(side_effect=[busy(),{'status':'queued'}])
        with patch('reply_lab_native.time.sleep'):
            self.assertEqual(execute_when_idle(call,'task')['status'],'queued')
        self.assertEqual(call.call_count,2)
        for code,body in [(409,b'{"error":"experiment_request_rejected"}'),(500,b'{"error":"agent_busy"}'),(409,b'not json')]:
            call=Mock(side_effect=HTTPError('http://local',code,'error',{},io.BytesIO(body)))
            with self.assertRaises(Fault):execute_when_idle(call,'task')
            self.assertEqual(call.call_count,1)

    def test_requires_finished_turn_successful_tool_and_scoped_pending_alert(self):
        with tempfile.TemporaryDirectory() as folder:
            database=Path(folder)/'live.sqlite'
            with closing(sqlite3.connect(database)) as db, db:
                db.execute('CREATE TABLE operator_alerts(run_id TEXT,uid TEXT,status TEXT)')
                db.execute("INSERT INTO operator_alerts VALUES('old','family','pending')")
            snapshot={'run_id':'current','family_uid':'family','native_evidence':{'completed_turns':1},
                      'tool_trace':[{'name':'request_human_confirmation','status':'ok'}]}
            self.assertFalse(pending_handoff(snapshot,database))
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("INSERT INTO operator_alerts VALUES('current','family','pending')")
            self.assertTrue(pending_handoff(snapshot,database))
            snapshot['native_evidence']['completed_turns']=0
            self.assertFalse(pending_handoff(snapshot,database))
            snapshot['native_evidence']['completed_turns']=1
            snapshot['tool_trace'][0]['status']='error'
            self.assertFalse(pending_handoff(snapshot,database))
            snapshot['tool_trace'][0]['status']='ok'
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("UPDATE operator_alerts SET status='sent'")
            self.assertFalse(pending_handoff(snapshot,database))


if __name__=='__main__':unittest.main()
