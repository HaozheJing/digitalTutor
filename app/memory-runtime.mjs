export const memoryPresetId='family-memory-night';
export function memoryPreset(root){return {id:memoryPresetId,name:'家庭夜间记忆',order:100,plugins:[
 {id:'memory-agent',name:root+'/app/memory-agent.mjs'},
 {id:'memory-mcp',name:'@deepseek-ai/dsh-mcp-client',config:{transport:'stdio',serverName:'memory',command:'python3',args:[root+'/adapters/memory_session.py'],cwd:root+'/adapters',failOnStartupError:true,toolCallTimeoutMs:150000}},
 {id:'compaction',name:'cordis:group',group:true,isolate:{compaction:true},config:[
  {id:'compaction-basic',name:'@deepseek-ai/dsh-compaction-basic',config:{headroomTokens:8192,maxTokens:4096}},
  {id:'command-compact',name:'@deepseek-ai/dsh-command-compact'}]}
]};}

// Success means a bound commit was persisted, not that the model said done.
export function installMemoryTurnCompletion(ctx,requests,verify){
 const owners=new Map();
 ctx.on('agent/inbox/claimed',({agent,message})=>{
  const requestId=message.source?.rpcId;
  if(message.source?.kind==='user'&&requests.has(requestId))owners.set(agent,{requestId,eventId:requests.get(requestId)});
  else if(!['working-directory','runtime-context'].includes(message.source?.kind))owners.delete(agent);
 });
 ctx.on('agent/status',({agent,status})=>{if(status==='idle')owners.delete(agent);});
 ctx.on('agent/disposed',({agent})=>owners.delete(agent));
 ctx.on('tools/execute',async(exec,next)=>{
  const result=await next(),owner=owners.get(exec.agent);
  if(!owner||requests.get(owner.requestId)!==owner.eventId||exec.name!=='mcp__memory__memory_consolidation_commit'||
     result?.isError!==false||exec.signal?.aborted||typeof exec.concludeTurn!=='function')return result;
  let receipt;try{receipt=JSON.parse(result.value?.content?.[0]?.text);}catch{return result;}
  if(receipt?.status!=='committed'||receipt.event_id!==owner.eventId||exec.arguments?.event_id!==owner.eventId||!exec.arguments?.prepared_token)return result;
  const confirmed=await verify(owner.eventId,exec.arguments.prepared_token);
  if(confirmed?.completed===true&&confirmed.event_id===owner.eventId&&!exec.signal?.aborted){exec.concludeTurn();return {...result};}
  return result;
 },{prepend:true});
 return {interrupt(){for(const agent of owners.keys())agent.cancel({kind:'hook',reason:'dialogue_has_priority'});}};
}
