"""A-owned reviewed assets and immutable native emoji package assembly."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from reply_lab import require, digest

RULE = '''# 表情选择与发送

表情包是当前回复的可选补充，不是任务，也不是每条必发。严格按“先判断要不要配图 → 需要才检索 → 看候选是否真正合适 → 一并交付”的顺序执行。以下判断在提交本轮交付之前完成，不是先发送文字再补发图片，也不向家长展示内部判断过程。

## 1. 先判断是否需要，暂不检索

先理解对方这次要表达什么、需要解决什么，并组织好必要的文字回应。结合当前语境、关系亲疏、近期交付和 task_context.emoji_assets.policy，判断一张图片是否能增加明确且合适的情绪表达价值。默认不配图；普通事实答疑、文字确认已足够、情绪或意图不确定时，不调用表情检索。对方用了 emoji、库里有图或图很可爱，都不是配图理由。不要先浏览素材再找理由发送，也不要为了凑出配图理由额外查家庭资料。

投诉、纠错、退款、健康/生病、悲伤、焦虑、拒绝或暂停联系，以及 policy.allowed 不为 true、上一轮已发过图时，直接跳过表情检索，完成必要的文字回应。明确索图可以成为配图意图，但不能绕过这些限制。图片表情包的检索规则不要求为了一个普通 Unicode emoji 搜索素材库。

## 2. 确认需要后，按沟通意图检索 OpenViking

只有判断配图有价值且程序频率允许，才确定要表达的具体意图，再调用 knowledge_search(scope="emoji", query="本轮沟通意图和完整语境", limit=3)。例如“孩子今天第一次完整讲完故事，老师温和肯定已完成的具体进步”，而不是只搜“开心”或“加油”。默认检索一次；没有适合的候选就回到纯文字，不为必须发图反复搜索。

逐个核对返回候选的 visible_text、visual_description、表达强度、suitable、avoid 和正反例，判断图中文字、动作和情绪是否适合本轮。OpenViking 提供素材与场景依据，reply-style 负责取舍；检索命中不代表必须发送。以下场景仅作判断参考，不能仅按关键词触发。

## 3. 按场景取舍候选

- 孩子完成了一件具体的事、主动分享阅读或有可核实的进展，可以选“真棒”。仅凭愿望、计划或模糊学情不表扬已经完成。
- 家长或孩子主动说愿意试试、准备开始，且没有压力或拒绝，可选“加油”。有畏难情绪时先给具体可行的建议，不用加油图代替回应。
- 双方轻松分享好消息，可以选“开心”。不能因为看到“开心”关键词就忽略否定、反话或投诉。
- 简单确认安排可以选“收到”，但已用文字表达清楚且没有情绪价值时可以不选。“成交”只适合真的达成约定，不能用于退款、价格、收费等事项。
- “来啦”只适合当前对话中回应轻松呼唤，并与本轮实质答复一起交付，不能只用它拖延、暗示实际到场或保证立即办理。“等回复/盼”只适合对方主动说马上发材料并请老师稍等的当前交流，不主动催作业、催回复，不因此增加联系。“达成共识”要求双方确已同意，不能把建议当成已确认或已办妥。
- 检索结果包含逐图视觉说明和表达强度；选图时同时检查文字、动作、关系亲疏和语境。哭泣/委屈候选不自动发送，避免把老师的失落转成家长的回应压力。正反例用于判断，不是可照抄的家长事实。
- 投诉、纠错、退款、健康/生病、悲伤、家长焦虑或要求暂停/不要联系时，不发任何表情。不要用“委屈”图扮可怜，不发开心图淡化问题。
- 每次交付最多一张，不连续两轮都发。先查看近期老师交付里的表情记录，上一轮已有表情则本轮不发。同一轮不能先发文字后新建一次表情交付，也不能为发图追加联系。
- 限频属于内部规则，不向家长解释程序机制，不编造“怕孩子记不清夸奖”等理由，不承诺把表情攒到下次发送。家长明确索要而本轮不能发图时，简短说这次用文字鼓励，并给出具体回应；没有索要就自然回复，不提表情限制。
- task_context.emoji_assets 只提供检索入口和程序频率状态，不展示全部素材；不得绕过第一步直接获取候选。
- 阅读返回素材的caption、visible_text、suitable、avoid及正反例，按实际语境自主选0或1张，只能使用本轮检索返回的精确asset_id和version。结果为空、资料变更或服务不可用时仅发文字，不猜ID、不用旧候选代替。即使命中也不必发送。
- 程序会拒绝一条消息带多张图、连续轮次发图、没有先检索就选图。若工具返回这些限制且尚未产生交付，改成纯文字重新提交，不为发图另建任务或拆出第二次交付。unknown或已受理交付继续按原回执规则处理，不重发。
- 把选择作为 emoji_refs:[{asset_id,version}] 传给本轮原有交付工具（reply_to_message、workflow_fulfill、deliver_message 等）。正文仍只写给家长/孩子的话，不写素材ID、URL、“[发送表情]”或Markdown图片。发送成功以工具回执为准。
'''

DECISION_SECTION = '''<!-- emoji-decision:start -->
## 表情包：先判断需要，再检索素材

在提交本轮回复之前，先组织好文字回应，再判断配图能否增加合适的情绪表达价值。默认不配图；普通答疑、文字已足够、严肃或负面场景、意图不确定、近期已发图或程序频率不允许时，跳过表情检索。

只有确定需要且 task_context.emoji_assets.policy.allowed 为 true，才按本轮具体沟通意图调用 knowledge_search(scope="emoji", query="完整沟通语境与表达意图", limit=3) 检索 OpenViking。不要先搜图再找理由发，也不要仅因对方用了 emoji 就配图。核对逐图文字、视觉说明、表达强度和适用/禁用场景，选择 0 或 1 张；没有适合的结果就只发文字。

选中的表情包随本轮原有交付一并提交，不先发文字再追加图片。执行细则见[表情选择与发送](references/emoji-selection.md)。以上判断只用于内部决策，不向家长展示。
<!-- emoji-decision:end -->'''


def bind_rule(skills, rule):
    skills['reply-style']['references/emoji-selection.md'] = rule
    body = skills['reply-style']['SKILL.md']
    start, end = '<!-- emoji-decision:start -->', '<!-- emoji-decision:end -->'
    if start in body and end in body:
        before, rest = body.split(start, 1)
        _, after = rest.split(end, 1)
        body = before + DECISION_SECTION + after
    else:
        old = '有表情素材时，发送前阅读[表情选择与发送](references/emoji-selection.md)，可以选择不发。'
        body = body.replace(old, DECISION_SECTION) if old in body else body.rstrip() + '\n\n' + DECISION_SECTION + '\n'
    skills['reply-style']['SKILL.md'] = body


def package(root, config, folder):
    assets = config.get('assets', [])
    if not assets:
        return {'version': 'empty', 'allowed_hosts': [], 'assets': []}
    root, folder = Path(root), Path(folder)
    publications = json.loads((root/'runtime/quality-source-snapshot/emoji-publications.json').read_text())
    registry = {'version': 'emoji-'+digest(assets)[:24], 'allowed_hosts': [], 'assets': []}
    for item in assets:
        card = item['card']; sha = card['sha256']
        require(card['review'] == 'approved', '表情未审核。', 409)
        raw = (root/'runtime/teacher-skill-workbench/reply-lab/assets'/sha).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == sha, '表情原文件摘要不匹配。', 409)
        published = publications['assets'].get(sha)
        require(published and published['sha256'] == sha, '表情尚未发布或登记摘要不符。', 409)
        parsed = urlsplit(published['url'])
        require(parsed.scheme == 'https' and parsed.hostname in publications['allowed_hosts'] and
                not (parsed.username or parsed.password or parsed.query or parsed.fragment), '表情地址无效。', 409)
        folder.mkdir(parents=True, exist_ok=True)
        (folder/sha).write_bytes(raw)
        registry['allowed_hosts'].append(parsed.hostname)
        registry['assets'].append({'asset_id': 'emoji-'+sha[:24], 'status': 'approved',
            'reviewed_by': card['reviewer'], 'sha256': sha, 'url': published['url'],
            'lab_asset_version': item['version'],
            **({'knowledge':item['knowledge']} if item.get('knowledge') else {}),
            **{k: card.get(k) for k in ['caption','visible_text','intents','suitable','avoid','mime','animated']}})
        if item.get('knowledge'):
            info={'uri':item['knowledge']['scope_uri'],'revision':item['knowledge']['revision']}
            require(not registry.get('retrieval') or registry['retrieval']==info,'表情知识版本混用。',409)
            registry['retrieval']=info
    require(not registry.get('retrieval') or all(a.get('knowledge') for a in registry['assets']), '表情知识卡缺失。',409)
    registry['allowed_hosts'] = sorted(set(registry['allowed_hosts']))
    require(config['skills']['reply-style'].get('references/emoji-selection.md') == config['asset_rule'], '表情规则未冻结进回复 Skill。', 409)
    return registry


def selected_parts(snapshot, registry):
    by_url = {a['url']: a for a in registry['assets']}
    result = []
    for part in snapshot.get('parts', []):
        if part.get('message_type') != 5: continue
        card = by_url.get(part['body'])
        require(card is not None, '原生发送选择了未冻结的表情。', 409)
        result.append({'part_id': part['id'], 'status': part['status'], 'asset_id': card['asset_id'],
                       'asset_version': card['lab_asset_version'], 'package_version': registry['version'],
                       'sha256': card['sha256'], 'caption': card['caption'], 'simulation': True})
    return result
