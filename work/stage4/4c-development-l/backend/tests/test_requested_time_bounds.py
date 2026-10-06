"""Time meaning is not inferred from whichever nullable field happened to be filled."""
from datetime import timedelta
from types import SimpleNamespace
import unittest

import test_intraday_contracts as fixtures
from digital_tutor.admission import grant_accepted_requests
from digital_tutor.domain import timestamp
from digital_tutor.scheduling import step_ready


class RequestedTimeBoundsTests(unittest.TestCase):
    setUp = fixtures.IntradayContracts.setUp
    tearDown = fixtures.IntradayContracts.tearDown
    key = fixtures.IntradayContracts.key
    call = fixtures.IntradayContracts.call
    applied = fixtures.IntradayContracts.applied
    rejected = fixtures.IntradayContracts.rejected
    business_snapshot = fixtures.IntradayContracts.business_snapshot
    message = fixtures.IntradayContracts.message
    admission = fixtures.IntradayContracts.admission

    def request(self, meaning='at', start='2026-10-05T16:00:00+08:00', end=None):
        body='Please provide a synthetic text summary tomorrow at the requested time.'
        message,service=self.message(body,service_kind='future_delivery',service_authorization=body)
        args={'request_id':self.key(),'expected_family_version':self.app.store.family('f')['version'],
            'source_message_id':message['id'],'source_version':message['version'],
            'source_intent_id':'request','source_excerpt':body,'request':'Synthetic summary',
            'requested_time':{'meaning':meaning,'source_text':'tomorrow at the requested time','start':start,'end':end}}
        return message,service,args

    def accept_request(self, meaning='at', start='2026-10-05T16:00:00+08:00', end=None, execute=None):
        message,service,args=self.request(meaning,start,end)
        pending=self.applied(service,'pending.propose_service',args)
        token,proposal=self.admission(pending,execute or start)
        saved=self.applied(token,'planner.commit_admission',proposal)
        return self.app.event('f',saved['event_ids']['new-request']),pending,message

    def test_at_omitted_end_has_bounded_nonzero_window_and_late_claim(self):
        event,_,_=self.accept_request()
        grant=event['accepted_commitment']
        self.assertEqual(timestamp(grant['valid_from']),timestamp('2026-10-05T16:00:00+08:00'))
        self.assertEqual(timestamp(grant['valid_until']),timestamp('2026-10-05T21:00:00+08:00'))
        self.assertIsNone(event['due_at'])
        self.app.mode='full'
        self.app.clock.fixed=timestamp(grant['valid_from'])-timedelta(microseconds=1)
        self.assertFalse(step_ready(self.app,'f',event))
        self.app.clock.fixed=timestamp(grant['valid_from'])+timedelta(seconds=15)
        self.assertTrue(step_ready(self.app,'f',event))
        self.app.clock.fixed=timestamp(grant['valid_until'])
        self.assertFalse(step_ready(self.app,'f',event))

    def test_at_same_instant_other_offset_end_normalizes_and_receipt_replays(self):
        message,service,args=self.request(end='2026-10-05T08:00:00+00:00')
        pending=self.applied(service,'pending.propose_service',args)
        self.assertIsNone(pending['requested_time']['end'])
        source=self.app.store.get('candidate_source',pending['candidate_source_id'],'f')
        self.assertIsNone(source['requested_time']['end']);self.assertIsNone(source['due_at'])
        replay=self.call(service,'pending.propose_service',args)
        self.assertTrue(replay['ok']);self.assertEqual(replay['result']['outcome'],'duplicate')
        token,proposal=self.admission(pending,'2026-10-05T16:00:00+08:00')
        saved=self.applied(token,'planner.commit_admission',proposal)
        event=self.app.event('f',saved['event_ids']['new-request'])
        self.assertEqual(timestamp(event['accepted_commitment']['valid_until']),timestamp('2026-10-05T21:00:00+08:00'))

    def test_at_distinct_end_is_ambiguous_and_creates_no_pending(self):
        _,service,args=self.request(end='2026-10-05T17:00:00+08:00')
        self.rejected(service,'pending.propose_service',args,'INVALID_TIME')
        self.assertEqual(self.app.store.list('pending','f'),[])

    def test_old_stored_at_redundant_end_does_not_become_deadline(self):
        _,service,args=self.request()
        pending=self.applied(service,'pending.propose_service',args)
        with self.app.store.transaction():
            # Explicit historical storage-shape fixture. Original message/receipt
            # is retained; this is not a migration of existing accepted grants.
            for kind,key in (('pending',pending['id']),('candidate_source',pending['candidate_source_id'])):
                value=self.app.store.get(kind,key,'f')
                value['requested_time']['end']=value['requested_time']['start']
                self.app.store.put(kind,key,'f',value,value['version'])
            self.app.store.bump('f')
        token,proposal=self.admission(pending,'2026-10-05T16:00:00+08:00')
        saved=self.applied(token,'planner.commit_admission',proposal)
        event=self.app.event('f',saved['event_ids']['new-request'])
        self.assertEqual(timestamp(event['accepted_commitment']['valid_until']),timestamp('2026-10-05T21:00:00+08:00'))
        self.assertIsNone(event['due_at'])

    def test_before_deadline_receives_no_grace(self):
        event,_,_=self.accept_request('before',None,'2026-10-05T16:00:00+08:00','2026-10-05T15:30:00+08:00')
        self.assertEqual(event['due_at'],'2026-10-05T16:00:00+08:00')
        self.assertEqual(timestamp(event['accepted_commitment']['valid_until']),timestamp(event['due_at']))
        self.app.mode='full';self.app.clock.fixed=timestamp(event['due_at'])+timedelta(microseconds=1)
        self.assertFalse(step_ready(self.app,'f',event))

    def test_window_keeps_both_bounds_and_never_extends_end(self):
        event,_,_=self.accept_request('window','2026-10-05T15:00:00+08:00',
                                    '2026-10-05T16:00:00+08:00','2026-10-05T15:30:00+08:00')
        grant=event['accepted_commitment']
        self.assertEqual(timestamp(grant['valid_from']),timestamp('2026-10-05T15:30:00+08:00'))
        self.assertEqual(timestamp(grant['valid_until']),timestamp('2026-10-05T16:00:00+08:00'))
        self.app.mode='full';self.app.clock.fixed=timestamp(grant['valid_until'])+timedelta(seconds=15)
        self.assertFalse(step_ready(self.app,'f',event))

    def test_at_cannot_be_scheduled_earlier(self):
        _,service,args=self.request()
        pending=self.applied(service,'pending.propose_service',args)
        token,proposal=self.admission(pending,'2026-10-05T15:59:59+08:00')
        self.rejected(token,'planner.commit_admission',proposal,'SCHEDULE_SOURCE_MISMATCH')

    def test_existing_actual_event_deadline_caps_at_window(self):
        event,pending,_=self.accept_request()
        # Focused host derivation check for a historical Event carrying a real
        # deadline in addition to at-time; no new model/receipt is claimed here.
        with self.app.store.transaction():
            event.pop('accepted_commitment')
            event['due_at']='2026-10-05T17:00:00+08:00'
            self.app.store.put('event',event['id'],'f',event,event['version'])
            command=SimpleNamespace(request_id='host-derivation-fixture',pending_decisions=[
                SimpleNamespace(disposition='linked',input_id=pending['id'],event_ref=event['id'])])
            grant_accepted_requests(self.app,'f',command,{})
        current=self.app.event('f',event['id'])
        self.assertEqual(current['due_at'],'2026-10-05T17:00:00+08:00')
        self.assertEqual(timestamp(current['accepted_commitment']['valid_until']),timestamp(current['due_at']))


if __name__=='__main__':unittest.main()
