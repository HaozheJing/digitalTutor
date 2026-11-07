import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Session} from '../runtime/dependencies/node_modules/@deepseek-ai/dsh-session/lib/index.js';
import {createUserMessage,CONTEXT_WINDOW_EXCEEDED_CODE} from '../runtime/dependencies/node_modules/@deepseek-ai/dsh-llm/lib/index.js';
import {installWorkingSurface} from './working-surface.mjs';
import {checkRequestBudget} from './request-budget.mjs';
function fixture(){
 const root=mkdtempSync(join(tmpdir(),'tutor-context-'));mkdirSync(root+'/runtime');
 const hooks={};const ctx={on(n,fn){hooks[n]=fn;}};
 const session=Session.create('session-context-test');
 const append=session.append.bind(session);session.append=(...args)=>{const e=append(...args);hooks['session/event']?.(session,e);return e;};
 const requests=new Map([['request-active','task-1']]);
 ctx.tokenMeter={measure(s){return {nodes:s.surface.nodes.map(seq=>({seq,heuristicTokens:100}))};}};
 const checkpoint=()=>({ready:true,task:{id:'task-1'},deliveries:[{status:'sent',action_key:'pdf'},{status:'unknown',action_key:'voice'}]});
 const api=installWorkingSurface(ctx,{root,serviceRequests:requests,checkpoint});
 const message=(text,rpcId)=>session.append('user/message',createUserMessage({content:[{type:"text",text}],source:{kind:'user',rpcId}}),{surfaceOp:'append'});
 return {root,session,hooks,api,message,checkpoint,requests,ctx};
}
test('real DSH surface replacement retains audit, partial receipts and interleaved parent',()=>{
 const f=fixture();f.message('earlier passive','parent-before');const original=f.message('temporary learning '.repeat(2000),'request-active');
 f.message('先别发，等我确认','parent-during');f.hooks['session/event'](f.session,{type:'turn/end',seq:f.session.seq});
 f.api.clean({session:f.session},'task-1');
 assert.equal(f.session.surface.nodes.length,2);
 const messages=f.session.surface.nodes.map(n=>f.session.deriveEventMessage(f.session.eventAt(n)));
 assert.ok(messages.every(m=>Array.isArray(m.content)));
 const visible=JSON.stringify(messages);assert.match(visible,/先别发/);assert.match(visible,/unknown/);assert.match(visible,/pdf/);assert.doesNotMatch(visible,/temporary learning/);
 assert.match(JSON.stringify(f.session.eventAt(original.seq)),/temporary learning/);
 assert.equal(JSON.parse(readFileSync(f.root+'/runtime/context-lifecycle.json'))[f.session.id].cleaned,true);
 f.api.clean({session:f.session},'task-1');assert.equal(f.session.surface.nodes.length,2);
});
test('unfinished turn cannot be cleaned and durable journal resumes after crash',()=>{
 const f=fixture();f.message('materials','request-active');assert.throws(()=>f.api.clean({session:f.session},'task-1'),/turn_not_ended/);
 f.hooks['session/event'](f.session,{type:'turn/end',seq:f.session.seq});
 const recovered=installWorkingSurface(f.ctx,{root:f.root,serviceRequests:f.requests,checkpoint:f.checkpoint});
 recovered.clean({session:f.session},'task-1');assert.equal(f.session.surface.nodes.length,1);
});
test('assembled tools and later results are checked before provider',()=>{
 assert.throws(()=>checkRequestBudget({maxTokens:1,messages:[]}),/output_budget/);
 assert.throws(()=>checkRequestBudget({maxTokens:8192,messages:[],tools:[{description:'x'.repeat(400000)}]}),/context_budget/);
 assert.throws(()=>checkRequestBudget({maxTokens:8192,messages:[{role:'tool',content:'大'.repeat(150000)}]}),/context_budget/);
 assert.equal(checkRequestBudget({maxTokens:4096,messages:[]}).output,4096);
});

test('Harness compaction replacement is adopted and cleanup resumes without deleting its summary',()=>{
 const f=fixture();const original=f.message('large facts','request-active');
 f.hooks['session/event'](f.session,{type:'turn/end',seq:f.session.seq});
 f.hooks['session/event'](f.session,{type:'compaction/start',seq:f.session.seq});
 assert.equal(f.api.clean({session:f.session},'task-1').status,'waiting_for_harness_compaction');
 f.session.append('user/message',createUserMessage({content:[{type:'text',text:'parent asked to pause'}],source:{kind:'compact-checkpoint'}}),{surfaceOp:{op:'replace',startSeq:original.seq,endSeq:original.seq},sourceEventSeqs:[original.seq]});
 f.hooks['session/event'](f.session,{type:'compaction/end',seq:f.session.seq});
 assert.equal(f.api.clean({session:f.session},'task-1').status,'cleaned');
 assert.match(JSON.stringify(f.session.deriveEventMessage(f.session.eventAt(f.session.surface.nodes[0]))),/parent asked to pause/);
 const prior=f.session.eventAt(f.session.surface.nodes[0]-1);assert.equal(prior.type,'compaction/prune');assert.equal(prior.data.shadowedTokenCount,100);
});

test('nonmonotonic Harness replacement before task must not become an owned range',()=>{
 const f=fixture();const old=f.message('old parent agreement','earlier');const task=f.message('temporary task','request-active');
 const replacement=f.session.append('user/message',createUserMessage({content:[{type:'text',text:'older parent agreement summary'}],source:{kind:'compact-checkpoint'}}),{surfaceOp:{op:'replace',startSeq:old.seq,endSeq:old.seq},sourceEventSeqs:[old.seq]});
 assert.deepEqual([...f.session.surface.nodes],[replacement.seq,task.seq]);
 f.hooks['session/event'](f.session,{type:'turn/end',seq:f.session.seq});f.api.clean({session:f.session},'task-1');
 assert.equal(f.session.surface.nodes[0],replacement.seq);
});
test('failed Harness summary releases coordination lock and cleanup completes',()=>{
 const f=fixture();f.message('temporary facts','request-active');f.hooks['session/event'](f.session,{type:'turn/end',seq:f.session.seq});
 f.hooks['session/event'](f.session,{type:'compaction/start',seq:f.session.seq});
 assert.equal(f.api.clean({session:f.session},'task-1').status,'waiting_for_harness_compaction');
 f.hooks['session/event'](f.session,{type:'compaction/end',seq:f.session.seq,data:{error:'summary is not smaller'}});
 assert.equal(f.api.clean({session:f.session},'task-1').status,'cleaned');
});

test('local complete-request overflow enters the retained Harness recovery taxonomy',()=>{
 assert.throws(()=>checkRequestBudget({maxTokens:8192,messages:[{role:'user',content:[{type:'text',text:'x'.repeat(400000)}]}]}),error=>error.code===CONTEXT_WINDOW_EXCEEDED_CODE);
});


test('Harness catalog and time messages do not preserve completed task materials as parent dialogue',()=>{
 const f=fixture();f.message('temporary active task','request-active');
 for(const kind of ['skill-catalog','time-context','runtime-context','working-directory']){
  f.session.append('user/message',createUserMessage({content:[{type:'text',text:'host-only '+kind+' '+ 'x'.repeat(8000)}],source:{kind}}),{surfaceOp:'append'});
 }
 f.hooks['session/event'](f.session,{type:'turn/end',seq:f.session.seq});f.api.clean({session:f.session},'task-1');
 const visible=JSON.stringify(f.session.surface.nodes.map(n=>f.session.deriveEventMessage(f.session.eventAt(n))));
 assert.doesNotMatch(visible,/host-only/);assert.doesNotMatch(visible,/temporary active/);assert.ok(visible.length<2000);
 assert.match(visible,/unknown/);
});

test('parent message after Harness catalog still preserves parent and later dialogue evidence',()=>{
 const f=fixture();f.message('temporary active task','request-active');
 f.session.append('user/message',createUserMessage({content:[{type:'text',text:'host-only catalog'}],source:{kind:'skill-catalog'}}),{surfaceOp:'append'});
 f.message('先别发，等我确认','parent-during');f.message('parent related evidence','parent-followup');
 f.hooks['session/event'](f.session,{type:'turn/end',seq:f.session.seq});f.api.clean({session:f.session},'task-1');
 const visible=JSON.stringify(f.session.surface.nodes.map(n=>f.session.deriveEventMessage(f.session.eventAt(n))));
 assert.doesNotMatch(visible,/host-only catalog/);assert.match(visible,/先别发/);assert.match(visible,/parent related evidence/);
});

test('passive retry replaces its packet with real dialogue and keeps the same event identity',()=>{
 const f=fixture();const passiveRequests=new Map([['parent-rpc','event-1']]);
 const api=installWorkingSurface(f.ctx,{root:f.root,serviceRequests:f.requests,passiveRequests,
 checkpoint:(id,rpc)=>({ready:true,kind:'passive_dialogue_checkpoint',event_id:id,closed_request_id:rpc,parent_messages:[{body:'先别发，等我确认'}],deliveries:[{status:'unknown'}]})});
 const original=f.message('passive learning giant '.repeat(6000),'parent-rpc');
 f.hooks['session/event'](f.session,{type:'turn/end',seq:f.session.seq});
 api.clean({session:f.session},'passive:event-1');
 assert.doesNotMatch(JSON.stringify(f.session.deriveEventMessage(f.session.eventAt(f.session.surface.nodes[0]))),/giant/);
 assert.match(JSON.stringify(f.session.deriveEventMessage(f.session.eventAt(f.session.surface.nodes[0]))),/先别发/);
 assert.match(JSON.stringify(f.session.eventAt(original.seq)),/giant/);
 passiveRequests.set('retry-rpc','event-1');api.prepare({session:f.session});f.message('second packet','retry-rpc');
 f.hooks['session/event'](f.session,{type:'turn/end',seq:f.session.seq});api.clean({session:f.session},'passive:event-1');
 const visible=JSON.stringify(f.session.surface.nodes.map(n=>f.session.deriveEventMessage(f.session.eventAt(n))));
 assert.doesNotMatch(visible,/second packet|giant/);assert.match(visible,/unknown/);assert.match(visible,/passive:event-1/);
});

test('new dispatch waits for existing Harness compaction and retries cleanup after failure',()=>{
 const f=fixture();f.message('temporary facts','request-active');
 f.hooks['session/event'](f.session,{type:'turn/end',seq:f.session.seq});
 f.hooks['session/event'](f.session,{type:'compaction/start',seq:f.session.seq});
 assert.throws(()=>f.api.prepare({session:f.session}),/cleanup_pending/);
 f.hooks['session/event'](f.session,{type:'compaction/end',seq:f.session.seq,data:{error:'summary not smaller'}});
 f.api.prepare({session:f.session});assert.equal(f.session.surface.nodes.length,1);
});


test('restart retires committed request lacking turn/end before appending another packet',()=>{
 const f=fixture();f.message('interrupted temporary package '.repeat(4000),'request-active');
 assert.throws(()=>f.api.prepare({session:f.session}),/turn_not_ended/);
 const resumed=installWorkingSurface(f.ctx,{root:f.root,serviceRequests:f.requests,checkpoint:f.checkpoint});
 resumed.prepare({session:f.session});
 const visible=JSON.stringify(f.session.surface.nodes.map(n=>f.session.deriveEventMessage(f.session.eventAt(n))));
 assert.doesNotMatch(visible,/temporary package/);assert.match(visible,/unknown/);
 assert.equal(JSON.parse(readFileSync(f.root+'/runtime/context-lifecycle.json'))[f.session.id].recoveredInterrupted,true);
});

test('restart cannot discard an unsettled old request without durable readiness',()=>{
 const f=fixture();f.message('unsettled task','request-active');
 const resumed=installWorkingSurface(f.ctx,{root:f.root,serviceRequests:f.requests,checkpoint:()=>({ready:false})});
 assert.throws(()=>resumed.prepare({session:f.session}),/previous_request_not_settled/);
 assert.match(JSON.stringify(f.session.deriveEventMessage(f.session.eventAt(f.session.surface.nodes[0]))),/unsettled task/);
});
