import html,json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
r=Path('outputs/dsh-live-20261020');d=json.loads((r/'live-evidence.json').read_text(encoding='utf-8'));t=d['tables']
def esc(v):return html.escape(str(v))
def stamp(v):return datetime.fromisoformat(v.replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M:%S') if v else '—'
def detail(title,v):return '<details><summary>'+esc(title)+'</summary><pre>'+esc(json.dumps(v,ensure_ascii=False,indent=2))+'</pre></details>'
def table(headers,rows):return '<table><thead><tr>'+''.join('<th>'+esc(x)+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table>'
body='<p class="eyebrow">DIGITAL TUTOR / LIVE LAB</p><h1>主实验已持续接入真实对话</h1><p class="lead">只读增量 → 当前交流 → 记忆审查 → 每日规划 → 原生排期。全部发送保持 SQL 模拟。</p>'
body+='<div class="cards">'+''.join('<article><b>'+esc(value)+'</b><span>'+esc(label)+'</span></article>' for value,label in [(str(d['real_inbound_count']),'真实平台测试入站消息'),('5 秒','持续检查新消息'),(str(len(t['memories'])),'持久化记忆'),('07:30 / 18:00','原生周期排期')])+'</div>'
body+='<p class="note">主服务 digitaltutor-dsh-lab 正在运行，路径 /home/ubuntu/digitalTutor-dsh-lab。核验时间：'+stamp(d['verified_at'])+'（上海）。原 Hermes 三个服务保持原 PID，未重启。</p>'
body+='<h2>1 · 消息增量与处理状态</h2>'+table(['事件','来源','状态','尝试次数','完成时间'],[(x['kind'],'SQL mock 新消息' if 'sql-mock' in x['id'] else '真实历史消息首次导入',x['status'],x['attempts'],stamp(x['completed_at'])) for x in t['events']])
body+='<p>历史导入不补发回复。新消息测试样本写在实验SQL，未写入原Hermes库；真实来源仍持续轮询，目前没有新增真人消息。</p>'+detail('查看游标与健康状态',d['health'])
body+='<h2>2 · 记忆自动进入每日计划</h2>'+table(['内容','边界','原话','记忆时间'],[(x['value'],'仅说话者自报称呼，不推断孩子或家长',x['quote'],stamp(x['recorded_at'])) for x in t['memories']])
plan=json.loads(t['daily_plans'][0]['body']);body+='<p>今天非周期候选为 0，保存有效空计划。计划直接记录当前记忆ID和档案来源，不从问候中编造事项。</p>'+detail('每日规划实际SQL记录',plan)+detail('逐事件记忆审查回执',t['memory_reviews'])
body+='<h2>3 · 原生排期与模拟发送</h2>'+table(['任务','周期','下一次执行（上海）','原生ID'],[(x['title'],x['kind'],stamp(x['scheduledAt']),x['id']) for x in d['schedules']])
body+=table(['动作','模拟内容','落库时间'],[(x['action_key'],x['body'],stamp(x['created_at'])) for x in t['deliveries']])
body+='<p>验收通过一条真实原生单次排期触发S01；被动回复先落库，主动提醒等待交流和安静窗口后执行。正式每日18:00规则继续保留，今日S01按唯一键去重。</p>'
body+=detail('家长优先的门禁和原生排期延后时间线',d['priority_observations'])
body+='<h2>4 · 完整上下文与档案接口</h2>'
latest=d['native_contexts'][-1]['context'];body+=table(['上下文部分','内容'],[(key, str(value)+' 字符') for key,value in latest['section_chars'].items()])
body+=detail('模型实际收到的原生 runtime-context 全文',latest)
body+=detail('儿童昵称 / 爸爸妈妈身份接口结果',d['health']['profile'])
body+='<p>真实档案API尚未配置。child_nickname目前是SQL mock“小禾”；guardian_role为unknown，接口支持father/mother。真实API字段接入后优先采用；“豆豆”不会覆盖儿童昵称。</p>'
body+='<h2>验证与边界</h2><p>通过增量游标、迟到回复、来源context切换、联系人隔离、原话记忆、重启去重、被动优先、回复与计划完成校验等测试。未修改官方Harness包；原有API适配器保留；OpenViking只读。</p>'
body+=detail('自动化测试实际输出',d['test_result'])+detail('主服务重启与排期去重证据',d['restart'])+detail('原服务PID与启动时间',d['services'])
body+='<p class="note">当前仅接入一个明确绑定的测试联系人，入站来源为Replyer记录而非原始回调。记忆目前只支持有原话证据的自报称呼。今日课程绑定与完课状态来自SQL mock；课文来自OpenViking，原资料标记AI生成、未经业务审核。没有实发微信，也未验证明天07:30的自然到点运行。</p><p><a href="live-evidence.json">全部SQL与运行证据</a> · <a href="native-events.json">原生工具调用与排期事件</a></p>'
style='''body{font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif;background:#f4f6f8;color:#182b3b;margin:0}main{max-width:1120px;margin:auto;padding:42px 26px}h1{font-size:38px;line-height:1.3}h2{margin-top:40px}.eyebrow{color:#14756a;letter-spacing:2px;font-size:13px}.lead{font-size:19px;color:#476071}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:28px 0}article{background:white;padding:22px;border-radius:14px;border:1px solid #dae4e9}article b{display:block;font-size:30px;color:#09675f}article span{display:block;color:#546b78}.note{border-left:4px solid #cfaa4b;padding:12px 18px;background:#fff9e9}table{border-collapse:collapse;width:100%;background:white;font-size:14px;table-layout:fixed}th,td{text-align:left;border:1px solid #dfe6eb;padding:12px;overflow-wrap:anywhere;vertical-align:top}th{background:#e7eff3}details{margin:16px 0;background:white;padding:14px;border:1px solid #dfe6eb;border-radius:8px}summary{cursor:pointer;color:#126e68;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.65 ui-monospace,monospace;max-height:620px;overflow:auto}a{color:#09675f}@media(max-width:700px){.cards{grid-template-columns:1fr 1fr}h1{font-size:28px}main{padding:22px 14px}}'''
(r/'report.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>主实验增量接入实测</title><style>'+style+'</style><main>'+body+'</main></html>',encoding='utf-8')
print(str((r/'report.html').resolve()))
