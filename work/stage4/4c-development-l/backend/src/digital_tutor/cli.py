"""Local server administrator CLI and family-bound JSON-lines tool transport."""
import argparse
import json
from pathlib import Path
import sys
from pydantic import ValidationError
from .application import Application, Clock
from .config import Config
from .domain import BusinessError
from .demo import run_demo
from .learning import import_workbook
from .tools import ToolRegistry


def output(value):
    print(json.dumps(value,ensure_ascii=False,allow_nan=False),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    parser.add_argument('--db')
    parser.add_argument('--now',help='Host-only test clock in timezone-qualified ISO format')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    p=sub.add_parser('demo'); p.add_argument('--output')
    p=sub.add_parser('import-workbook'); p.add_argument('path')
    p=sub.add_parser('bind-family'); p.add_argument('--family',required=True); p.add_argument('--uid',required=True); p.add_argument('--recipient',required=True)
    for name in ('tools','call','serve'):
        p=sub.add_parser(name)
        p.add_argument('--family',required=True)
        p.add_argument('--role',choices=['service','planner_daily','planner_progress','channel','admin'],default='service')
        p.add_argument('--report-id'); p.add_argument('--revision',type=int)
        if name=='call':
            p.add_argument('tool'); p.add_argument('--arguments',default='{}',help='JSON object; - reads stdin')
    args=parser.parse_args()
    app=None; token=None
    try:
        config=Config.load(args.config)
        app=Application(args.db or config.database,Clock(args.now),config.service_start,config.service_end)
        if args.command=='init':
            output({'schema_version':app.store.connection.execute('PRAGMA user_version').fetchone()[0], 'channel':config.channel})
        elif args.command=='demo':
            result=run_demo(app)
            if args.output:
                path=Path(args.output); path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            output(result)
        elif args.command=='import-workbook':
            output(import_workbook(app,args.path))
        elif args.command=='bind-family':
            app.create_family(args.family,args.uid,args.recipient)
            output({'family':args.family,'bound':True,'mock':True})
        else:
            token=app.open_session(args.family,args.role,args.report_id,args.revision)
            registry=ToolRegistry(app,token)
            if args.command=='tools':
                output(registry.describe())
            elif args.command=='call':
                arguments=json.loads(sys.stdin.read() if args.arguments=='-' else args.arguments)
                result=registry.call(args.tool,arguments); output(result)
                return 0 if result['ok'] else 1
            else:
                # Family/role supplied once by trusted host, never accepted from request JSON.
                for line in sys.stdin:
                    try:
                        request=json.loads(line)
                        if not isinstance(request,dict) or set(request)-{'method','name','arguments'}:
                            raise ValueError('Invalid request envelope')
                        if request.get('method')=='tools/list':
                            output({'ok':True,'tools':registry.describe()})
                        elif request.get('method')=='tools/call' and isinstance(request.get('name'),str):
                            output(registry.call(request['name'],request.get('arguments',{})))
                        else:
                            raise ValueError('Use tools/list or tools/call')
                    except (ValueError,TypeError):
                        output({'ok':False,'error':{'code':'INVALID_JSON_REQUEST'}})
        return 0
    except (BusinessError,ValidationError,ValueError) as exc:
        output({'ok':False,'error':{'code':getattr(exc,'code','INVALID_CONFIG_OR_JSON'),'message':str(exc)}})
        return 1
    finally:
        if app:
            if token:
                app.revoke_session(token)
            app.close()


if __name__=='__main__':
    sys.exit(main())
