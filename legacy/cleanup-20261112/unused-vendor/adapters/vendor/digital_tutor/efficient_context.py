"""Opt-in model-facing projections. Authoritative records and validation stay intact."""
from copy import deepcopy
import os

FEATURES = frozenset({'dialogue', 'knowledge', 'receipts', 'schema', 'bindings','responsibility','catalog_focus'})


def configured_features():
    value = os.environ.get('DIGITAL_TUTOR_CONTEXT_FEATURES', '')
    names = set(filter(None, (x.strip() for x in value.split(','))))
    if names - FEATURES:
        raise ValueError('Unknown context feature')
    return frozenset(names)


def enabled(app, name):
    return name in getattr(app, 'context_features', ())


def _thin_anchor(row):
    value = deepcopy(row)
    tr = value.get('transcript')
    if not isinstance(tr, dict):
        return value
    # Only import bookkeeping and provably redundant values are removed.
    tr.pop('normalization_id', None)
    if tr.get('source_ids') == [value.get('source_id')]:
        tr.pop('source_ids')
    if tr.get('original_body') == value.get('body'):
        tr.pop('original_body')
    return value


def project_context(context):
    """Keep all selected bodies, changed raw text, identities and business state."""
    if not isinstance(context.get('dialogue_context'), dict):
        return deepcopy(context)
    out = deepcopy(context)
    src = context['dialogue_context']; dst = out['dialogue_context']
    records = list(src.get('transcript', []))
    index = {}
    for row in records:
        key = (row['message_id'], row['version'])
        if key in index and index[key] != row:
            raise ValueError('Conflicting message identity')
        index[key] = row
    extra = []
    for name in ('current_inputs', 'preceding_outbound'):
        refs = []
        for row in src.get(name, []):
            # The live question is worth repeating: avoid a lookup for each subpoint.
            if name=='current_inputs':
                refs.append(_thin_anchor(row)); continue
            key = (row['message_id'], row['version'])
            if key in index and index[key] != row:
                # Preserve a distinct view instead of silently binding a stale body.
                refs.append(_thin_anchor(row)); continue
            if key not in index:
                index[key] = row; extra.append(row)
            refs.append({'message_ref': row['message_id'], 'version': row['version']})
        dst[name] = refs
    dst['transcript'] = [_thin_anchor(row) for row in records]
    if extra:
        dst['reference_messages'] = [_thin_anchor(row) for row in extra]
    groups = []
    for group in src.get('utterance_groups', []):
        rows = []
        for mid, body in zip(group.get('message_ids', []), group.get('parts', [])):
            matches = [row for row in index.values() if row['message_id'] == mid and row['body'] == body]
            if len(matches) != 1: break
            rows.append(matches[0])
        if (not rows or len(rows) != len(group.get('message_ids', []))
                or len(rows) != len(group.get('parts', []))
                or any(row.get(k) != group.get(k) for row in rows
                       for k in ('direction', 'speaker_role', 'historical_reference'))
                or rows[0]['occurred_at'] != group.get('first_at')
                or rows[-1]['occurred_at'] != group.get('last_at')):
            groups.append(deepcopy(group)); continue
        known = {'direction','speaker_role','historical_reference','first_at','last_at','message_ids','parts'}
        groups.append({**{k:deepcopy(v) for k,v in group.items() if k not in known},
                       'message_refs':[{'message_ref':r['message_id'],'version':r['version']} for r in rows]})
    dst['utterance_groups'] = groups
    dst['view_reading_rule'] = ('message_ref与version精确引用transcript或reference_messages中的同一记录。'
        '分组message_refs按原顺序给出连续发言，各正文、首末时刻和来源身份取自对应消息；分组不确认同一说话人，也不合并责任。'
        'transcript.original_body省略仅表示与body逐字相同；有差异的原转写和置信度仍保留。导入批次等审计信息留在原记录。'
        'messages中的媒体缺失标记、意图、当前版本和待答状态仍须核对。历史教师话语不是智能体交付或当前事实。')
    return out


def _pick(value, keys):
    return {k:deepcopy(value[k]) for k in keys if k in value}


def project_result(tool, result, features):
    if not result.get('ok') and isinstance(result.get('error',{}).get('input_schema'),dict):
        out=deepcopy(result);error=out['error']
        if tool=='wechat.send_message' and 'bindings' in features:
            from .reply_segments import send_schema
            error['input_schema']=send_schema(error['input_schema'])
            for detail in error.get('details',[]):
                if isinstance(detail.get('loc'),list):
                    detail['loc']=[{'body':'body_segments','response_excerpt':'response_segment_index'}.get(v,v) for v in detail['loc']]
        if 'schema' in features:error['input_schema']=strip_schema_titles(error['input_schema'])
        return out
    if not result.get('ok') or not isinstance(result.get('result'), dict):
        return result
    value = result['result']
    if tool == 'knowledge.list' and 'knowledge' in features:
        docs = value.get('documents', [])
        if not docs: return result
        sources = [d.get('provenance', {}) for d in docs]
        common = {k:deepcopy(v) for k,v in sources[0].items()
                  if all(k in p and p[k] == v for p in sources)}
        projected = []
        for doc in docs:
            d = deepcopy(doc)
            # Integrity is checked on the original result by the material reader.
            d.pop('locator_source_sha256', None)
            d['provenance'] = {k:v for k,v in d.get('provenance', {}).items() if k not in common}
            projected.append(d)
        return {**result, 'result':{**value, 'documents':projected, 'shared_provenance':common,
            'provenance_reading_rule':'各document.provenance与shared_provenance合并才是该资料完整来源；条目字段覆盖共同字段。目录不是正文证据，完整候选均保留。'}}
    if 'receipts' not in features: return result
    if tool in {'message.interpret', 'wechat.send_message'} and isinstance(value.get('data'),dict):
        # Exclude only identified repeat/audit fields; unknown future fields survive.
        omit = {'body'} if tool == 'message.interpret' else {
            'body','request','body_source_digest','content_digest','material_approval_digest'}
        return {**result, 'result':{**value,
            'data':{k:deepcopy(v) for k,v in value['data'].items() if k not in omit},
            'receipt_view':'这是写入回执；完整原记录仍保存在宿主。stage、版本和未答状态按本回执，不能把ok当送达。'}}
    # Recovery reads and final outcome retain full semantics in this revision.
    return result


def strip_schema_titles(schema):
    """Do not strip user properties literally named title or schema examples."""
    if not isinstance(schema, dict): return deepcopy(schema)
    out = {k:deepcopy(v) for k,v in schema.items() if k != 'title'}
    for key in ('properties', '$defs', 'definitions', 'patternProperties'):
        if isinstance(out.get(key),dict):
            out[key] = {k:strip_schema_titles(v) for k,v in out[key].items()}
    for key in ('items','additionalProperties','not','if','then','else','contains'):
        if isinstance(out.get(key),dict): out[key] = strip_schema_titles(out[key])
    for key in ('anyOf','allOf','oneOf','prefixItems'):
        if isinstance(out.get(key),list): out[key] = [strip_schema_titles(v) for v in out[key]]
    return out
