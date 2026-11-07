"""Single-stage experiment MCP: original operations with additive result labels."""
import mcp_server as original
from tool_result_contract import annotate


def install():
    from experiment_runtime import verify
    if not (verify() or __import__('manual_workbench').verify() or __import__('family_lab_bridge').verify()):raise ValueError('result_contract_is_experiment_only')
    operation = original.business_call
    def wrapped(name, args):
        # Original business_call may normalize args in-place; keep the query scope.
        request = dict(args)
        result=operation(name,args)
        if name=='request_human_confirmation' and __import__('family_lab_bridge').configuration():
            cfg,lab=__import__('family_lab_bridge').connection()
            alert=result.get('alert',{})
            case=lab.case(cfg['run_id'],cfg['family_id'],'human:'+str(request.get('category','needs_teacher')),
                          [alert['id']] if alert.get('id') else [],request.get('reason','需要老师处理'),70)
            lab.control(cfg['run_id'],cfg['family_id'],'takeover',True,'model_requested_handoff')
            if request.get('proposed_reply'):lab.save_draft(case,request['proposed_reply'],0,ai=True)
            result={**result,'teacher_case_id':case,'ai_resumption':'teacher_explicit_only'}
        return annotate(name, request, result)
    original.business_call = wrapped


if __name__ == '__main__':
    install()
    original.base_mcp.main()
