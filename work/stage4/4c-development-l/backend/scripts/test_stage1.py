"""Run on the server and retain a minimal, non-learner test report."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sqlite3
import unittest

parser=argparse.ArgumentParser(); parser.add_argument('--output',required=True); args=parser.parse_args()
suite=unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parents[1]/'tests'))
result=unittest.TextTestRunner(verbosity=2).run(suite)
report={'executed_at':datetime.now(timezone.utc).isoformat(),'platform':platform.platform(),
        'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'tests_run':result.testsRun,
        'failures':[str(t) for t,_ in result.failures],'errors':[str(t) for t,_ in result.errors],
        'skipped':[{'test':str(t),'reason':reason} for t,reason in result.skipped],
        'successful':result.wasSuccessful(),'real_model_used':False,'real_wechat_used':False}
path=Path(args.output); path.parent.mkdir(parents=True,exist_ok=True)
path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
raise SystemExit(0 if result.wasSuccessful() else 1)
