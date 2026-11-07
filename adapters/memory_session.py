"""Native nightly task envelope and memory-only MCP; no provider client."""
import json
import sys
import preference_memory as m
import memory_protocol as protocol
import sql_tools as s

NAMES = frozenset({'memory_consolidation_context','memory_evidence_read','memory_consolidation_prepare','memory_consolidation_commit'})


def compact_page(page):
    # Remove duplicate policy and storage bookkeeping, never evidence text or meaning.
    result={k:v for k,v in page.items() if k!='rules'}
    result['items']=[{k:v for k,v in item.items() if k not in ('scope','hash')} for item in page['items']]
    result['existing_memories']=[{k:v for k,v in item.items() if k not in ('scope','event_id','usage')} for item in page['existing_memories']]
    return result


def call(name,args):
    if name not in NAMES:raise ValueError('memory_only_tool_not_allowed')
    __import__('experiment_runtime').verify()
    functions={'memory_consolidation_context':m.consolidation_context,'memory_evidence_read':m.evidence_read,
               'memory_consolidation_prepare':m.consolidation_prepare,'memory_consolidation_commit':m.consolidation_commit}
    def operation():
        try:
            value=functions[name](**args)
            return compact_page(value) if name=='memory_consolidation_context' else value
        except ValueError as error:raise __import__('agent_plan').ServiceValidationError(str(error)) from error
    return __import__('observability').invoke(name,args,operation,origin='model')


def no_memory_evidence(page):
    if page['has_more'] or page['repair_of']:return False
    # These are unexecuted internal scheduling placeholders, not family reports
    # or service outcomes. Keep their raw journal and counts, but do not ask a
    # model to infer a preference from them. All other log types still run.
    return all(item['kind']=='task' and item['body'].get('kind') in ('plan','completion_plan')
        and item['body'].get('status') in ('planned','expired') and item['body'].get('result') is None
        for item in page['items'])


def launch(event_id):
    __import__('experiment_runtime').verify()
    audit=__import__('observability')
    page=audit.invoke('memory_consolidation_context',{'event_id':event_id},lambda:m.consolidation_context(event_id),origin='host')
    if no_memory_evidence(page):
        # No new/changed source can support a new memory. Time-based expiry is
        # already enforced by recall; this does not infer anything from silence.
        packet={'event_id':event_id,'expected_revision':page['revision'],'reviewed_through_sequence':page['through_sequence'],
                'entries':[],'changes':[],'no_change_reason':'本窗口为空或只有未执行的内部规划占位状态，无家庭新证据及实际服务结果；保留原始日志，不新增、强化或撤销记忆，到期有效性沿用读取时校验。'}
        prepared=audit.invoke('memory_consolidation_prepare',packet,lambda:m.consolidation_prepare(**packet),origin='host')
        args={'event_id':event_id,'prepared_token':prepared['prepared_token']}
        result=audit.invoke('memory_consolidation_commit',args,lambda:m.consolidation_commit(**args),origin='host')
        return {'completed':True,'model_required':False,'result':result}
    envelope={'family_identity':s.family_profile(),'business_time':m.clock().isoformat(),'memory_context':compact_page(page)}
    return {'completed':False,'model_required':True,'prompt':m.INSTRUCTION+'\n本轮为独立夜间记忆任务，event_id='+event_id+
        '。以下首批材料由宿主通过实际context接口完整装载，不是摘要。先阅读它；不要重复取首页。has_more为true时，用next_sequence继续读取，不能越页。'
        '保留原文核对和prepare后的review复核；确认忠实后commit。成功提交即完成，无需最终汇报，也不向家庭发消息。\n'+s.encoded(envelope)}


def completion(event_id,prepared_token):
    with m.database() as (c,sc):
        row=c.execute('SELECT r.result_hash,r.result,e.status FROM memory_runs r JOIN events e ON e.id=r.event_id WHERE r.event_id=? AND r.scope=?',(event_id,sc)).fetchone()
        ok=bool(row and row['status']=='done' and row['result_hash']==m.digest(prepared_token))
        return {'completed':ok,'event_id':event_id,**({'revision':json.loads(row['result'])['revision']} if ok else {})}


if __name__=='__main__':
    if len(sys.argv)>1:
        result=launch(sys.argv[2]) if sys.argv[1]=='launch' else completion(sys.argv[2],sys.argv[3])
        print(s.encoded(result))
    else:
        import base_mcp
        base_mcp.TOOLS=[x for x in protocol.tools(m.KEYS) if x[0] in NAMES]
        base_mcp.call=call
        base_mcp.main()
