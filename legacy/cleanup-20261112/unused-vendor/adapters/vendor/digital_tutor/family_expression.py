"""Reject implementation narration before any outbound side effect."""
import re
from .domain import require

INTERNAL = re.compile(
    r'(?<![a-z])(?:planner(?:_daily|_progress)?|skill[s]?|reply_scope|no_contact|mock|'
    r'service\.[a-z_]+|memory\.[a-z_]+|event-[a-f0-9]+|run-[a-f0-9]+)(?![a-z])'
    r'|(?:移交|交接|提交|同步|转交|登记).{0,12}(?:规划器|规划智能体|后台|内部|记忆|账本)'
    r'|(?:作业配置|数据字段|字段缺失|本周可见|没有每课的具体完成时间|不额外推断掌握程度|长期记忆|记忆库|记忆整理|内部记录|后台记录|操作回执|完成槽位|任务队列|原生运行|发送门禁)'
    r'|(?:记录|保存|写入|更新|整理).{0,8}(?:您的偏好|你的偏好|联系偏好|记忆|交接备忘)'
    r'|(?:我会|已经|已|给您|帮您).{0,4}记(?:住|下)', re.I)

PRAISE_RELAY = re.compile(r'(?:请|麻烦).{0,8}(?:替我|帮我).{0,4}(?:告诉|转告|转达)|(?:请您|请你|麻烦您).{0,4}转告.{0,5}(?:孩子|小朋友)|(?:请|麻烦).{0,6}(?:转述|转达).{0,6}(?:鼓励|表扬)')


def require_family_expression(body):
    require(not (PRAISE_RELAY.search(body) and re.search(r'鼓励|表扬|真棒|坚持|完成|认真|阅读|孩子',body)), 'DIRECT_CHILD_PRAISE_REQUIRED', '鼓励请直接对孩子说你，有可信昵称时再使用；不要请家长转述，不向家庭复述本修改要求。')
    require(not INTERNAL.search(body), 'INTERNAL_PROCESS_DISCLOSURE',
        '正文包含内部算法或记忆/交接叙述，尚未发送。请删除技术过程和记忆复述，'
        '仅回应家长/孩子需要的结论或已生效安排；例如“好的，之后晚上八点联系您”。'
        '不要将本错误、工具名或修改过程告诉家庭。使用新request_id重试。')


def require_course_date(app, event, body):
    """Catch the observed stale-day claim without changing historical messages."""
    if event.get('service_action_id')!='S02':return
    original=(event.get('source_identity') or {}).get('period_start')
    if not original:return
    from .domain import timestamp,SHANGHAI
    day=timestamp(original).astimezone(SHANGHAI).date()
    stale_today=day<app.clock.now().date() and re.search(r'今天(?:的)?第[0-9一二三四五六七八九十]+课.{0,8}(?:完成|学完|读完)',body)
    require(not stale_today,'COURSE_DATE_MISMATCH','此项原课日为'+day.isoformat()+'，跨日补发不能说今天完成；请用原日期或那一课，保留真实内容，不向家庭解释内部检查。')


def require_family_address(app, family, body, binding):
    """Opt-in lab safeguard; let the model revise before sending or media spend."""
    policies=app.store.list('lab_expression_policy',family)
    if not any(p.get('require_address') is True for p in policies):return
    if app.store.family(family)['mock']!=1:return
    run_id=binding.get('run_id')
    if run_id and any(o.get('run_id')==run_id and o.get('stage') in {'sent','delivered','read'}
                      for o in app.store.list('operation',family)):return
    # A native task is not a new human conversation. Continuous replies should
    # not be forced to repeat a greeting. Proactive tasks and role switches do.
    if not binding.get('event_id'):
        from datetime import datetime,timedelta
        messages=app.store.list('message',family)
        sent=[(i,m) for i,m in enumerate(messages) if m.get('direction')=='outbound' and not m.get('historical_reference')]
        if sent:
            index,last=sent[-1]
            switch=any(m.get('direction')=='inbound' and re.search(r'现在是(?:孩子|家长)本人发言|(?:我|孩子)(?:叫|是)|手机给孩子|我是.{0,12}(?:家长|妈妈|爸爸)',m.get('body','')) for m in messages[index+1:])
            before=[m for m in messages[:index+1] if m.get('direction')=='inbound']
            after=[m for m in messages[index+1:] if m.get('direction')=='inbound']
            previous_role=(before[-1].get('source_metadata') or {}).get('speaker_role') if before else None
            for m in after:
                role=(m.get('source_metadata') or {}).get('speaker_role')
                if role in {'parent','child'} and role!=previous_role:switch=True
                previous_role=role
            try:gap=app.clock.now()-datetime.fromisoformat(last['occurred_at'])
            except (KeyError,ValueError,TypeError):gap=None
            if not switch and gap is not None and timedelta(0)<=gap<=timedelta(minutes=30):return
    names=set()
    from .family_profile import view
    profile=view(app,family)
    names.update(x for x in (profile['child_name'],profile['child_nickname']) if x)
    for m in app.store.list('message',family):
        if m.get('direction')!='inbound':continue
        if m.get('occurred_at','')>app.clock.iso():continue
        names.update(re.findall(r'(?:我叫|我是|孩子叫|小名叫|小名是|名字叫|名字是)\s*([\u4e00-\u9fffA-Za-z]{1,12})(?=[，。！!、\s]|$)',m.get('body','')))
    addresses={'家长您好','家长你好','家长','小朋友','宝贝','同学','您好','你好呀','你好啊','你好',*names,*(n+'家长' for n in names)}
    prefix=r'^[^\w\u4e00-\u9fff]*(?:'+ '|'.join(re.escape(x) for x in sorted(addresses,key=len,reverse=True))+r')(?:[，,！!。～~：:\s]|$)'
    require(bool(re.search(prefix,body)), 'FAMILY_ADDRESS_REQUIRED',
            '本次主动触达、对话开头或对象切换的消息尚未发送：请在开头自然带上称呼。有家庭原话确认的昵称就使用；否则直接鼓励孩子用“小朋友”，对家长用“家长您好”，身份不确定用“你好”。不要编造姓名，也不要把本检查告诉家庭。请修正文案后使用新request_id；生成媒体前同样检查caption或讲稿。')
