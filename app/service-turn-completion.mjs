import {createHash} from 'node:crypto';
// Only a successfully registered, host-dispatched service task may omit its
// final model summary. The native loop still commits every sibling tool result
// and processes fresh steering; no cancellation or session-log edits are used.
const finishTool='mcp__tutor__service_task_finish';
const outcomes=new Map([
 ['done','completed'],['no_contact','no_contact'],['blocked','blocked'],
 ['deferred','deferred'],['awaiting_delivery','awaiting_delivery'],
]);

export function registeredFinish(exec,result,taskId){
 if(exec.name!==finishTool||result?.isError!==false||exec.signal?.aborted)return false;
 const blocks=result.value?.content;
 if(!Array.isArray(blocks)||blocks.length!==1||blocks[0].type!=='text')return false;
 let receipt;
 try{receipt=JSON.parse(blocks[0].text);}catch{return false;}
 if(typeof taskId!=='string'||!taskId)return false;
 const ref='ref-'+createHash('sha256').update(taskId).digest('hex').slice(0,24);
 return !!receipt&&[taskId,ref].includes(exec.arguments?.task_id)&&
  [taskId,ref].includes(receipt.task_id)&&outcomes.has(receipt.status)&&
  receipt.outcome===outcomes.get(receipt.status)&&
  typeof receipt.reason==='string'&&receipt.reason.trim().length>0;
}

const deliveryTools=new Set(['mcp__tutor__send_message','mcp__tutor__deliver_message','mcp__tutor__deliver_proactive','mcp__tutor__deliver_festival','mcp__tutor__send_image']);
const fencedTools=new Set([...deliveryTools,'mcp__tutor__reply_to_message','mcp__tutor__reply_with_highlights','mcp__tutor__workflow_fulfill','mcp__tutor__workflow_highlight']);

export function installServiceTurnCompletion(ctx,serviceRequests,finishDelivery){
 const owners=new WeakMap();
 const batches=new Map();
 ctx.on('session/event',(session,event)=>{
  if(event.type==='assistant/message'){
   const calls=event.data.message.content.filter(block=>block.type==='tool-call');
   batches.set(session.id,calls.map(call=>call.name));
  }else if(event.type==='turn/end')batches.delete(session.id);
 });
 ctx.on('agent/inbox/claimed',({agent,message})=>{
  // Bind on consumption, not insertion: a queued service must not grant this
  // behavior to a different turn already running on the same agent.
  const requestId=message.source?.rpcId;
  if(message.source?.kind==='user'&&serviceRequests.has(requestId))
   owners.set(agent,{requestId,taskId:serviceRequests.get(requestId)});
  // Native environment refreshes describe the same running task. They neither
  // grant ownership nor revoke an existing claimed host request. A fresh user,
  // schedule, or unknown source still clears it below.
  else if(['working-directory','runtime-context'].includes(message.source?.kind))return;
  else owners.delete(agent);
 });
 ctx.on('agent/status',({agent,status})=>{if(status==='idle')owners.delete(agent);});
 ctx.on('agent/disposed',({agent})=>owners.delete(agent));
 ctx.on('tools/execute',async(exec,next)=>{
  const result=await next();
  if(fencedTools.has(exec.name)&&result?.isError===false&&typeof exec.concludeTurn==='function'&&!exec.signal?.aborted){
   let receipt;try{receipt=JSON.parse(result.value?.content?.[0]?.text);}catch{}
   if(receipt?.status==='replan_required'&&receipt.blocked_by==='message_hooks'){
    exec.concludeTurn();return {...result};
   }
  }
  const owner=exec.agent&&owners.get(exec.agent);
  if(!owner||serviceRequests.get(owner.requestId)!==owner.taskId||
     typeof exec.concludeTurn!=='function'||exec.signal?.aborted)return result;
  let registered=registeredFinish(exec,result,owner.taskId);
  const batch=batches.get(exec.agent.session?.id);
  // Never clear an active task in the middle of sibling calls. Multi-tool
  // batches keep the existing model-owned registration flow.
  if(!registered&&finishDelivery&&deliveryTools.has(exec.name)&&
     result?.isError===false&&batch?.length===1&&batch[0]===exec.name){
   const blocks=result.value?.content;
   let receipt;
   try{if(blocks?.length===1&&blocks[0].type==='text')receipt=JSON.parse(blocks[0].text);}catch{}
   const route=exec.name==='mcp__tutor__send_message'?receipt?.delivery_route:exec.name.slice('mcp__tutor__'.length);
   const validRoute=['deliver_message','deliver_proactive','deliver_festival','send_image'].includes(route);
   const routine=route==='deliver_message'||route==='deliver_proactive';
   const handled=routine?['accepted','sent','skipped','deferred','unknown','failed','partial','expired','blocked']:['accepted','sent'];
   if(validRoute&&receipt&&handled.includes(receipt.status)&&(routine||typeof receipt.action_key==='string')){
    try{
     const finished=await finishDelivery(owner,route,receipt.action_key,routine?receipt:undefined);
     registered=finished?.task_id===owner.taskId&&
      (routine?outcomes.has(finished.status)&&outcomes.get(finished.status)===finished.outcome:
       ((finished.status==='done'&&finished.outcome==='completed')||
        (finished.status==='awaiting_delivery'&&finished.outcome==='awaiting_delivery')));
    }catch{/* Keep the true delivery result; original registration remains available. */}
   }
  }
  if(!registered||exec.signal?.aborted||owners.get(exec.agent)!==owner||
     serviceRequests.get(owner.requestId)!==owner.taskId)return result;
  exec.concludeTurn();
  // A fresh result passes through the runtime's canonical output validation
  // again, which attaches the execution-owned concludesTurn marker. Later
  // post-execute rejection/cancellation still turns it into a non-terminal error.
  return {...result};
 },{prepend:true});
}
