"""Adapt legacy expression-only prose after copying teacher business Skills."""
from pathlib import Path
REPLACEMENTS = {'answer-family-question/SKILL.md': [('按五步判断将家长或孩子的问题分派到Q01–Q07，先检索OpenViking和真实学情，答疑结束再做内部回流。', '对需要业务答疑的家庭问题选择Q01–Q07，按需查证后直接回答当前问题；普通接话不启动完整答疑流程。'), ('1. 识别家长本人、孩子借手机、家长代问；分别成人语态、儿童语态、双层讲法。称呼遵循 reply-style，每条都带孩子昵称或已确认的“昵称＋爸爸／妈妈”；身份未明时可用孩子昵称承接给孩子的话，需要家长处理时先确认关系，不用泛称、不猜身份。', '1. 根据当前原话识别家长本人、孩子或家长代问，采用适合对象的讲法。连续交流直接接话，不重复称呼；需要称呼时按 reply-style 使用已确认身份。对象不明但不影响答复时中性表达，只有影响解释或授权时才确认，不为称呼额外追问。'), ('3. 有情绪先接住具体感受，再核实事实与期待；接情绪不等于确认未核实事实。', '3. 有情绪时认真处理具体问题，按需要表达体谅，不默认先复述或安慰。情绪承接不等于确认未核实事实。'), ('## 五步判断', '普通问候、同意、致谢以及没有新增问题的确认，按 tutor-main 与 reply-style 自然短答即可，不为这些接话启动检索、全套答疑路由或新的建议。对实际业务问题使用下列判断。\n\n## 五步判断')], 'respond-child-sharing/references/examples.md': [('孩子分享一件日常小事时，点出他实际提到的细节并自然问一句。', '孩子分享日常小事时，回应有意思的那一点即可；需要了解更多才能帮助他时再问，不固定复述细节或追加问题。')], 'answer-faq/SKILL.md': [('只有本次证据足够才给简洁答案，至多1句有用延伸；', '只有本次证据足够才直接回答当前问题；必要条件保留，答案完整就结束，不默认追加延伸内容；')], 'service-policy/references/qa.md': [('有情绪先回应具体感受，再给事实证据和可兑现方案。', '有情绪时认真回应具体问题，按需要表达体谅并提供事实或可执行方案，不固定先复述、共情再建议。')]}

def adapt(skills_root):
    root = Path(skills_root)
    changed = []
    for path in root.rglob('*.md'):
        relative = path.relative_to(root).as_posix()
        before = path.read_text(encoding='utf-8')
        after = before.replace('两条分别带正确称呼', '连续交流及后续分片不重复称呼，按当前reply-style表达')
        for old, new in REPLACEMENTS.get(relative, []):
            if new not in after:
                after = after.replace(old, new)
        from legacy.delivery_limits import with_current_limits
        after = with_current_limits(after)
        if after != before:
            path.write_text(after, encoding='utf-8')
            changed.append(relative)
    return changed
