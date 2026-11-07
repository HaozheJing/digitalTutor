import json,sqlite3,unittest
import skill_capabilities as caps,sql_tools as s
import test_skill_capabilities as legacy


class AmendmentReceiptTests(unittest.TestCase):
    setUp=legacy.ScopeTests.setUp

    def inbound(self,body,source='current',speaker='user'):
        with sqlite3.connect(s.DB) as c:
            if 'payload' not in {x[1] for x in c.execute('PRAGMA table_info(events)')}:
                c.execute('ALTER TABLE events ADD COLUMN payload TEXT')
            c.execute('CREATE TABLE IF NOT EXISTS messages(source_id TEXT,speaker TEXT,body TEXT)')
            c.execute('INSERT INTO messages VALUES(?,?,?)',(source,speaker,body))
            c.execute('UPDATE events SET payload=?',(json.dumps({'source_message_ids':['current']}),))

    def test_current_pause_cannot_skip_arrangement_flow(self):
        self.inbound('今天所有主动提醒、表扬和鼓励都暂停，请不要因为完课又来联系我。')
        for name in ('send_message','reply_to_message'):
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'pause_flow_required'):caps.guard(name)
        self.amend()
        with self.assertRaisesRegex(ValueError,'not_committed'):caps.guard('send_message')
        self.receipt();caps.guard('send_message')

    def test_pause_routing_does_not_create_constraints(self):
        self.inbound('今天别提醒我')
        with sqlite3.connect(s.DB) as c:before=list(c.iterdump())
        with self.assertRaisesRegex(ValueError,'pause_flow_required'):caps.guard('send_message')
        with sqlite3.connect(s.DB) as c:self.assertEqual(before,list(c.iterdump()))

    def test_old_or_assistant_message_is_not_pause_authority(self):
        self.inbound('暂停所有主动服务',source='old')
        self.inbound('暂停所有主动服务',speaker='assistant')
        caps.guard('send_message')

    def test_negated_or_unrelated_pause_does_not_route(self):
        self.inbound('不要暂停提醒。视频暂停了。今天读完了。')
        caps.guard('send_message')

    def test_inspection_allows_clarification_without_writing_pause(self):
        self.inbound('暂停提醒可以吗？')
        caps.loaded('manage-service-arrangements',{'body':'loaded'})
        caps.read_complete('workflow_read',{'purpose':'inspect'},{'context_token':'inspection'})
        caps.guard('send_message',{'body':'想暂停哪段时间？'})

    def amend(self,token='context-a'):
        caps.loaded('manage-service-arrangements',{'body':'loaded'})
        caps.read_complete('workflow_read',{'view':'context','purpose':'amend'},{'context_token':token})

    def receipt(self,token='context-a',run='run',status='committed'):
        with sqlite3.connect(s.DB) as c:
            c.execute('CREATE TABLE IF NOT EXISTS service_workflow_commits(run_id TEXT,request_id TEXT,body TEXT,result TEXT,created_at TEXT)')
            c.execute('INSERT INTO service_workflow_commits VALUES(?,?,?,?,?)',(run,'request',json.dumps([token,[],[],{'scope':{'kind':'all'}}]),json.dumps({'status':status}),'now'))

    def test_empty_task_amendment_cannot_reply_before_receipt(self):
        self.amend()
        for name in ('send_message','reply_to_message'):
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'arrangement_amendment_not_committed'):
                caps.guard(name,{'body':'今天不打扰'})

    def test_other_run_wrong_context_and_noncommitted_do_not_unlock_reply(self):
        self.amend();self.receipt(run='other');self.receipt(token='old');self.receipt(status='accepted')
        with self.assertRaisesRegex(ValueError,'not_committed'):caps.guard('send_message')
        self.receipt();caps.guard('send_message')

    def test_refresh_requires_receipt_for_current_amendment(self):
        self.amend();self.receipt();caps.guard('send_message')
        self.amend('context-b')
        with self.assertRaisesRegex(ValueError,'not_committed'):caps.guard('send_message')
        self.receipt('context-b');caps.guard('send_message')

    def test_inspection_and_normal_reply_remain_available(self):
        caps.guard('send_message')
        caps.loaded('manage-service-arrangements',{'body':'loaded'})
        caps.read_complete('workflow_read',{'purpose':'inspect'},{'context_token':'inspection'})
        caps.guard('send_message')

    def test_scope_does_not_leak_to_next_message_or_internal_task(self):
        self.amend()
        with sqlite3.connect(s.DB) as c:c.execute("UPDATE events SET id='next'")
        caps.guard('send_message')
        self.task={'id':'task','kind':'completion_plan','status':'running','native_request_id':'request','attempts':1}
        with self.assertRaisesRegex(ValueError,'not_open'):caps.guard('send_message')


if __name__=='__main__':unittest.main()
