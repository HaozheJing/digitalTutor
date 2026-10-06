"""Manual mock-channel test: fictional input, strict passive Worker, approved materials."""
import argparse,json,sys
from pathlib import Path
from manual_demo_runtime import ManualDemo,DemoError,InputEncodingError,validate_message


def display(summary):
    labels={'idle':'空闲','replied':'已有实际模拟回复','waiting':'等待/仍有未答','failed':'执行失败'}
    print('状态：'+labels[summary['status']]+'；模型调用：'+str(summary['model_runs']))
    for reply in summary['channel_replies']:print('[模拟渠道 '+reply['stage']+'] '+reply['body'])
    for item in summary['unanswered']:print('[未答] '+item['body'])
    for item in summary['waiting']:print('[等待] '+str(item.get('reason') or '尚未执行'))
    for item in summary['failures']:print('[失败] '+str(item.get('reason') or item.get('error')))
    print('证据：'+summary['evidence'])
    print('本次为人工演示，不代表验收通过。')


def terminal_input(prompt,encoding='utf-8'):
    # Decode the terminal wire bytes explicitly, independent of Python locale /
    # surrogateescape. Never guess a legacy encoding or replace damaged text.
    print(prompt,end='',flush=True)
    raw=sys.stdin.buffer.readline()
    if not raw:raise EOFError
    try:return raw.decode(encoding,errors='strict').rstrip('\r\n')
    except UnicodeDecodeError as exc:
        raise InputEncodingError('Input bytes do not match --input-encoding. Use a UTF-8 terminal or restart with --input-encoding gb18030 for GBK. No message was accepted.') from exc


def main(argv=None,*,project=None,demo_factory=ManualDemo,input_fn=None,is_interactive=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True,help='Manual demo identifier, never a state path')
    parser.add_argument('--resume',action='store_true',help='Explicitly resume this manual demo with unchanged source/materials')
    parser.add_argument('--input-encoding',choices=('utf-8','gb18030'),default='utf-8',help='Interactive terminal input byte encoding; GBK terminals use gb18030. Output remains unchanged.')
    parser.add_argument('--message',action='append',help='Fictional parent input; repeat for successive turns')
    parser.add_argument('--clock',help='Optional simulated ISO business time including timezone; shared with actual sending process')
    args=parser.parse_args(argv)
    if args.message and any(not text.strip() for text in args.message):parser.error('message cannot be empty')
    project=Path(project) if project else Path(__file__).resolve().parents[2]
    interactive=sys.stdin.isatty() if is_interactive is None else is_interactive
    if input_fn is None:input_fn=lambda prompt:terminal_input(prompt,args.input_encoding)
    demo=None;codes=[]
    try:
        demo=demo_factory(project,args.run,resume=args.resume,clock=args.clock)
        if args.message:
            for message in args.message:
                summary=demo.turn(message);display(summary);codes.append(summary['exit_code'])
        else:
            summary=demo.turn();display(summary);codes.append(summary['exit_code'])
            if interactive:
                print('仅输入虚构家庭信息；输入 /quit 结束，不启动常驻服务。')
                while True:
                    try:
                        message=input_fn('家长> ')
                        if message.strip():validate_message(message)
                    except EOFError:break
                    except InputEncodingError as exc:
                        print('[INPUT_ENCODING_INVALID] '+str(exc),file=sys.stderr)
                        codes.append(1)
                        continue
                    if message.strip()=='/quit':break
                    if not message.strip():continue
                    summary=demo.turn(message);display(summary);codes.append(summary['exit_code'])
        # A later successful turn never conceals a technical failure earlier in this invocation.
        return 1 if 1 in codes else codes[-1] if codes else 0
    except Exception as exc:
        from accept_stage4_passive import safe
        print(json.dumps({'status':'failed','error_type':type(exc).__name__,'error_code':getattr(exc,'code',None),
            'message':safe(str(exc)) if isinstance(exc,DemoError) else 'See diagnostic type/code; raw provider errors are not printed'},ensure_ascii=False),file=sys.stderr)
        return 1
    finally:
        if demo is not None:demo.close()


if __name__=='__main__':raise SystemExit(main())
