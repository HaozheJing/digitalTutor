import test from 'node:test';
import assert from 'node:assert/strict';
import {apply,memoryTools} from './memory-agent.mjs';
import {memoryPreset,installMemoryTurnCompletion} from './memory-runtime.mjs';

function context(){const hooks=new Map(),contexts=[],sections=[];let suppressed=false;
 return {hooks,contexts,sections,get suppressed(){return suppressed;},on(name,fn){hooks.set(name,fn);},systemPrompt:{context(x){contexts.push(x);},section(x){sections.push(x);},suppressRuntimeContext(){suppressed=true;}}};}
test('memory preset omits customer service, skill and send capabilities',()=>{
 const p=memoryPreset('/isolated');assert.equal(p.id,'family-memory-night');assert.equal(p.plugins.length,3);
 assert.equal(p.plugins[1].config.args[0],'/isolated/adapters/memory_session.py');
});
test('scoped memory prompt suppresses duplicated runtime recall and exposes four tools',async()=>{
 const ctx=context();apply(ctx);
 assert.equal(ctx.contexts[0].name,'tutor-live-context');assert.equal(ctx.contexts[0].text,'');assert.equal(ctx.suppressed,true);
 const seed={tools:[...memoryTools].map(name=>({name})).concat([{name:'send_message'}]),variables:{model:'instant-model'},contexts:['security']};
 const result=await ctx.hooks.get('system-prompt/assemble')(seed,{},async()=>seed);
 assert.deepEqual(result.tools.map(x=>x.name),[...memoryTools]);assert.deepEqual(result.contexts,['security']);assert.equal(seed.tools.length,5);
 await assert.rejects(()=>ctx.hooks.get('system-prompt/assemble')({}, {},async()=>({tools:[],variables:{}})),/unavailable/);
});
test('memory retains pro low and output budget without changing the shared model',async()=>{
 const ctx=context();apply(ctx);const seed={provider:'same-provider',model:'cheap-daytime',reasoningEffort:'high',maxTokens:4096};
 const selected=await ctx.hooks.get('agent/request')({},async()=>seed);
 assert.deepEqual(selected,{provider:'same-provider',model:'deepseek-v4-pro',reasoningEffort:'low',maxTokens:32768});assert.equal(seed.model,'cheap-daytime');
});
test('memory scope rejects unlisted execution as well as hiding its schema',async()=>{
 const ctx=context();apply(ctx);let called=false;
 await assert.rejects(()=>ctx.hooks.get('tools/execute')({name:'send_message'},async()=>{called=true;}),/not_allowed/);assert.equal(called,false);
});
test('five-family memory uses the isolated metered memory provider',async()=>{
 const before=process.env.TUTOR_FAMILY_LAB;process.env.TUTOR_FAMILY_LAB='1';
 try{const ctx=context();apply(ctx);const selected=await ctx.hooks.get('agent/request')({},async()=>({provider:'daytime-provider'}));assert.equal(selected.provider,'experiment-memory-provider');assert.equal(selected.model,'deepseek-v4-pro');}
 finally{if(before===undefined)delete process.env.TUTOR_FAMILY_LAB;else process.env.TUTOR_FAMILY_LAB=before;}
});
function completion(){const ctx=context(),requests=new Map([['host-request','night-event']]);let verified=true,verifyCalls=0,concluded=0,cancelled=0;
 const agent={cancel(){cancelled++;}};
 const control=installMemoryTurnCompletion(ctx,requests,async(eventId,token)=>{verifyCalls++;return {completed:verified&&token==='persisted',event_id:eventId};});
 const claim=(id='host-request')=>ctx.hooks.get('agent/inbox/claimed')({agent,message:{source:{kind:'user',rpcId:id}}});
 const exec={agent,name:'mcp__memory__memory_consolidation_commit',arguments:{event_id:'night-event',prepared_token:'persisted'},signal:{aborted:false},concludeTurn(){concluded++;}};
 const result={isError:false,value:{content:[{type:'text',text:JSON.stringify({status:'committed',event_id:'night-event'})}]}};
 return {ctx,requests,agent,control,claim,exec,result,set verified(v){verified=v;},get concluded(){return concluded;},get verifyCalls(){return verifyCalls;},get cancelled(){return cancelled;},run(){return ctx.hooks.get('tools/execute')(exec,async()=>result);}};}
test('persisted bound commit concludes without a final model summary',async()=>{
 const h=completion();h.claim();const result=await h.run();assert.equal(h.concluded,1);assert.equal(h.verifyCalls,1);assert.notEqual(result,h.result);
});
test('unknown ownership, failed verification, errors and cancellation do not conclude',async()=>{
 for(const change of [h=>h.claim('unknown'),h=>{h.verified=false;},h=>{h.result.isError=true;},h=>{h.exec.signal.aborted=true;},h=>{h.exec.arguments.event_id='foreign';},h=>{h.requests.clear();}]){
  const h=completion();h.claim();change(h);await h.run();assert.equal(h.concluded,0);
 }
});
test('new dialogue cancels only owned memory agents and idle clears ownership',()=>{
 const h=completion();h.claim();h.control.interrupt();assert.equal(h.cancelled,1);
 h.ctx.hooks.get('agent/status')({agent:h.agent,status:'idle'});h.control.interrupt();assert.equal(h.cancelled,1);
});
test('native context refresh retains ownership but a new unrelated message revokes it',async()=>{
 const h=completion();h.claim();h.ctx.hooks.get('agent/inbox/claimed')({agent:h.agent,message:{source:{kind:'runtime-context'}}});await h.run();assert.equal(h.concluded,1);
 h.claim('unrelated');await h.run();assert.equal(h.concluded,1);
});
