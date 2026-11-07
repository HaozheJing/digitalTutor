"""Bounded native continuation for a missing business receipt, never text execution."""
import json
import time
from .domain import BusinessError

# A repair can observe facts and commit only the existing outcome contract.
READ_TOOLS = frozenset({'family.get_profile','state.read','context.read','learning.schema','learning.query_sql',
    'learning.summary','wechat.read_messages','wechat.read_all_messages','wechat.read_message_stream',
    'wechat.get_operation','resource.read','knowledge.list','knowledge.read','skills.list','skills.read',
    'memory.read','human.get_requests'})


def recover(app, token, agent, value, *, system, run_id, work, boundary=None,
            budget_seconds=180, started=None, clock=time.monotonic, max_attempts=2):
    """The same native agent/history performs real calls through the existing bridge.

    No final-response parsing, fabricated tools, host-authored success or replayed
    writes. Revocation, run budget, source/lease checks and outcome validation apply.
    """
    family,binding=app.auth(token,{'service','planner_daily','planner_progress','memory_daily'})
    if binding['role']!='service' or not binding.get('lease') or not app.strict_runtime:return value
    started=clock() if started is None else started
    attempts=[];total_calls=value.get('api_calls') or 0
    original_budget=agent.run_budget_seconds
    prior_mode=getattr(app,'outcome_repair_only',False)
    def exists():
        return bool(app.store.connection.execute(
            "SELECT 1 FROM records WHERE kind='run_outcome' AND family_id=? AND id=?",(family,run_id)).fetchone())
    try:
        for index in range(max_attempts):
            if exists() or not value.get('completed') or value.get('interrupted') or value.get('error'):break
            app.auth(token,{'service'})
            if boundary:
                boundary.check()
                if boundary.reason:break
            remaining=budget_seconds-(clock()-started)
            if remaining<=5:break
            history=value.get('messages')
            if not isinstance(history,list) or not history:break
            # Preserve the exact returned history, including malformed final text,
            # as evidence. Never transform that text into executable tool calls.
            prompt=(f'宿主结束回执检查：本次 run_id={run_id} 尚无真实 service.record_outcome 回执。'
                '上一段普通文字不是工具调用，不会执行或发送。请通过真实工具补交本轮结束回执：'
                'direct 模式调用 dt_service_record_outcome；discovery 模式用 dt_tools_describe 查明后由 dt_tools_call 调用 service.record_outcome。'
                '本次补交仅允许只读核对与 service.record_outcome；禁止再次发送、改计划、提报告或重复业务写入。'
                '按已成功回执记录实际效果；仍欠未来交付或人工资料时用 waiting 或 needs_input，保留 remaining_work 和 resume_condition。'
                'refs 可以省略，由宿主取真实记录。不得把未来安排或人工请求当成已交付。')
            attempts.append({'attempt':index+1,'previous_final_response':value.get('final_response',''),
                             'history_messages':len(history),'remaining_budget_seconds':round(remaining,3),
                             'host_message':prompt})
            app.outcome_repair_only=True
            agent.run_budget_seconds=remaining
            value=agent.run_conversation(prompt,system_message=system,conversation_history=history,
                persist_user_platform_id=run_id+':outcome-repair-'+str(index+1))
            total_calls+=value.get('api_calls') or 0
            attempts[-1].update(receipt_present=exists(),completed=value.get('completed'),
                                final_response=value.get('final_response',''))
        if attempts:
            value={**value,'api_calls':total_calls,'outcome_recovery_attempts':len(attempts)}
        return value
    finally:
        agent.run_budget_seconds=original_budget
        app.outcome_repair_only=prior_mode
        if attempts:
            (work/'outcome-recovery.json').write_text(json.dumps({'run_id':run_id,'attempts':attempts,
                'receipt_present':exists(),'system_unchanged':True,'text_tool_calls_executed':False,
                'business_writes_replayed':False},ensure_ascii=False,indent=2),encoding='utf-8')
