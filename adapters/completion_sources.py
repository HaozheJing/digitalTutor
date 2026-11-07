"""Course-system adapter registry, selected by trusted host routing.

An adapter resolves enrollment/lesson identity and validates authoritative facts.
It does not choose a Skill, write a plan or send a message. No provider name from
the request body is ever imported or executed.
"""
import test_mode as tm


class MockCourseSource:
    def resolve(self, payload, run_id, state):
        from completion_events import enrollment
        if not state or not state.get('frozen') or state['run_id'] != run_id:
            raise ValueError('测试轮次已变化，请刷新后再操作')
        if payload['uid'] != state['uid']:
            raise ValueError('事件不属于当前绑定的模拟家庭')
        data = tm.fixture_data(state, include_events=False)
        lesson = next((l for l in data['learning_daily'] if l['id'] == payload['lesson_id']), None)
        if not lesson or enrollment(data, lesson)['id'] != payload['enrollment_id']:
            raise ValueError('课节与报名记录不匹配')
        if not lesson.get('unlocked_at', lesson['date'] + 'T07:00:00+08:00') <= payload['completed_at'] <= state['as_of']:
            raise ValueError('完课时间须在课程解锁后，且不能晚于当前业务时间')
        return data


SOURCES = {'mock-course-system': MockCourseSource(), 'workbench': MockCourseSource()}


def resolve(source, payload, run_id, state):
    adapter = SOURCES.get(source)
    if adapter is None:
        import json,sql_tools as s
        path=s.R/'config/completion-ingress.json'
        config=json.loads(path.read_text()) if path.is_file() else {}
        entry=next((x for x in config.get('sources',[]) if x.get('id')==source and x.get('enabled') is True),None)
        if config.get('enabled') is True and entry and entry.get('adapter')=='normalized-v1':
            adapter=MockCourseSource()
        else:raise ValueError('来源未配置或适配器未启用')
    return adapter.resolve(payload, run_id, state)
