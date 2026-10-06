"""Run the existing host/Worker loop explicitly; passive mock mode is the default."""
import argparse
import json
from pathlib import Path
import time
from digital_tutor.application import Application,Clock
from digital_tutor.hermes_host import HermesHost
from digital_tutor.runtime_assembly import approval
from digital_tutor.scheduler import Scheduler,Worker
from digital_tutor.outbox import drain

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',required=True)
    p.add_argument('--runs',required=True)
    p.add_argument('--clock-file')
    p.add_argument('--mode',choices=['passive','daily','full'],default='passive')
    p.add_argument('--approved-materials',action='store_true')
    p.add_argument('--scans',type=int,default=1)
    p.add_argument('--interval',type=float,default=1)
    a=p.parse_args()
    if not 1<=a.scans<=100000 or a.interval<0: p.error('invalid scans or interval')
    root=Path(__file__).resolve().parents[2]
    app=Application(a.db,Clock(shared_path=a.clock_file),mode=a.mode,strict_runtime=True)
    host=HermesHost(root,a.runs,240,approved_materials=approval(root) if a.approved_materials else None)
    worker=Worker(app,host)
    try:
        for index in range(a.scans):
            drain(app)
            scheduled=Scheduler(app).tick()
            value=worker.run_once()
            print(json.dumps({'scan':index,'scheduled':scheduled,'worker':value},ensure_ascii=False),flush=True)
            if index+1<a.scans: time.sleep(a.interval)
    finally:app.close()

if __name__=='__main__':main()
