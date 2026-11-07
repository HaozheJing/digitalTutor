"""Experiment host entry: load actual passive-turn skills before model execution."""
import json
import sys
import business
import sql_tools as s


def prepare(event_id):
    from experiment_runtime import verify
    if not (verify() or __import__('manual_workbench').verify() or __import__('family_lab_bridge').verify()):
        raise ValueError('dialogue_preload_is_experiment_only')
    rows = s.read('SELECT kind,status FROM events WHERE id=?', (event_id,))
    if not rows or rows[0]['status'] != 'dispatched':
        raise ValueError('dispatched_event_required')
    media=__import__('host_tool_steps').event_materials(event_id)
    result = business.task(event_id, passive_view=True)
    if media:result['prompt']+='\n<host_voice_materials>'+s.encoded(media)+'</host_voice_materials>'
    if rows[0]['kind'] != 'passive':
        return result
    import agent_plan as ap
    import observability as audit
    names = ['service-policy', *business.passive_reply_skills()]
    frozen = s.R / 'config/frozen-experiment-input.json'
    if frozen.exists():
        data = json.loads(frozen.read_text(encoding='utf8'))
        selected = data.get('scenario', {}).get('preload_skill')
        if selected:
            if not isinstance(selected, str) or selected not in data.get('config', {}).get('skills', {}):
                raise ValueError('selected_skill_not_in_frozen_config')
            if selected not in names:names.append(selected)
    loaded = []
    for name in names:
        item = audit.invoke('service_skill', {'name': name},
                            lambda name=name: ap.load_skill(name), origin='host')
        if not item.get('body') or not item.get('bundle_sha256'):
            raise ValueError('required_skill_body_missing:' + name)
        loaded.append(item)
    # Never synthesize read records or remove guards. A business Skill is loaded
    # only when explicitly selected by the frozen experiment scenario.
    # Each dispatch reloads current bodies, including after a version change.
    manifest = [{'name': x['name'], 'sha256': x['sha256'],
                 'bundle_sha256': x['bundle_sha256']} for x in loaded]
    context = __import__('tool_surface').guidance()+('本轮必需 Skill 正文及附属规则已由宿主实际读取并登记，见下方 loaded_skills。'
               '请阅读后执行，无需为登记重复调用 skill 或 service_skill。'
               '原任务中“读取当前规则”的要求由这些正文满足；具体业务 Skill 仍按需用 service_skill 读取，'
               '共享参考资料用 service_reference，知识库用 knowledge_search / course_search / course_read。'
               '这些正文只提供当前规则，不增加工具权限，也不是资料已核实或操作已完成的证明。\n')
    if __import__('capability_surface').enabled():
        context=__import__('tool_surface').current_instructions(context.replace('skill 或 service_skill','skill_read'))
    return {**result, 'prompt': context + ('可复用的历史材料索引（需要时evidence_read，ref.kind=material；不替代动态事实刷新）：' if __import__('tool_surface').enabled('knowledge') else '可复用的历史材料索引（需要时material_read；不替代动态事实刷新）：')+s.encoded(__import__('passive_context').materials())+'\n' + '<loaded_skills>\n' +
            s.encoded(loaded) + '\n</loaded_skills>\n' + result['prompt'],
            'context_version': 'experiment-dialogue-materials-v2', 'prepared_skills': manifest}


if __name__ == '__main__':
    print(json.dumps(prepare(sys.argv[1]), ensure_ascii=False))
