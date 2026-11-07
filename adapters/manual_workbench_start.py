"""Launch the manual real-WeChat test workbench with separate model profiles."""
from pathlib import Path
import os,json,subprocess,signal
from native_experiments import profile,write_json,bind_knowledge
from experiment_parameter_proxy import ParameterProxy
import manual_workbench

def main():
    root=Path(__file__).resolve().parents[1];manual_workbench.verify()
    operator=json.loads((root/'runtime/manual-worker.private.json').read_text())
    config=json.loads((root/'config/manual-model.json').read_text())
    scope=json.loads((root/'config/manual-workbench.json').read_text())
    native_port=scope.get('native_port',19031);workbench_port=scope.get('workbench_port',19032)
    if type(native_port) is not int or type(workbench_port) is not int or not 1024<=native_port<=65535 or not 1024<=workbench_port<=65535 or native_port==workbench_port:raise ValueError('invalid_test_ports')
    if config['model'] not in operator['allowed_models'] or 'deepseek-v4-pro' not in operator['allowed_models']:raise ValueError('manual_model_not_allowed')
    emoji_binding=__import__('emoji_runtime').bind(root,operator,config)
    audit=root/'runtime/manual-audit';audit.mkdir(exist_ok=True)
    (audit/'emoji-binding.json').write_text(json.dumps(emoji_binding))
    proxy=ParameterProxy(operator['base_url'],operator['api_key'],config['model'],config.get('parameters',{}),audit/'model-parameters.jsonl',image_workspace=root)
    memory=ParameterProxy(operator['base_url'],operator['api_key'],'deepseek-v4-pro',{},audit/'memory-model-parameters.jsonl')
    assembled=profile(root,config['persona'],config['model'],proxy.url,config.get('max_output_tokens'),config.get('reasoning_effort'))
    providers=next(x for x in assembled if x.get('id')=='llm-pi-ai')['config']['providers']
    providers['experiment-memory-provider']={'api':'openai-completions','baseURL':memory.url,'apiKeyEnv':'TUTOR_MEMORY_API_KEY','models':[{'id':'deepseek-v4-pro','contextWindow':131072,'maxTokens':32768,'reasoningEfforts':{'low':'low','high':'high','max':'max'}}],'compat':{'supportsDeveloperRole':False,'thinkingFormat':'deepseek'}}
    home=root/'runtime/dsh-home';pr=home/'profiles/web';pr.mkdir(parents=True,exist_ok=True)
    write_json(pr/'package.json',{'name':'dsh-profile-web','private':True,'dependencies':{},'dsh':{'profile':{'bundles':['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app']}}})
    (pr/'cordis.yml').write_text('[]');write_json(pr/'cordis.patch.yml',assembled)
    modules=Path(operator['node_modules']).resolve();node=Path(operator['node']).resolve()
    if not (pr/'node_modules').exists():(pr/'node_modules').symlink_to(modules,target_is_directory=True)
    for folder in ['home','tmp','cache','agents-home']:(root/'runtime'/folder).mkdir(exist_ok=True)
    env={**os.environ,'PATH':str(node.parent)+':/usr/local/bin:/usr/bin:/bin','HOME':str(root/'runtime/home'),'TMPDIR':str(root/'runtime/tmp'),'XDG_CACHE_HOME':str(root/'runtime/cache'),'DSH_HOME':str(home),'DSH_AGENTS_HOME':str(root/'runtime/agents-home'),'DSH_PERMISSION_MODE':'workspace-write','TUTOR_ROOT':str(root),'TUTOR_MANUAL_WORKBENCH':'1','TUTOR_API_KEY':proxy.token,'TUTOR_MEMORY_API_KEY':memory.token,'TUTOR_WORKBENCH_PORT':str(workbench_port),'PYTHONDONTWRITEBYTECODE':'1'}
    for key in list(env):
        if key.startswith('TUTOR_EXPERIMENT_'):env.pop(key)
    knowledge=bind_knowledge(root,operator)
    if knowledge:env['TUTOR_KNOWLEDGE_CONFIG_FILE']=str(knowledge)
    voice=__import__('voice_understanding').bind(root,operator)
    if voice:env['TUTOR_VOICE_BINDING_SHA256']=voice['sha256']
    from knowledge_search import CLI
    subprocess.run([CLI,'language','en'],env=env,capture_output=True,check=True,timeout=15)
    proxy.start();memory.start()
    child=subprocess.Popen([str(node),str(modules/'@deepseek-ai/dsh/lib/bin.js'),'web','--port',str(native_port),'--no-open'],cwd=root,env=env)
    def stop(*args):
        if child.poll() is None:child.terminate()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:raise SystemExit(child.wait())
    finally:stop();proxy.close();memory.close()

if __name__=='__main__':main()
