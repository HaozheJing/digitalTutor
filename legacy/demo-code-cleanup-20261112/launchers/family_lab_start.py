"""One supervisor, five native family actors, bounded shared gateways."""
import fcntl,json,os,signal,subprocess,sys,threading,time
from pathlib import Path
from family_lab import Lab
from family_lab_server import Workbench,ThreadingHTTPServer
from native_experiments import profile,write_json,bind_knowledge
from experiment_parameter_proxy import ParameterProxy


def launch(workspace,ports,operator,model):
    workspace=Path(workspace);audit=workspace/'runtime/model-audit';audit.mkdir(exist_ok=True)
    proxy=ParameterProxy(operator['base_url'],operator['api_key'],model['model'],model.get('parameters',{}),audit/'service.jsonl',image_workspace=workspace)
    memory=ParameterProxy(operator['base_url'],operator['api_key'],'deepseek-v4-pro',{},audit/'memory.jsonl',image_workspace=workspace)
    assembled=profile(workspace,model['persona'],model['model'],proxy.url,model.get('max_output_tokens'),model.get('reasoning_effort'))
    providers=next(x for x in assembled if x.get('id')=='llm-pi-ai')['config']['providers']
    providers['experiment-memory-provider']={'api':'openai-completions','baseURL':memory.url,'apiKeyEnv':'TUTOR_MEMORY_API_KEY','models':[{'id':'deepseek-v4-pro','contextWindow':131072,'maxTokens':32768,'reasoningEfforts':{'low':'low','high':'high','max':'max'}}],'compat':{'supportsDeveloperRole':False,'thinkingFormat':'deepseek'}}
    home=workspace/'runtime/dsh-home';pr=home/'profiles/web';pr.mkdir(parents=True,exist_ok=True)
    write_json(pr/'package.json',{'name':'dsh-profile-web','private':True,'dependencies':{},'dsh':{'profile':{'bundles':['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app']}}})
    (pr/'cordis.yml').write_text('[]');write_json(pr/'cordis.patch.yml',assembled)
    modules=Path(operator['node_modules']).resolve();node=Path(operator['node']).resolve()
    dependencies=workspace/'runtime/dependencies';dependencies.mkdir(exist_ok=True)
    if not (dependencies/'node_modules').exists():(dependencies/'node_modules').symlink_to(modules,target_is_directory=True)
    if not (pr/'node_modules').exists():(pr/'node_modules').symlink_to(modules,target_is_directory=True)
    for folder in ['home','tmp','cache','agents-home']:(workspace/'runtime'/folder).mkdir(exist_ok=True)
    env={'PATH':str(node.parent)+':/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8','HOME':str(workspace/'runtime/home'),'TMPDIR':str(workspace/'runtime/tmp'),'XDG_CACHE_HOME':str(workspace/'runtime/cache'),'DSH_HOME':str(home),'DSH_AGENTS_HOME':str(workspace/'runtime/agents-home'),'DSH_PERMISSION_MODE':'workspace-write','TUTOR_ROOT':str(workspace),'TUTOR_API_KEY':proxy.token,'TUTOR_MEMORY_API_KEY':memory.token,'TUTOR_WORKBENCH_PORT':str(ports[1]),'PYTHONDONTWRITEBYTECODE':'1'}
    knowledge=bind_knowledge(workspace,operator)
    env['TUTOR_FAMILY_LAB']='1'
    if knowledge:env['TUTOR_KNOWLEDGE_CONFIG_FILE']=str(knowledge)
    __import__('emoji_runtime').bind(workspace,operator,model)
    voice=__import__('voice_understanding').bind(workspace,operator)
    if voice:env['TUTOR_VOICE_BINDING_SHA256']=voice['sha256']
    from knowledge_search import CLI
    subprocess.run([CLI,'language','en'],env=env,capture_output=True,check=True,timeout=15)
    proxy.start();memory.start()
    log=(workspace/'runtime/host.log').open('ab')
    child=subprocess.Popen([str(node),str(modules/'@deepseek-ai/dsh/lib/bin.js'),'web','--port',str(ports[0]),'--no-open'],cwd=workspace,env=env,stdout=log,stderr=log,start_new_session=True)
    return child,proxy,memory,log


def main(home):
    home=Path(home).resolve();lock=(home/'supervisor.lock').open('a+')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    lab=Lab(home/'control.sqlite');lab.recover()
    assembly=json.loads((home/'assembly.json').read_text());operator=json.loads((home/'operator.private.json').read_text());model=json.loads((home/'model.json').read_text())
    children=[];stop=threading.Event();workbench=Workbench(home)
    server=ThreadingHTTPServer(('127.0.0.1',assembly['port']),workbench.handler());server.daemon_threads=True
    signal.signal(signal.SIGTERM,lambda *_:stop.set());signal.signal(signal.SIGINT,lambda *_:stop.set())
    try:
        for f in lab.snapshot()['families']:children.append(launch(f['workspace'],assembly['family_ports'][f['family_id']],operator,model))
        write_json(home/'processes.json',{'supervisor':os.getpid(),'children':[x[0].pid for x in children]})
        workbench.gateway.start();threading.Thread(target=server.serve_forever,daemon=True).start()
        failed=set()
        while not stop.wait(1):
            requests=lab.read("SELECT value FROM settings WHERE key='new_round_request'")
            request=json.loads(requests[0]['value']) if requests else None
            if request and request['status']=='queued':
                try:
                    request['status']='preparing'
                    with lab.transaction() as c:c.execute("UPDATE settings SET value=? WHERE key='new_round_request'",(json.dumps(request),))
                    from family_lab_round import prepare_round,activate
                    prepared=prepare_round(home,request)
                    for p,proxy,memory,log in children:
                        if p.poll() is None:os.killpg(p.pid,signal.SIGTERM)
                        try:p.wait(timeout=15)
                        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
                        proxy.close();memory.close();log.close()
                    children=[]
                    request['run_id']=activate(lab,request,prepared)
                    for f in lab.snapshot()['families']:children.append(launch(f['workspace'],assembly['family_ports'][f['family_id']],operator,model))
                    failed=set();request['status']='ready'
                    write_json(home/'processes.json',{'supervisor':os.getpid(),'children':[x[0].pid for x in children]})
                except Exception as exc:
                    request.update(status='failed',error=type(exc).__name__)
                    import traceback
                    (home/'reports'/('round-error-'+request['id']+'.txt')).write_text(traceback.format_exc())
                with lab.transaction() as c:c.execute("UPDATE settings SET value=? WHERE key='new_round_request'",(json.dumps(request),))
            for i,(p,_,_,_) in enumerate(children):
                if p.poll() is not None and i not in failed:
                    f=lab.snapshot()['families'][i]
                    # Fence the failed family. Healthy families remain usable.
                    lab.control(lab.current()['id'],f['family_id'],'takeover',True,'native_host_exited')
                    lab.finish_turn(f['run_id'],f['family_id'])
                    with lab.transaction() as c:lab.audit(c,'host_exited',{'returncode':p.returncode},f['run_id'],f['family_id'])
                    failed.add(i)
    finally:
        workbench.gateway.stopped.set()
        for p,_,_,_ in children:
            if p.poll() is None:os.killpg(p.pid,signal.SIGTERM)
        for p,proxy,memory,log in children:
            try:p.wait(timeout=15)
            except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
            proxy.close();memory.close();log.close()
        server.server_close()


if __name__=='__main__':main(sys.argv[1])
