"""Read exported closed evidence and prepare human-review text without rewriting replies."""
from pathlib import Path
import argparse,json

parser=argparse.ArgumentParser();parser.add_argument('--export',action='append',required=True)
parser.add_argument('--output',required=True);args=parser.parse_args()
lines=['# 日内承接与兼容复验：原文对话','',
       '仅虚构家庭、mock渠道。下列回复逐字取自独立渠道账本；程序通过不等于语义通过。未知/失败状态单独标注，不当作实际送达。后台标识如出现在正文中，按原样保留供评审。','']
for export in args.export:
    root=Path(export)
    for case in sorted(p for p in root.iterdir() if p.is_dir()):
        result=next((case/p for p in ('summary.json','result.json') if (case/p).exists()),None)
        if result is None:continue
        if (case/'final.json').exists():
            final=json.loads((case/'final.json').read_text(encoding='utf8'))
            records={'family':final['records']};channel=final['independent_channel']
        elif (case/'records.json').exists() and (case/'channel-ledger.json').exists():
            records=json.loads((case/'records.json').read_text(encoding='utf8'))
            channel=json.loads((case/'channel-ledger.json').read_text(encoding='utf8'))
        else:continue
        if isinstance(records.get('message'),list):records={'family':records}
        messages={m['id']:m for rec in records.values() for m in rec.get('message',[]) if m['direction']=='inbound'}
        seen=set();lines += ['## '+root.name+' / '+case.name,'',f'证据：`{case.as_posix()}`','']
        for index,row in enumerate(channel,1):
            payload=row['payload']
            for ref in payload.get('reply_to',[]):
                mid=ref['message_id']
                if mid in seen or mid not in messages:continue
                seen.add(mid);message=messages[mid]
                lines += ['**家长输入**','',message['body'],'']
            lines += [f"**多多老师 · 渠道 {row['stage']} · {payload.get('admitted_at',payload.get('occurred_at',''))}**",'',payload['body'],'']
        for mid,message in messages.items():
            if mid not in seen:
                lines += ['**其他来源输入（不能据此认定已答复）**','',message['body'],'']
        for rec in records.values():
            for outcome in rec.get('run_outcome',[]):
                if outcome.get('draft'):
                    lines += ['**内部草稿 · 未向家长发送**','',outcome['draft'],'']
        if not channel:lines += ['本案没有独立渠道外发记录。','']
output=Path(args.output)
assert not output.exists(), 'Use a fresh dialogue artifact; preserve earlier reviews.'
output.write_text('\n'.join(lines)+'\n',encoding='utf8')
print(json.dumps({'dialogue_file':str(output),'characters':sum(map(len,lines))},ensure_ascii=False))
