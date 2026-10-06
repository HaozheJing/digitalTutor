"""Deadline ambiguity and persisted H receipt compatibility; no model API."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from digital_tutor.application import Application, Clock
from digital_tutor.domain import BusinessError
from digital_tutor.family_candidates import explicit_request
from digital_tutor.jobs import JobQueue
from digital_tutor.storage import digest, encode


class CandidateFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=Application(Path(self.tmp.name)/'case.sqlite',
            Clock('2026-10-04T09:00:00+08:00'),mode='daily',strict_runtime=True)
        self.app.create_family('f','synthetic-f','mock:f')
        # Persisted command/result shape from H, before candidate extensions.
        # Build it explicitly rather than from the new Pydantic model/helper.
        self.old_payload={'request_id':'historical-daily','business_date':'2026-10-04',
            'expected_family_version':1,'new_events':[{
                'client_ref':'original','goal_key':'old-goal','goal_text':'Historical responsibility',
                'completion':{'kind':'goal','lesson_refs':['synthetic-lesson'],
                              'required_part_ids':[],'required_stage':'sent'},
                'status':'pending','waiting':None,'due_at':None,'next_action':None}],
            'event_updates':[],'plan_refs':['original'],'pending_decisions':[]}
        self.old_result={'request_id':'historical-daily','outcome':'applied',
            'data':{'plan':{'id':'historical-plan'},'event_ids':{'original':'historical-event'}},
            'record_versions':{'family_input_version':2},'error':None}

    def tearDown(self):
        self.app.close();self.tmp.cleanup()

    def historical_receipt(self):
        with self.app.store.transaction() as db:
            db.execute('INSERT INTO receipts VALUES(?,?,?,?)',('f',self.old_payload['request_id'],
                digest({'command':'commit_daily','payload':self.old_payload}),encode(self.old_result)))

    def worker_token(self):
        queue=JobQueue(self.app)
        with self.app.store.transaction():
            queue.enqueue('f','daily-replay',{'kind':'daily','business_date':'2026-10-04'})
        claim=queue.claim('strict-replay')
        self.assertIsNotNone(claim)
        token=queue.session(claim)
        self.assertIn('lease',self.app.auth(token,{'planner_daily'})[1])
        return token

    def assert_conflict(self,token,payload):
        before=self.app.store.family('f')['version']
        with self.assertRaises(BusinessError) as caught:self.app.commit_daily(token,payload)
        self.assertEqual(caught.exception.code,'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.app.store.family('f')['version'],before)
        self.assertEqual(self.app.store.list('event','f'),[])

    def test_evening_and_morning_twelve_are_not_fixed_to_noon_or_midnight(self):
        for meridiem in ('上午','晚上'):
            for time in ('十二点','12点','十二点半','12点15分'):
                body='请明天'+meridiem+time+'前补完第1天'
                with self.subTest(body=body),self.assertRaises(BusinessError) as caught:
                    explicit_request({'body':body,'occurred_at':'2026-10-04T09:00:00+08:00'})
                self.assertEqual(caught.exception.code,'DEADLINE_AMBIGUOUS')

    def test_unambiguous_times_keep_original_precision_and_text(self):
        for phrase,hour in [('上午十一点',11),('中午十二点',12),('下午四点',16),('晚上十一点',23)]:
            with self.subTest(phrase=phrase):
                parsed=explicit_request({'body':'请明天'+phrase+'前补完第1天',
                    'occurred_at':'2026-10-04T09:00:00+08:00'})
                self.assertEqual(parsed['due_at'],f'2026-10-05T{hour:02d}:00:00+08:00')
                self.assertEqual(parsed['requested_time']['precision'],'datetime')
                self.assertEqual(parsed['original_deadline'],'明天'+phrase+'前')

    def test_historical_strict_worker_daily_receipt_replays(self):
        self.historical_receipt();token=self.worker_token()
        result=self.app.commit_daily(token,self.old_payload)
        self.assertEqual(result,{**self.old_result,'outcome':'duplicate'})
        self.assertEqual(self.app.store.family('f')['version'],1)
        self.assertEqual(self.app.store.list('event','f'),[])
        self.assertEqual(self.app.store.list('plan','f'),[])

    def test_explicit_empty_extensions_mean_same_historical_command(self):
        self.historical_receipt();token=self.worker_token();payload=deepcopy(self.old_payload)
        payload['candidate_decisions']=[]
        payload['new_events'][0].update(candidate_id=None,delivery_parts=[])
        self.assertEqual(self.app.commit_daily(token,payload)['outcome'],'duplicate')
        self.assertEqual(self.app.store.family('f')['version'],1)

    def test_nonempty_candidate_decision_cannot_replay_old_receipt(self):
        self.historical_receipt();token=self.worker_token();payload=deepcopy(self.old_payload)
        payload['candidate_decisions']=[{'candidate_id':'proposed-new','expected_version':1,
            'disposition':'retained','event_ref':None,'reason':'Different submitted command'}]
        self.assert_conflict(token,payload)

    def test_nonempty_candidate_reference_cannot_replay_old_receipt(self):
        self.historical_receipt();token=self.worker_token();payload=deepcopy(self.old_payload)
        payload['new_events'][0]['candidate_id']='different-candidate'
        self.assert_conflict(token,payload)

    def test_nonempty_delivery_parts_cannot_replay_old_receipt(self):
        self.historical_receipt();token=self.worker_token();payload=deepcopy(self.old_payload)
        payload['new_events'][0]['delivery_parts']=[{
            'part_id':'extra','media_type':'text','purpose':'Different delivery requirement'}]
        self.assert_conflict(token,payload)

    def test_changed_original_fields_cannot_replay_after_normalization(self):
        self.historical_receipt();token=self.worker_token();payload=deepcopy(self.old_payload)
        payload['new_events'][0]['goal_text']='Changed original responsibility'
        self.assert_conflict(token,payload)

    def test_no_historical_receipt_does_not_waive_strict_source_gate(self):
        token=self.worker_token()
        with self.assertRaises(BusinessError) as caught:self.app.commit_daily(token,self.old_payload)
        self.assertEqual(caught.exception.code,'CANDIDATE_SOURCE_MISSING')
        self.assertEqual(self.app.store.list('event','f'),[])
        self.assertEqual(self.app.store.list('plan','f'),[])
        self.assertEqual(self.app.store.connection.execute('SELECT count(*) FROM receipts').fetchone()[0],0)

    def test_legacy_cannot_strip_nonempty_extension_to_replay_old_receipt(self):
        self.app.strict_runtime=False
        self.historical_receipt();token=self.app.open_session('f','planner_daily')
        payload=deepcopy(self.old_payload);payload['new_events'][0]['candidate_id']='different-candidate'
        self.assert_conflict(token,payload)

    def test_extended_strict_receipt_keeps_nonempty_candidate_contract(self):
        payload=deepcopy(self.old_payload)
        payload['new_events'][0].update(candidate_id='real-candidate',delivery_parts=[])
        payload['candidate_decisions']=[{'candidate_id':'real-candidate','expected_version':1,
            'disposition':'created','event_ref':'original','reason':'Original source-backed decision'}]
        with self.app.store.transaction() as db:
            db.execute('INSERT INTO receipts VALUES(?,?,?,?)',('f',payload['request_id'],
                digest({'command':'commit_daily','payload':payload}),encode(self.old_result)))
        token=self.worker_token()
        self.assertEqual(self.app.commit_daily(token,payload)['outcome'],'duplicate')
        payload['candidate_decisions'][0]['reason']='Changed decision'
        self.assert_conflict(token,payload)


if __name__=='__main__':unittest.main()
