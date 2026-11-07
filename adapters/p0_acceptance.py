"""Authenticated operator console for isolated, persistent acceptance scenarios."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from reply_lab import Fault, require

CASES={
 'human':{'title':'人工接管','preset':'remind-reading','outcome':'sent','steps':['准备发送前断点','人工接管','探测旧发送门禁','填写人工回复并发送','观察所有分段sent、人工事项解决']},
 'cancel':{'title':'取消提醒','preset':'remind-reading','outcome':'sent','message':'今天这条阅读提醒不用发了，其他安排不变。','steps':['发送取消消息','等真实模型处理','检查workflow_commit与任务cancelled','选择原任务尝试执行，确认拒绝']},
 'reschedule':{'title':'修改时间（窗口内图文）','outcome':'sent','message':'今晚这条课堂表达高光图文请改到20点30分再发，其他安排不变。','steps':['发送改时消息','等真实模型处理','核对新due_at为20:30、revision和实际工具回执','原20:15尝试执行应拒绝；推进到20:30后可试执行同一事项']},
 'unknown':{'title':'未知回执','preset':'remind-reading','outcome':'unknown','steps':['准备发送前断点','人工接管','发送一段人工回复','确认unknown且任务未完成','再次核对发送，确认分段ID不增加','选已尝试的分段补录sent','观察原事项对账；两段场景可能仍partial']},
 'memory':{'title':'跨会话与夜间记忆','outcome':'sent','message':'老师，我是孩子爸爸，请叫我林先生。孩子的小名是豆豆。以后不用叫我妈妈。','steps':['发送称呼原话，等回复完成','新建会话（保留业务库和原文）','问“老师，你应该怎么称呼我？孩子小名是什么？”','推进至22:00并触发夜间整理','等待独立记忆session完成','再次新建会话并复问，检查原文/摘要和来源']}
}


class Console:
    def __init__(self,studio):
        self.studio=studio;self.root=Path(studio.root).resolve();self.lock=threading.Lock();self.lease=None
        marker=self.root/'config/p0-acceptance.json'
        require(marker.is_file() and json.loads(marker.read_text()).get('scope')=='integration_only','此工作区未启用P0隔离验收。',403)
        require(self.root.name=='digitalTutor-dsh-integration-20261026','P0仅可在指定集成工作区启用。',403)
        self.home=self.root/'runtime/p0-acceptance';self.home.mkdir(exist_ok=True)
        self.pointer=self.home/'current.json'
        self.current=json.loads(self.pointer.read_text()) if self.pointer.exists() else None

    def save(self):
        temp=self.pointer.with_suffix('.tmp');temp.write_text(json.dumps(self.current,ensure_ascii=False));temp.replace(self.pointer)

    def authorize(self,actor):
        lab=self.studio._reply_lab
        require(lab is not None,'回复工作台尚未初始化。',409)
        version=lab.default_config(actor)
        require(version is not None,'当前账户无权访问集成默认组合。',403)
        if self.current:require(self.current['owner']==actor['id'],'此验收场景属于另一操作员。',403)
        return lab.version(actor,version,'config')

    def bound(self):
        require(self.current is not None,'请先开启场景。',409)
        run=Path(self.current['run_root']).resolve()
        require(run.parent==self.root/'runtime/experiments' and re.fullmatch(r'exp-[a-f0-9]{32}',run.name),'验收路径不属于集成隔离根。',403)
        require(not run.is_symlink() and (run/'workspace').resolve()==run/'workspace','隔离目录不可重定向。',403)
        return run

    def ops(self,action,data=None):
        run=self.bound();w=run/'workspace'
        proc=subprocess.run([sys.executable,'-B',str(w/'adapters/p0_actions.py'),action,json.dumps(data or {},ensure_ascii=False)],cwd=w,capture_output=True,text=True,timeout=90)
        if proc.returncode:
            (run/'p0-action-error.log').write_text(proc.stderr)
            raise Fault('验收动作执行异常，诊断已保留；不要重复发送。',409)
        value=json.loads(proc.stdout)
        require(value['ok'],value.get('error','验收操作被拒绝'),409)
        return value['result']

    def native(self,path,data=None):
        run=self.bound();w=run/'workspace';m=json.loads((w/'config/experiment.json').read_text())
        worker=json.loads((self.root/'runtime/quality-source-snapshot/native-worker.json').read_text())
        token=json.loads((w/'config/workbench.json').read_text())['token']
        body=None if data is None else json.dumps({**data,'experiment_run_id':m['experiment_run_id'],'run_id':m['business_run_id'],'family_uid':m['family_uid']}).encode()
        req=urllib.request.Request('http://127.0.0.1:'+str(worker['workbench_port'])+path,body,{'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=45) as f:return json.load(f)
        except urllib.error.HTTPError as e:
            detail=json.load(e);raise Fault(str(detail.get('detail') or detail.get('error')),409) from None

    def claim_ports(self):
        import fcntl
        if self.lease:return
        lease=(self.root/'runtime/emoji-real-window.lock').open('a+')
        try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            lease.close();raise Fault('原生端口正用于另一项试聊；请等它完成。',409) from None
        self.lease=lease

    def launch(self):
        run=self.bound();self.claim_ports()
        worker=self.root/'runtime/quality-source-snapshot/native-worker.json'
        with (run/'p0-supervisor.log').open('ab') as log:
            p=subprocess.Popen([sys.executable,'-B',str(self.root/'adapters/native_experiments.py'),'serve',str(run),str(worker)],cwd=self.root,stdout=log,stderr=log,start_new_session=True)
        self.current['supervisor_pid']=p.pid;self.save()
        for _ in range(60):
            if p.poll() is not None:raise Fault('隔离原生服务未能启动，诊断已保留。',409)
            try:
                status=self.native('/health')
                if status.get('ok') and status.get('experiment_run_id')==run.name:
                    self.current['running']=True;self.save();return status
            except (OSError,Fault):pass
            time.sleep(.5)
        raise Fault('隔离服务启动尚未就绪；请刷新状态，不要重建场景。',409)

    def wait_worker_release(self,run):
        import fcntl
        path=run.parent/'worker.lock'
        if not path.exists():return
        with path.open('a+') as lock:
            for _ in range(80):
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);return
                except BlockingIOError:time.sleep(.25)
        raise Fault('旧原生服务仍在释放运行锁，请稍后结束场景；不会强抢运行锁。',409)

    def stop(self):
        if self.current:
            from native_experiments import stop
            run=self.bound();record=json.loads((run/'record.json').read_text())
            if record.get('status')=='running':
                stop(run)
                for _ in range(60):
                    record=json.loads((run/'record.json').read_text())
                    if record.get('status')=='stopped':break
                    time.sleep(.25)
                require(record.get('status')=='stopped','原生进程仍在退出，暂不能切换场景。',409)
            self.wait_worker_release(run)
            self.current['running']=False;self.save()
        if self.lease:self.lease.close();self.lease=None
        return {'status':'stopped_evidence_retained'}

    def create(self,actor,case):
        require(case in CASES,'未知验收场景。')
        require(not self.current or not self.current.get('running'),'请先结束当前场景，保留证据后再开启下一个。',409)
        if self.current:require(json.loads((self.bound()/'record.json').read_text()).get('status')!='running','旧场景的进程尚未退出，请先结束它。',409)
        cfg=self.authorize(actor);p=cfg['payload'];kind=CASES[case]
        from native_experiments import prepare
        from reply_lab_emojis import package
        self.claim_ports()
        folder=self.home/uuid.uuid4().hex;folder.mkdir()
        frozen={'config_version':cfg['id'],'persona':p['persona'],'model':p['model'],'parameters':p['parameters'],'skills':p['skills'],
                'emoji_assets':package(self.root,p,folder/'assets'),'transport_outcome':kind['outcome'],'preference_memory':p.get('preference_memory',{})}
        if p.get('reply_pipeline'):frozen['reply_pipeline']=p['reply_pipeline']
        if case in ('memory','reschedule'):
            fixture=json.loads((self.root/'runtime/fixtures/mock_uid_00001_first_month.json').read_text())
            if case=='memory':
                require(frozen['preference_memory'].get('enabled') and frozen['preference_memory'].get('slow_enabled'),'默认组合未启用独立记忆机制。',409)
                scenario={'as_of':'2025-10-26T19:00:00+08:00','fixture':fixture,'initial_tasks':[],'mode':'passive'}
            else:
                scenario={'as_of':'2025-11-07T20:15:00+08:00','fixture':fixture,'initial_tasks':[{'skill':'highlight-expressive-answer','run_at':'2025-11-07T20:15:00+08:00'}],'mode':'execution'}
        else:
            presets=json.loads((self.root/'runtime/quality-source-snapshot/active-presets.json').read_text())
            row=next(x['payload'] for x in presets if x['id']==kind['preset'])
            scenario={'as_of':row['as_of'],'fixture':row['native_fixture'],'initial_tasks':row['initial_tasks'],'mode':'execution'}
        scenario.update(scenario_version='p0-'+case+('-v2' if case=='reschedule' else '-v1'),family_uid=scenario['fixture']['uid'],history=[])
        try:prepared=prepare(frozen,scenario,source=self.root,skill_source=self.root)
        except Exception:
            self.lease.close();self.lease=None;raise
        self.current={'owner':actor['id'],'case':case,'running':False,'run_root':prepared['run_root'],'config_version':cfg['id'],'config_digest':cfg['digest'],'code_commit':prepared['code_commit'],'experiment_run_id':prepared['experiment_run_id'],'business_run_id':prepared['business_run_id'],'created_at':time.time()}
        self.save();self.launch()
        return {'created':self.current['experiment_run_id'],'simulation':True}

    def state(self):
        value={'cases':CASES,'current':self.current,'simulation':True,'production_enabled':False}
        if self.current:
            try:value['snapshot']=self.ops('state')
            except Fault as e:value['snapshot_error']=str(e)
        return value

    def dispatch(self,actor,action,data):
        self.authorize(actor)
        if action=='state':return self.state()
        allowed={'create','stop','message','stage','takeover','probe','human_reply','flush','late_receipt','new_session','night','advance','execute'}
        require(action in allowed,'未开放的验收操作。',404)
        req=data.get('request_id','')
        require(isinstance(req,str) and re.fullmatch(r'[A-Za-z0-9_-]{8,100}',req),'缺少操作请求编号。')
        require(self.lock.acquire(blocking=False),'已有验收操作执行中，请稍后刷新。',409)
        try:
            audit=self.home/(req+'.json');identity={'actor':actor['id'],'action':action,'input':data}
            if audit.exists():
                old=json.loads(audit.read_text());require(old['request']==identity,'同一请求编号不可改写。',409)
                require(old.get('done'), '上次操作结果尚未确认，请查看现场；不会自动重发。',409)
                if 'error' in old:raise Fault(old['error'],409)
                return old['result']
            audit.write_text(json.dumps({'request':identity,'done':False},ensure_ascii=False))
            try:
                if action=='create':result=self.create(actor,data['case'])
                else:
                    require(self.current and data.get('experiment_run_id')==self.current['experiment_run_id'],'场景已变化，请刷新。',409)
                    require(self.current.get('running') or action=='stop','当前场景已结束，请开启新场景。',409)
                    if action=='stop':result=self.stop()
                    elif action=='message':result=self.native('/experiment/v1/messages',{'request_id':req,'body':data['body']})
                    elif action=='new_session':
                        require(self.current['case']=='memory','新会话操作限记忆场景。',409)
                        self.ops('idle');path=self.bound()/'workspace/runtime/service-session.json'
                        old=json.loads(path.read_text())['sessionId'];self.stop()
                        path.rename(path.with_name('p0-prior-session-'+uuid.uuid4().hex+'.json'))
                        result=self.launch();result={'old_session':old,'new_session':result['sessionId'],'business_run_id':self.current['business_run_id'],'history_replayed':False,'database_retained':True}
                        require(old!=result['new_session'],'新会话未建立。',409)
                    else:
                        if action in {'stage','takeover','probe','human_reply','flush','late_receipt'}:require(self.current['case'] in {'human','unknown'},'此操作不属于当前场景。',409)
                        if action=='night':require(self.current['case']=='memory','夜间整理限记忆场景。',409)
                        result=self.ops(action,data)
                audit.write_text(json.dumps({'request':identity,'done':True,'result':result},ensure_ascii=False))
                return result
            except Exception as e:
                audit.write_text(json.dumps({'request':identity,'done':True,'error':str(e)},ensure_ascii=False));raise
        finally:self.lock.release()


def dispatch(studio,actor,path,data):
    with studio.lock:
        if not hasattr(studio,'_p0_console'):studio._p0_console=Console(studio)
    return studio._p0_console.dispatch(actor,path,data)

