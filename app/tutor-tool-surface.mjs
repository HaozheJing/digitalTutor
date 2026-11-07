// Experiment-wide retirement of model entry points, not host capabilities.
// Harness 0.2.1-rc.2 registers MCP resource tools in the caller's own scope;
// tools.restrict() intentionally exempts that scope. Use the public assembly
// waterfall plus a monotonic execution guard, without editing dependencies.
export const retiredTutorTools=Object.freeze([
 'list_mcp_resource_templates','list_mcp_resources','read_mcp_resource',
 'working_directory','schedule_create','schedule_update','schedule_delete','schedule_list',
]);
const retired=new Set(retiredTutorTools);

export function assertTutorToolSurface(tools=[],additional=[]){
 const blocked=new Set([...retired,...additional]);
 const leaked=tools.filter(tool=>blocked.has(tool.name)).map(tool=>tool.name);
 if(leaked.length)throw Error('retired_tutor_tool_in_model_request:'+leaked.join(','));
}

export function installTutorToolSurface(ctx,{additional=()=>[]}={}){
 const blocked=()=>new Set([...retired,...additional()]);
 // Keep the resource provider, workingDirectory and schedule services intact.
 // Direct/nested model tool dispatch still passes this final guard, including
 // stale history and future registrations. Host service calls are unaffected.
 ctx.tools.guard(exec=>blocked().has(exec.name)?'tutor_tool_entry_retired:'+exec.name:undefined);
 ctx.on('system-prompt/assemble',async(_assembly,_context,next)=>{
  const value=await next(),excluded=blocked();
  return {...value,tools:value.tools.filter(tool=>!excluded.has(tool.name)),
   sections:value.sections.filter(section=>section.name!=='mcp-resource-servers')};
 },{prepend:true});
}
