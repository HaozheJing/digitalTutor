"""Business-only model projection; raw records remain available to operators."""
ENV_KEYS={'simulation','test_run_id','test_instructions','test_calendar_basis','scenario',
          'source_context','mock_data','fixture','test_mode','frozen','archive',
          'boundaries','instruction','test_instructions','nickname_basis','birthday_basis',
          'guardian_role_basis','binding_source','completion_source','source',
          'unlocked','unlocked_at','unlock_at'}


def project(value):
    if isinstance(value,list):return [project(x) for x in value]
    if not isinstance(value,dict):return value
    result={k:project(v) for k,v in value.items() if k not in ENV_KEYS}
    if result.get('binding_status')=='bound_test_wechat':result['binding_status']='bound'
    if result.get('trigger_status')=='eligible_fixture':result['trigger_status']='eligible'
    if result.get('status')=='mock_sent':result['status']='sent'
    if result.get('status')=='mock_recorded':result['status']='recorded'
    if result.get('clock')=='test_business_time':result['clock']='business_time'
    # Preserve the explicit profile relationship; stripping a provenance label
    # must not turn a known mother/father into an unknown identity.
    if value.get('simulation'):result.pop('notice',None)
    return result
