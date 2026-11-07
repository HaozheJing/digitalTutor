"""Typed model query adapters, independent of source and authority checks."""
def course_args(uri=None,class_type=None,lesson_no=None,detail=None):
    import course_service,course_navigation
    exact=class_type is not None or lesson_no is not None
    if uri is not None and exact:raise ValueError('choose_uri_or_exact_lesson')
    if uri is None:
        if not isinstance(class_type,str) or type(lesson_no) is not int or lesson_no<1:raise ValueError('course_reference_required')
        item=course_service.entry(class_type,lesson_no)
        if item is None:return None
        registered=course_navigation.resolve(item['uri'])
        if any(registered.get(k)!=item.get(k) for k in ('class_type','lesson_no','sha256')):raise ValueError('course_indexes_disagree')
        uri=item['uri'];detail=detail or 'full'
    else:course_navigation.resolve(uri);detail=detail or 'overview'
    if detail not in course_navigation.DETAILS:raise ValueError('invalid_course_detail')
    return {'uri':uri,'detail':detail}

def evidence_target(ref,section=None,path=None,offset=0):
    if not isinstance(ref,dict) or set(ref)!= {'kind','id'} or not isinstance(ref['id'],str) or not ref['id']:raise ValueError('typed_evidence_ref_required')
    if type(offset) is not int or offset<0:raise ValueError('invalid_evidence_offset')
    if ref['kind']=='material':
        if section is not None or path is not None:raise ValueError('material_ref_does_not_accept_context_path')
        return 'material_read',{'material_id':ref['id'],'offset':offset}
    if ref['kind']=='context':
        if not isinstance(section,str) or not section:raise ValueError('context_section_required')
        return 'context_page',{'context_id':ref['id'],'section':section,'path':path,'offset':offset}
    raise ValueError('unknown_evidence_kind')

def knowledge_result(value,budget=12000):
    """Expose whole canonical bodies or references; never clip factual text."""
    if not isinstance(value,dict) or not isinstance(value.get('matches'),list):return value
    result={**value,'matches':[]}
    for match in value['matches']:
        item=dict(match);body=item.get('body');size=len(body) if isinstance(body,str) else 0
        if size and size<=budget:
            item['content_included']='full';budget-=size
            item['read_guidance']='本次已含同版本正文，不必重复knowledge_read；审核状态仍以源字段为准。'
        elif size:
            item.pop('body');item['content_included']='reference';item['body_chars']=size
            item['read_guidance']='正文超过本次检索预算，按需knowledge_read(document_id)；若返回material_ref则evidence_read分页。'
        else:item['content_included']='reference'
        result['matches'].append(item)
    return result
