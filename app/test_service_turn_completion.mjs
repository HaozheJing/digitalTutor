import test from 'node:test';
import assert from 'node:assert/strict';
import {installServiceTurnCompletion} from './service-turn-completion.mjs';

function harness(finish){
 const hooks=new Map(),requests=new Map([['request','task']]);
 installServiceTurnCompletion({on:(name,fn)=>hooks.set(name,fn)},requests,finish);
 const agent={session:{id:'session'}},name='mcp__tutor__deliver_message';
 hooks.get('agent/inbox/claimed')({agent,message:{source:{kind:'user',rpcId:'request'}}});
 const batch=(names=[name])=>hooks.get('session/event')(agent.session,{type:'assistant/message',data:{message:{content:names.map(name=>({type:'tool-call',name}))}}});
 const result=(status='accepted')=>({isError:false,value:{content:[{type:'text',text:JSON.stringify({status,action_key:'key'})}]}});
 let concluded=0;
 const exec={agent,name,signal:{aborted:false},concludeTurn(){concluded++;}};
 return {hooks,requests,agent,name,batch,result,exec,get concluded(){return concluded;},run:res=>hooks.get('tools/execute')(exec,async()=>res)};
}

test('host registers each routine S01 result and concludes without a model finish',async()=>{
 for(const [receipt,status,outcome] of [['accepted','awaiting_delivery','awaiting_delivery'],['sent','done','completed'],['skipped','no_contact','no_contact'],['deferred','deferred','deferred'],['unknown','blocked','blocked'],['failed','blocked','blocked'],['partial','blocked','blocked']]){
  let calls=0;const h=harness(async(owner,name,key,actual)=>{calls++;assert.equal(owner.taskId,'task');assert.equal(name,'deliver_message');assert.equal(actual.status,receipt);return {task_id:'task',status,outcome};});
  h.batch();const result=h.result(receipt);assert.deepEqual(await h.run(result),result);assert.equal(h.concluded,1);assert.equal(calls,1);
 }
});
test('failed registration, unknown status, tool errors and needs_revision never claim completion',async()=>{
 for(const finish of [async()=>({task_id:'task',status:'continue'}),async()=>({task_id:'other',status:'done',outcome:'completed'}),async()=>{throw Error('database unavailable');}]){
  const h=harness(finish);h.batch();await h.run(h.result());assert.equal(h.concluded,0);
 }
 const h=harness(async()=>{throw Error('must not run');});h.batch();await h.run({...h.result(),isError:true});await h.run(h.result('needs_revision'));assert.equal(h.concluded,0);
});
test('foreign or interrupted turn and sibling tools cannot register this task',async()=>{
 for(const kind of ['sibling','foreign','aborted','unregistered']){
  let calls=0;const h=harness(async()=>{calls++;return {task_id:'task',status:'done',outcome:'completed'};});h.batch();
  if(kind==='sibling')h.batch([h.name,'mcp__tutor__learning_query']);
  if(kind==='foreign')h.hooks.get('agent/inbox/claimed')({agent:h.agent,message:{source:{kind:'user',rpcId:'other'}}});
  if(kind==='aborted')h.exec.signal.aborted=true;
  if(kind==='unregistered')h.requests.clear();
  await h.run(h.result());assert.equal(calls,0);assert.equal(h.concluded,0);
 }
});
test('new steering during host registration is not concluded',async()=>{
 let h;h=harness(async()=>{h.hooks.get('agent/inbox/claimed')({agent:h.agent,message:{source:{kind:'user',rpcId:'other'}}});return {task_id:'task',status:'done',outcome:'completed'};});
 h.batch();await h.run(h.result());assert.equal(h.concluded,0);
});

test('S02 proactive results are passed to the host and only registered results conclude',async()=>{
 for(const [status,outcome] of [['accepted','awaiting_delivery'],['skipped','no_contact'],['deferred','deferred'],['unknown','blocked'],['expired','no_contact']]){
  const h=harness(async(owner,name,key,receipt)=>{assert.equal(name,'deliver_proactive');assert.equal(receipt.status,status);return {task_id:'task',status:outcome,outcome};});
  h.exec.name='mcp__tutor__deliver_proactive';h.batch([h.exec.name]);await h.run(h.result(status));assert.equal(h.concluded,1);
 }
 const h=harness(async()=>({status:'continue'}));h.exec.name='mcp__tutor__deliver_proactive';h.batch([h.exec.name]);await h.run(h.result('skipped'));assert.equal(h.concluded,0);
});

test('unified message receipt uses original delivery route and preserves registered outcomes',async()=>{
 for(const [route,status,outcome] of [['deliver_message','sent','completed'],['deliver_proactive','unknown','blocked'],['deliver_proactive','accepted','awaiting_delivery']]){
  const h=harness(async(owner,name,key,receipt)=>{assert.equal(name,route);assert.equal(receipt.status,status);return {task_id:'task',status:outcome==='completed'?'done':outcome,outcome};});
  h.exec.name='mcp__tutor__send_message';h.batch([h.exec.name]);const result=h.result(status);
  const payload=JSON.parse(result.value.content[0].text);payload.delivery_route=route;result.value.content[0].text=JSON.stringify(payload);
  await h.run(result);assert.equal(h.concluded,1);
 }
 const h=harness(async()=>{throw Error('untrusted route');});h.exec.name='mcp__tutor__send_message';h.batch([h.exec.name]);
 await h.run(h.result('sent'));assert.equal(h.concluded,0);
});
