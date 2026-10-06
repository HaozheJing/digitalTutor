"""Manual synthetic daily planning; finite native Worker runs, never a daemon."""
import argparse
import json
from pathlib import Path
import sys

from manual_daily_runtime import ManualDaily, DemoError
from manual_demo_runtime import InputEncodingError, validate_message
from demo_passive import terminal_input
from accept_stage4_passive import safe


def display(summary):
    print('状态：'+summary['status']+'；已返回的原生运行：'+str(summary['completed_native_runs'])+
          '；已记录 API 调用：'+str(summary['known_api_calls']))
    print('业务时间：'+summary['business_time']+'；mode=daily；渠道=mock')
    for event in summary['selected_events']:print('[当日成员] '+event['id']+' '+event['goal_text'])
    for event in summary['unselected_open_events']:print('[未入选责任仍保留] '+event['id']+' '+event['goal_text'])
    for item in summary['pending_inputs']:print('[待规划] '+item['id']+' '+item.get('request',''))
    for item in summary['unconsumed_reports']:print('[未消费报告，daily 门禁] '+item['id'])
    for item in summary['candidate_gaps']:print('[来源缺口] '+item['id']+' '+','.join(item.get('gaps',[])))
    for message_id,intents in summary['unanswered'].items():print('[未答] '+message_id+' '+','.join(intents))
    for item in summary['waiting']:print('[等待] '+str(item.get('reason') or item['kind']))
    for item in summary['channel_replies']:print('[累计模拟渠道 '+item['stage']+'] '+item['body'])
    for item in summary['failures']:print('[失败] '+json.dumps(safe(item),ensure_ascii=False))
    print('证据：'+summary['evidence'])
    print('仅人工演示；生成计划不是交付完成，也不代表独立验收通过。')


def main(argv=None,*,project=None,demo_factory=ManualDaily,input_fn=None,is_interactive=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True,help='New manual daily identifier, never an existing state path')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--sources',type=Path,help='UTF-8 synthetic raw source/confirmed rule JSON; new runs only')
    parser.add_argument('--clock',help='ISO business clock with timezone; system time is never changed')
    parser.add_argument('--message',action='append',help='Fictional inbound message; repeat for successive turns')
    parser.add_argument('--tick',action='store_true',help='Explicitly run bounded Scheduler/Worker now; default only stages input and shows state')
    parser.add_argument('--once',action='store_true',help='Exit after the requested turn even on an interactive terminal')
    parser.add_argument('--max-steps',type=int,default=8,choices=range(1,21))
    parser.add_argument('--input-encoding',choices=('utf-8','gb18030'),default='utf-8')
    args=parser.parse_args(argv)
    if args.sources and args.resume:parser.error('--sources is only valid for a new run')
    demo=None;codes=[]
    project=Path(project).resolve() if project else Path(__file__).resolve().parents[2]
    if input_fn is None:input_fn=lambda prompt:terminal_input(prompt,args.input_encoding)
    interactive=sys.stdin.isatty() if is_interactive is None else is_interactive
    try:
        sources=json.loads(args.sources.read_text(encoding='utf-8')) if args.sources else None
        if args.message:
            for text in args.message:validate_message(text)
        demo=demo_factory(project,args.run,resume=args.resume,clock=args.clock,sources=sources)
        for text in args.message or [None]:
            result=demo.turn(text,tick=args.tick,max_steps=args.max_steps);display(result);codes.append(result['exit_code'])
        if interactive and not args.once and not args.message:
            print('仅虚构信息。/tick 有限执行，/clock ISO 设置业务时间，/quit 退出；自然消息入站后执行本轮。')
            while True:
                try:text=input_fn('daily> ').strip()
                except EOFError:break
                except InputEncodingError as exc:
                    print('[INPUT_ENCODING_INVALID] '+str(exc),file=sys.stderr);codes.append(1);continue
                if not text:continue
                if text=='/quit':break
                if text=='/tick':result=demo.turn(tick=True,max_steps=args.max_steps)
                elif text.startswith('/clock '):result=demo.turn(clock=text[7:].strip())
                elif text.startswith('/'):
                    print('可用命令：/tick、/clock ISO、/quit');continue
                else:result=demo.turn(text,tick=True,max_steps=args.max_steps)
                display(result);codes.append(result['exit_code'])
        return 1 if 1 in codes else codes[-1] if codes else 0
    except Exception as exc:
        print(json.dumps({'status':'failed','type':type(exc).__name__,'code':getattr(exc,'code',None),
            'message':safe(str(exc)) if isinstance(exc,DemoError) else 'See diagnostic type/code; raw provider details are not printed'},ensure_ascii=False),file=sys.stderr)
        return 1
    finally:
        if demo is not None:demo.close()


if __name__=='__main__':raise SystemExit(main())
