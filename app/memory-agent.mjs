// Scoped native preset: retain the same model and evidence protocol, omit the
// customer-service history, skill catalogue, scheduling and sending tools.
export const name='family-memory-agent';
export const inject=['systemPrompt'];
export const memoryTools=new Set(['memory_consolidation_context','memory_evidence_read','memory_consolidation_prepare','memory_consolidation_commit'].map(n=>'mcp__memory__'+n));
export function apply(ctx){
 ctx.systemPrompt.context({name:'tutor-live-context',order:500,required:true,interpolate:false,text:''});
 ctx.systemPrompt.suppressRuntimeContext();
 ctx.systemPrompt.context({name:'tool-result-semantics',order:510,required:true,interpolate:false,text:''});
 ctx.systemPrompt.section({name:'deployment:persona-prefix',order:0,interpolate:false,text:'你负责当前家庭的夜间记忆维护。只分析提供的原文、已有记忆和证据，遵守完整记忆政策及提交前复核。'});
 ctx.on('system-prompt/assemble',async(_assembly,_context,next)=>{
  const value=await next();const tools=value.tools.filter(t=>memoryTools.has(t.name));
  if(tools.length!==memoryTools.size)throw Error('memory_tools_unavailable');
  return {...value,tools,variables:{...value.variables,model:'deepseek-v4-pro'}};
 },{prepend:true});
 ctx.on('agent/request',async(_request,next)=>({...await next(),model:'deepseek-v4-pro',...((process.env.TUTOR_EXPERIMENT_ID||process.env.TUTOR_MANUAL_WORKBENCH||process.env.TUTOR_FAMILY_LAB)?{provider:'experiment-memory-provider'}:{}),reasoningEffort:'low',maxTokens:32768}),{prepend:true});
 ctx.on('tools/execute',async(exec,next)=>{
  if(!memoryTools.has(exec.name))throw Error('memory_only_tool_not_allowed');
  return next();
 },{prepend:true});
}
