"""Independent S-scenario acceptance using synthetic identities and public operations."""
import unittest

import test_stage1 as baseline
from digital_tutor.application import Application, Clock
from digital_tutor.jobs import JobQueue
from digital_tutor.mock_channel import channel_for
from digital_tutor.scheduler import Scheduler, Worker


class ScenarioAcceptance(baseline.CoreTests):
    def setUp(self):
        super().setUp()
        self.app.store.connection.execute("DELETE FROM families WHERE id='other'")

    def with_step(self, action='verify_learning'):
        event = self.goal('a')
        event['next_action'] = {
            'action_kind': action, 'target_refs': ['lesson-a'],
            'purpose': '核验原课节，保留原责任',
        }
        return self.create([event])['a']

    def answer_service(self, job, tools):
        message = self.app.store.get('message', job['message_id'], 'f')
        result = tools.call('wechat.send_message', self.reply(message))
        self.assertTrue(result['ok'], result)
        return {}

    def test_s02_service_answers_before_any_daily_commit(self):
        Scheduler(self.app).tick()
        message = self.incoming()
        seen = []

        def answer(job, tools):
            seen.append(job['kind'])
            return self.answer_service(job, tools)

        outcome = Worker(self.app, answer).run_once()
        self.assertEqual(outcome['outcome'], 'succeeded')
        self.assertEqual(seen, ['service'])
        self.assertEqual(self.app.store.list('plan', 'f'), [])
        self.assertEqual(channel_for(self.app).count(), 1)
        self.assertTrue(self.app.store.get('message', message['id'], 'f')['intents'][0]['reply_operation_refs'])

    def test_progress_empty_return_cannot_abandon_unconsumed_report(self):
        event_id = self.create()['a']
        evidence = self.evidence(lesson_ref='lesson-a')
        report_id = self.report([event_id], [evidence])
        Worker(self.app, lambda job, tools: {}).run_once()
        report = self.app.store.get('report', self.app.report_key('f', report_id, 1), 'f')
        self.assertIsNone(report['resolution'])
        self.assertTrue(self.app.event('f', event_id)['update_pending'])
        job = self.app.store.list('job', 'f')[0]
        self.assertIn(job['status'], {'waiting', 'ready'},
                      '模型正常返回不能把未消费报告的执行义务标成已完成')

    def test_empty_step_return_preserves_schedulable_responsibility(self):
        event_id = self.with_step()
        Scheduler(self.app).tick()
        Worker(self.app, lambda job, tools: {}).run_once()
        step = next(j for j in self.app.store.list('job', 'f') if j['kind'] == 'step')
        self.assertIn(step['status'], {'waiting', 'ready'},
                      '无任何核验结果不能关闭原步骤执行责任')
        self.assertEqual(self.app.event('f', event_id)['remaining_work'], ['lesson-a'])
        calls = []
        Scheduler(self.app).tick()
        Worker(self.app, lambda job, tools: calls.append(job['kind']) or {}).run_once()
        self.assertEqual(calls, [], '无变化扫描不应再次启动原步骤模型')

    def test_s05_pause_blocks_step_even_without_planner_update(self):
        event_id = self.with_step('remind_makeup')
        message = self.incoming()
        self.pause(message, event_id)
        Worker(self.app, self.answer_service).run_once()
        calls = []
        Scheduler(self.app).tick()
        Worker(self.app, lambda job, tools: calls.append(job['kind']) or {}).run_once()
        self.assertNotIn('step', calls,
                         'Planner暂未更新Event时暂停仍须使催课退出执行队列')
        self.assertEqual(channel_for(self.app).count(), 1)  # only the requested reply
        self.assertEqual(self.app.event('f', event_id)['status'], 'pending')
        self.assertEqual(self.app.event('f', event_id)['remaining_work'], ['lesson-a'])

    def test_s14_new_progress_invalidates_next_day_daily_snapshot(self):
        event_id = self.create()['a']
        self.app.clock = Clock('2026-10-04T08:00:00+08:00')
        daily_token, daily_args = self.daily([], refs=[event_id])
        evidence = self.evidence(lesson_ref='lesson-a')
        report_id = self.report([event_id], [evidence])
        self.call(daily_token, 'planner.commit_daily', daily_args, 'STALE_INPUT')
        self.assertFalse(any(p['business_date'] == '2026-10-04'
                             for p in self.app.store.list('plan', 'f')))
        self.assertTrue(self.app.event('f', event_id)['update_pending'])
        report = self.app.store.get('report', self.app.report_key('f', report_id, 1), 'f')
        self.assertIsNone(report['resolution'])

    def test_review_session_rejects_all_outbound_even_normal_reply(self):
        event = self.goal('a')
        event.update(status='waiting', waiting={
            'reason': '等待用户恢复', 'resume_condition': '用户主动联系',
            'review_at': '2026-10-03T08:30:00+08:00',
        })
        event_id = self.create([event])['a']
        message = self.incoming()
        Worker(self.app, self.answer_service).run_once()
        Scheduler(self.app).tick()
        attempts = []

        def review(job, tools):
            self.assertEqual(job['kind'], 'review')
            attempts.append(tools.call('wechat.send_message', self.send_event(event_id)))
            attempts.append(tools.call('wechat.send_message', self.reply(message)))
            return {'wait': True, 'reason': '没有新信息，继续等待'}

        Worker(self.app, review).run_once()
        self.assertEqual([r['error']['code'] for r in attempts], ['NO_CONTACT_RUN', 'NO_CONTACT_RUN'])
        self.assertEqual(channel_for(self.app).count(), 1)

    def test_s10_waiting_without_review_survives_two_days_and_restart(self):
        event = self.goal('a')
        event.update(status='waiting', waiting={
            'reason': '恢复后家长联系', 'resume_condition': '用户主动联系',
        })
        event_id = self.create([event])['a']
        original = self.app.event('f', event_id)
        for date in ('2026-10-04', '2026-10-05'):
            self.app.close()
            self.app = Application(self.path, Clock(date + 'T09:00:00+08:00'))
            token, args = self.daily([], refs=[event_id])
            self.call(token, 'planner.commit_daily', args)
            Scheduler(self.app).tick()
            self.assertIsNone(JobQueue(self.app).claim('no-contact'))
            current = self.app.event('f', event_id)
            self.assertEqual(current['status'], 'waiting')
            self.assertEqual(current['due_at'], original['due_at'])
            self.assertEqual(current['remaining_work'], ['lesson-a'])
        self.assertEqual(channel_for(self.app).count(), 0)

    def test_s09_unknown_original_step_cross_day_never_resends(self):
        event_id = self.with_step('remind_makeup')
        self.app.mock_send_outcome = 'unknown'
        original = self.call(self.service, 'wechat.send_message', self.send_event(event_id))['data']
        self.assertEqual(original['stage'], 'unknown')
        self.app.close()
        self.app = Application(self.path, Clock('2026-10-04T09:00:00+08:00'))
        token, args = self.daily([], refs=[event_id])
        self.call(token, 'planner.commit_daily', args)
        replay = self.call(self.service, 'wechat.send_message', self.send_event(event_id))['data']
        self.assertEqual(replay['id'], original['id'])
        self.assertEqual(channel_for(self.app).count(), 1)
        self.assertEqual(self.app.event('f', event_id)['status'], 'pending')
        confirmed = self.call(self.channel, 'wechat.mock_receipt', {
            'request_id': self.key(), 'operation_id': original['id'], 'stage': 'sent',
        })['data']
        self.assertEqual(confirmed['id'], original['id'])
        self.assertEqual(len([m for m in self.app.store.list('message', 'f') if m['direction'] == 'outbound']), 1)
        self.assertEqual(self.app.event('f', event_id)['remaining_work'], ['lesson-a'])


# Reuse fixture helpers without executing the inherited stage-one suite again.
for _name in baseline.CoreTests.__dict__:
    if _name.startswith('test_'):
        setattr(ScenarioAcceptance, _name, None)
