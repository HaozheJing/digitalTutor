"""Cooperative native-loop checkpoints; never interrupt an in-flight tool write."""
import time
from .daily_memory import STALE_CODES, snapshot_current
from .domain import BusinessError


class RunBoundary:
    def __init__(self,app,token,agent,budget,clock=time.monotonic):
        self.app,self.token,self.agent,self.clock=app,token,agent,clock
        self.started=clock();self.limit=max(1,float(budget)*0.65);self.reason=None

    def stop(self,reason):
        if self.reason:return
        self.reason=reason
        self.agent.interrupt(hard_cancel=True,tool_reason=reason+'; resume from durable records, never replay sends')

    def check(self,*unused):
        if self.reason:return
        family,binding=self.app.auth(self.token,{'service','memory_daily','planner_daily','planner_progress'})
        if binding['role']=='memory_daily':
            try:snapshot_current(self.app,family,self.app.store.get('memory_batch',binding['memory_batch_id'],family))
            except BusinessError as exc:
                if exc.code not in STALE_CODES:raise
                self.stop(exc.code)
            return
        if binding['role']!='service' or not binding.get('job_id'):return
        # A committed outcome is already a complete business checkpoint.
        if any(r['id']==binding.get('run_id') for r in self.app.store.list('run_outcome',family)):return
        if self.clock()-self.started>=self.limit:self.stop('CONTINUATION_REQUIRED')

    def tool_finished(self,row):
        if row.get('error') in STALE_CODES:self.stop(row['error'])
        else:self.check()
