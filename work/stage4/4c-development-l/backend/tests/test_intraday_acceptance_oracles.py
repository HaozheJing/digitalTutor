"""Negative controls prevent schema compatibility from weakening acceptance."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1]/'scripts'))
from accept_intraday import condition_faithfully_propagated
from stage4_behavior_checks import pending_preserves_deadline


class AcceptanceOracleTests(unittest.TestCase):
    def deadline_fixture(self):
        expected=datetime.fromisoformat('2026-10-05T16:00:00+08:00')
        messages=[{'id':'m','direction':'inbound','body':'请安排明天10月5日下午四点前需要的总结。'}]
        pending={'disposition':'pending','event_id':None,'source_message_id':'m',
            'original_deadline':'明天10月5日下午四点前需要',
            'requested_time':{'meaning':'before','source_text':'明天10月5日下午四点前需要',
                              'end':'2026-10-05T16:00:00+08:00'}}
        return pending,messages,expected

    def test_new_and_legacy_deadlines(self):
        p,m,t=self.deadline_fixture()
        self.assertTrue(pending_preserves_deadline(p,m,t))
        p['requested_time']['end']='2026-10-05T08:00:00+00:00'
        self.assertTrue(pending_preserves_deadline(p,m,t))
        p.pop('requested_time');p['original_deadline']='2026-10-05T16:00:00+08:00'
        self.assertTrue(pending_preserves_deadline(p,m,t))

    def test_wrong_time_meaning_or_provenance_cannot_pass(self):
        for field,value in [('meaning','at'),('end','2026-10-05T16:01:00+08:00'),
                            ('end','2026-10-05'),('source_text','未出现的原话')]:
            with self.subTest(field=field):
                p,m,t=self.deadline_fixture();p['requested_time'][field]=value
                self.assertFalse(pending_preserves_deadline(p,m,t))
        for field,value in [('event_id','e'),('disposition','created'),('source_message_id','other'),
                            ('original_deadline','改写后的期限')]:
            with self.subTest(field=field):
                p,m,t=self.deadline_fixture();p[field]=value
                self.assertFalse(pending_preserves_deadline(p,m,t))

    def condition_fixture(self,condition):
        identity={'kind':'family_text_delivery','message_id':'m','intent_id':'i'}
        return ({'id':'e','completion_condition':condition,'source_identity':identity},
            {'event_id':'e','candidate_source_id':'s','source_message_id':'m','source_intent_id':'i','completion_condition':condition},
            {'id':'s','source_identity':identity,'source_message_id':'m','source_intent_id':'i','completion_condition':condition},
            {'id':'m','body':'请明天给我总结，今天不用发。总结要说清完成情况和作业提交情况。'},
            [{'criterion':condition or '原请求所需的文字总结实际送达'}])

    def test_null_and_source_quoted_condition_are_both_faithful(self):
        for condition in (None,'今天不用发','总结要说清完成情况和作业提交情况'):
            with self.subTest(condition=condition):
                self.assertTrue(condition_faithfully_propagated(*self.condition_fixture(condition)))

    def test_explicit_condition_cannot_be_dropped(self):
        self.assertTrue(condition_faithfully_propagated(*self.condition_fixture('总结要说清完成情况和作业提交情况'),explicit=True))
        for condition in (None,'今天不用发'):
            self.assertFalse(condition_faithfully_propagated(*self.condition_fixture(condition),explicit=True))

    def test_condition_mutation_invention_and_wrong_link_fail(self):
        values=self.condition_fixture('今天不用发')
        for index,key,value in [(1,'event_id','other'),(1,'candidate_source_id','other'),
                                (1,'completion_condition',None),(2,'source_message_id','other'),
                                (2,'completion_condition','不同条件')]:
            with self.subTest(index=index,key=key):
                changed=deepcopy(values);changed[index][key]=value
                self.assertFalse(condition_faithfully_propagated(*changed))
        self.assertFalse(condition_faithfully_propagated(*self.condition_fixture('必须打电话')))

    def test_assessment_must_match_or_explain_autonomous_criterion(self):
        for condition in (None,'今天不用发'):
            values=self.condition_fixture(condition);values[-1][0]['criterion']=''
            self.assertFalse(condition_faithfully_propagated(*values))
        values=list(self.condition_fixture(None));values[-1]=[]
        self.assertFalse(condition_faithfully_propagated(*values))


if __name__=='__main__':unittest.main()
