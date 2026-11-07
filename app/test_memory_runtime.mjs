import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {memoryPresetId,memoryPreset} from './memory-runtime.mjs';
import {readFileSync} from 'node:fs';
import {apply as installMemoryAgent} from './memory-agent.mjs';

test('isolated nightly request keeps Pro thinking independent from Flash reply',async()=>{
 const previous=process.env.TUTOR_EXPERIMENT_ID;
 try {
  process.env.TUTOR_EXPERIMENT_ID='isolated-test';
  const hooks=new Map();
  installMemoryAgent({systemPrompt:{context(){},suppressRuntimeContext(){},section(){}},on(name,fn){hooks.set(name,fn);}});
  const request=await hooks.get('agent/request')({},async()=>({provider:'experiment-provider',model:'deepseek-v4.1-flash-economy',reasoningEffort:'none',maxTokens:8192}));
  assert.equal(request.provider,'experiment-memory-provider');
  assert.equal(request.model,'deepseek-v4-pro');
  assert.equal(request.reasoningEffort,'low');assert.equal(request.maxTokens,32768);
  delete process.env.TUTOR_EXPERIMENT_ID;
  const ordinary=await hooks.get('agent/request')({},async()=>({provider:'configured-provider'}));
  assert.equal(ordinary.provider,'configured-provider');
 } finally {
  if(previous===undefined)delete process.env.TUTOR_EXPERIMENT_ID;else process.env.TUTOR_EXPERIMENT_ID=previous;
 }
});

async function host(enabled=true) {
 const calls=[],prompts=[],events=new Map();let tick,pending=enabled;
 const state={run_id:'r',as_of:'2026-10-26T22:00:00+08:00',agent_planning:false};
 const run=(file,args)=>{
  calls.push([file,...args]);
  if(file==='test_mode.py')return state;
  if(file==='sql_tools.py')return {active:false,until:0};
  if(file==='live_runtime.py'){
   if(args[0]==='status')return {events:[]};
   if(args[0]==='claim'&&pending){pending=false;return {id:'nightly',kind:'memory_consolidation'};}
  }
  if(file==='memory_session.py')return {completed:false,prompt:'preloaded memory'};
  if(file==='business.py')return {prompt:'internal memory'};
  return null;
 };
 const source=readFileSync(new URL('./tutor-plugin.mjs',import.meta.url),'utf8')
  .replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,'')
  .replace(/^const root=.*;$/m,"const root='isolated';")+'\nglobalThis.apply=apply;';
 const context=vm.createContext({memoryPresetId,memoryPreset,installMemoryTurnCompletion:()=>({interrupt(){}}),JSON,Date,Intl,Map,Promise,AbortController,process:{env:{}},
  randomUUID:()=> 'uuid',setTimeout,clearInterval(){},setInterval(fn){tick=fn;},
  existsSync:path=>path.endsWith('preference-memory.json')||path.endsWith('workbench.json'),
  readFileSync:path=>JSON.stringify(path.endsWith('preference-memory.json')?{enabled}:{token:'test'}),writeFileSync(){},appendFileSync(){},
  execFileSync:(exe,args)=>JSON.stringify(run(args[0].split('/').pop(),args.slice(1))),
  createServer:()=>({listen(){},close(){}}),
  workbenchHandler:()=>()=>false,experimentHandler:()=>()=>false,learningEventsAdapter:()=>()=>{},
  installServiceTurnCompletion(){},syncWorkflowSchedules:async()=>{},workflowMode:'agent-workflow-v1'});
 vm.runInContext(source,context);
 context.apply({agentPresets:{async register(){return ()=>{};}},on(name,fn){events.set(name,fn);},systemPrompt:{context(){}},
  schedule:{async list(){return[];}},sessionController:{async create(args){return{sessionId:args.agentPreset};},async prompt(value){prompts.push(value);}}});
 await new Promise(resolve=>setTimeout(resolve,5));
 return {calls,prompts,events,tick};
}

test('nightly work uses native queue without obtaining a send generation',async()=>{
 const h=await host();
 assert.equal(h.prompts.length,1);assert.equal(h.prompts[0].sessionId,memoryPresetId);assert.equal(h.prompts[0].mode,'queue');
 assert.match(h.prompts[0].requestId,/^lab-run-/);
 assert.equal(h.calls.some(c=>c[0]==='message_hooks.py'&&c[1]==='begin'),false);
 assert.equal(h.calls.some(c=>c[0]==='preference_memory.py'&&c[1]==='tick'),true);
});

test('new user turn pauses internal memory ownership before model tools',async()=>{
 const h=await host();let entered=false;
 await h.events.get('agent/pre-step')({messages:[{source:{kind:'user',rpcId:'live-inbound-new'}}]},()=>{entered=true;});
 assert.equal(entered,true);
 assert.equal(h.calls.some(c=>c[0]==='preference_memory.py'&&c[1]==='pause'),true);
});

test('disabled memory does not poll or create an internal run',async()=>{
 const h=await host(false);await h.tick();
 assert.equal(h.calls.some(c=>c[0]==='preference_memory.py'),false);
 assert.equal(h.prompts.length,0);
});
