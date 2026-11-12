"""Existing Worker in a separate process for arrival-during-generation replay."""
import argparse
import json
from pathlib import Path
import time

from .application import Application, Clock
from .outbox import drain
from .replay import make_handler, atomic_json, append, load_pack
from .scheduler import Scheduler, Worker


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--project',required=True)
    parser.add_argument('--case',required=True)
    parser.add_argument('--native',action='store_true')
    parser.add_argument('--max-runs',type=int,default=40)
    parser.add_argument('--fixture-delay',type=float,default=0)
    args=parser.parse_args();case=Path(args.case)
    pack,source_sha=load_pack(case/'input.json');pack['_input_sha256']=source_sha
    app=Application(case/'business.sqlite',Clock(shared_path=case/'clock.json'),strict_runtime=True,mode='full')
    worker=Worker(app,make_handler(args.project,case,pack,args.native,args.fixture_delay))
    runs=0
    try:
        while not (case/'worker-stop').exists():
            drain(app);Scheduler(app).tick()
            if runs>=args.max_runs:
                atomic_json(case/'worker-status.json',{'budget_limited':True,'runs':runs,'now':app.clock.iso()});break
            value=worker.run_once()
            runs+=int(value['ran'])
            append(case/'worker-events.jsonl',{'now':app.clock.iso(),'result':value})
            atomic_json(case/'worker-status.json',{'idle':not value['ran'],'runs':runs,'now':app.clock.iso()})
            time.sleep(0.02)
    finally:app.close()


if __name__=='__main__':main()
