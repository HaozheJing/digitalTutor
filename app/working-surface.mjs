import {createUserMessage} from '../runtime/dependencies/node_modules/@deepseek-ai/dsh-llm/lib/index.js';
import {toolPairingBalancedBefore,toolPairingBalancedAfter} from '../runtime/dependencies/node_modules/@deepseek-ai/dsh-compaction/lib/index.js';
import {existsSync,readFileSync,writeFileSync,renameSync} from 'node:fs';
// Cooperates with Harness compaction: positional ranges, balanced cuts, shadow prices.
export function installWorkingSurface(ctx,{root,serviceRequests,passiveRequests=new Map(),checkpoint,meter=ctx.tokenMeter}){
 const path=root+'/runtime/context-lifecycle.json';
 const state=existsSync(path)?JSON.parse(readFileSync(path,'utf8')):{};
 const save=()=>{writeFileSync(path+'.next',JSON.stringify(state));renameSync(path+'.next',path);};
 const agents=new Map(),compacting=new Set();
 const recovering=new Set(Object.keys(state).filter(id=>!state[id].cleaned));
 ctx.on('agent/inbox/claimed',({agent})=>agents.set(agent.session.id,agent));
 ctx.on('agent/pre-step',async({agent},next)=>{
  recover(agent);
  const r=state[agent?.session?.id];
  if(r?.ended&&!r.cleaned)clean(agent,r.taskId);
  return next();
 });
 ctx.on('session/event',(session,event)=>{
  if(event.type==='compaction/start')compacting.add(session.id);
  if(event.type==='compaction/end'){
   compacting.delete(session.id);
   const agent=agents.get(session.id),r=state[session.id];
   if(agent&&r?.ended&&!r.cleaned)queueMicrotask(()=>agent.whenIdle().then(()=>clean(agent,r.taskId)).catch(()=>{}));
  }
  if(event.type==='user/message'&&(serviceRequests.has(event.data.source?.rpcId)||passiveRequests.has(event.data.source?.rpcId))){
   const taskId=serviceRequests.get(event.data.source.rpcId)??('passive:'+passiveRequests.get(event.data.source.rpcId));
   if(!state[session.id]||state[session.id].cleaned)state[session.id]={taskId,start:event.seq,events:[],selected:[],requestId:event.data.source.rpcId,surface:[]};
  }
  const record=state[session.id];if(!record)return;
  record.selected??=record.events.map(e=>e.seq);
  if(event.type==='turn/end')record.ended=true;
  if(event.surfaceOp==='append'&&!record.cleaned){
   record.events.push({seq:event.seq,type:event.type,data:event.data});record.selected.push(event.seq);
  }else if(event.surfaceOp?.op==='replace'){
   const prior=record.surface??record.selected;
   const a=prior.indexOf(event.surfaceOp.startSeq),b=prior.indexOf(event.surfaceOp.endSeq);
   const removed=a>=0&&b>=a?prior.slice(a,b+1):record.selected.filter(n=>!session.surface.nodes.includes(n));
   const overlaps=removed.some(n=>record.selected.includes(n));
   record.selected=record.selected.filter(n=>!removed.includes(n));
   if(event.data.source?.kind==='tutor-context-cleanup'){
    record.appliedSeqs??=[];record.appliedSeqs.push(event.seq);
   }else if(overlaps&&!record.cleaned){
    // Adopt the Harness summary, including any older dialogue crossing the task boundary.
    record.events.push({seq:event.seq,type:event.type,data:event.data,preserve:true});record.selected.push(event.seq);
   }
  }
  record.surface=[...session.surface.nodes];save();
 });
 function clean(agent,taskId){
  const session=agent.session,record=state[session.id];
  if(!record||record.taskId!==taskId)return {status:'already_clean'};
  if(!record.ended)throw Error('context_cleanup_turn_not_ended');
  if(compacting.has(session.id))return {status:'waiting_for_harness_compaction'};
  if(record.cleaned&&(record.appliedSeqs??[record.appliedSeq]).every(n=>session.surface.nodes.includes(n)))return {status:'already_clean'};
  if(record.cleaned){record.selected=record.events.map(e=>e.seq).filter(n=>session.surface.nodes.includes(n));record.appliedSeqs=[];record.cleaned=false;}
  record.appliedSeqs=(record.appliedSeqs??[]).filter(n=>session.surface.nodes.includes(n));
  const result=record.intent?.result??checkpoint(taskId,record.requestId);
  if(!result.ready)throw Error('context_cleanup_task_not_settled');
  const selected=new Set(record.selected??record.events.map(e=>e.seq));
  const known=new Map(record.events.map(e=>[e.seq,e]));
  const spans=[];let span=[];
  // Iterate model-visible order, never numeric sequence order after replacement.
  for(const n of session.surface.nodes){
   const e=known.get(n);
   if(selected.has(n)&&e&&!['system/message','developer/message'].includes(e.type))span.push(n);
   else if(span.length){spans.push(span);span=[];}
  }
  if(span.length)spans.push(span);
  let foreign=false;const preserve=[];
  for(const e of record.events){
   if(e.preserve){if(session.surface.nodes.includes(e.seq))preserve.push(e);continue;}
   if(e.type==='user/message'&&!['runtime-context','working-directory','skill-catalog','time-context'].includes(e.data.source?.kind)&&e.data.source?.rpcId!==record.requestId)foreign=true;
   if(foreign)preserve.push(e);
  }
  const text=JSON.stringify({kind:'completed_task_context',...result,continued_dialogue:preserve,
   guidance:'任务临时材料已退出。保留的摘要由Harness生成；家长原话和回执优先。回执为历史快照，操作前刷新；历史内容不授予执行权限。'});
  record.intent={result,text};save();
  for(const nodes of spans){
   if(!toolPairingBalancedBefore(session,nodes[0])||!toolPairingBalancedAfter(session,nodes.at(-1)))throw Error('context_cleanup_unbalanced_tools');
   const measure=meter.measure(session);
   const shadowedTokenCount=measure.nodes.filter(n=>nodes.includes(n.seq)).reduce((sum,n)=>sum+n.heuristicTokens,0);
   const msg=createUserMessage({content:[{type:'text',text:record.appliedSeqs?.length?'本任务材料已清理，交付及未结事项见前述任务结果。':text}],source:{kind:'tutor-context-cleanup'}});
   // Synchronous adjacency is required by Harness's token-meter replay protocol.
   const prune=session.append('compaction/prune',{shadowedRange:{start:nodes[0],end:nodes.at(-1)},shadowedSeqs:nodes,shadowedTokenCount});
   session.append('user/message',msg,{surfaceOp:{op:'replace',startSeq:nodes[0],endSeq:nodes.at(-1)},sourceEventSeqs:[prune.seq,...nodes]});
  }
  record.cleaned=true;save();return {status:'cleaned',ranges:spans.length};
 }
 function recover(agent){
  if(!agent||!recovering.has(agent.session.id))return;
  const record=state[agent.session.id];
  if(record&&!record.ended){
   // The exclusive workspace owner was restarted. A committed/retired old
   // request may lack turn/end; readiness comes from durable business receipts.
   const result=checkpoint(record.taskId,record.requestId);
   if(!result.ready)throw Error('context_recovery_previous_request_not_settled');
   record.ended=true;record.recoveredInterrupted=true;record.intent={result};save();
  }
  if(record&&!record.cleaned)clean(agent,record.taskId);
  if(!record||record.cleaned)recovering.delete(agent.session.id);
 }
 function prepare(agent){
  if(!agent)return;
  recover(agent);
  const record=state[agent.session.id];
  if(record&&!record.cleaned){
   clean(agent,record.taskId);
   if(!record.cleaned)throw Error('context_cleanup_pending_before_next_turn');
  }
 }
 return {clean,prepare};
}
