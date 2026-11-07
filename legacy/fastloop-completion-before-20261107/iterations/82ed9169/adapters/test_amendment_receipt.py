import json,sqlite3,unittest
import skill_capabilities as caps,sql_tools as s
import test_skill_capabilities as legacy


class AmendmentReceiptTests(unittest.TestCase):
    setUp=legacy.ScopeTests.setUp

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
