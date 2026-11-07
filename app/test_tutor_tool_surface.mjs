import test from 'node:test';
import assert from 'node:assert/strict';
import {Context} from '../runtime/dependencies/node_modules/@deepseek-ai/cordis/lib/index.js';
import SystemPrompt from '../runtime/dependencies/node_modules/@deepseek-ai/dsh-system-prompt/lib/index.js';
import ToolRuntime,{defineTool} from '../runtime/dependencies/node_modules/@deepseek-ai/dsh-tools/lib/index.js';
import {retiredTutorTools,installTutorToolSurface,assertTutorToolSurface} from './tutor-tool-surface.mjs';
import {syncWorkflowSchedules,workflowMode} from './workflow-schedules.mjs';

test('assembly removes exactly the first eight; second batch, memory and metadata survive',async()=>{
 const hooks=new Map(),guards=[];
 installTutorToolSurface({tools:{guard:g=>guards.push(g)},on:(name,fn)=>hooks.set(name,fn)});
 const retained=['mcp__tutor__service_schedule','mcp__tutor__service_schedule_cancel','mcp__tutor__save_plan',
  'mcp__tutor__pause_contact','mcp__tutor__service_case_feedback','mcp__tutor__memory_remember','skill',
  'mcp__tutor__generate_audio','mcp__memory__memory_consolidation_commit','mcp__tutor__reply_to_message'];
 const tools=[...retiredTutorTools,...retained].map(name=>({name,description:name,parameters:{type:'object'}}));
 const assembly={tools,sections:[{name:'mcp-resource-servers',text:'obsolete discovery advice'},
  {name:'persona',text:'unchanged'}],variables:{model:'unchanged'},other:{retained:true}};
 const actual=await hooks.get('system-prompt/assemble')(assembly,{},async()=>assembly);
 assert.deepEqual(actual.tools.map(t=>t.name),retained);
 assert.equal(actual.tools[0],tools[8]);assert.equal(actual.variables,assembly.variables);
 assert.equal(actual.other,assembly.other);assert.equal(assembly.tools.length,18);
 assert.deepEqual(actual.sections,[assembly.sections[1]]);
 assert.equal(guards[0]({name:'mcp__tutor__reply_to_message'}),undefined);
 assert.doesNotThrow(()=>assertTutorToolSurface(actual.tools));
 for(const name of retiredTutorTools)assert.throws(()=>assertTutorToolSurface([{name}]),/retired_tutor_tool_in_model_request/);
});

test('real Harness registry denies stale calls before bodies; host services and business tools remain callable',async()=>{
 const ctx=new Context();
 await ctx.plugin(SystemPrompt,{includeHarnessIdentity:false,includeRuntimeContext:false});
 await ctx.plugin(ToolRuntime);
 try{
  let bodies=0;
  const register=name=>ctx.tools.register(defineTool({name,description:name,parameters:{},
   output:{schema:{type:'json'},render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]},
   execute:async()=>{bodies++;return {status:'ok'};}}));
  for(const name of retiredTutorTools)register(name);
  register('mcp__tutor__reply_to_message');
  installTutorToolSurface(ctx);
  const assembled=await ctx.systemPrompt.assemble();
  assert.deepEqual(assembled.tools.map(t=>t.name),['mcp__tutor__reply_to_message']);
  for(const name of retiredTutorTools){
   const result=await ctx.tools.execute({name,arguments:{},signal:new AbortController().signal});
   assert.equal(result.isError,true,name);
   assert.match(JSON.stringify(result),/tutor_tool_entry_retired/);
  }
  assert.equal(bodies,0);
  const ok=await ctx.tools.execute({name:'mcp__tutor__reply_to_message',arguments:{},signal:new AbortController().signal});
  assert.equal(ok.isError,false);assert.equal(bodies,1);
  // The host calls the scheduling service, never the retired model tools.
  const removed=[],created=[];
  await syncWorkflowSchedules({list:async()=>[{id:'old',title:'live: obsolete'}],
   delete:async item=>removed.push(item.id),create:async(session,rule)=>created.push({session,rule})},
   'session',{service_workflow_mode:workflowMode,frozen:false},[]);
  assert.deepEqual(removed,['old']);assert.equal(created.length,1);
  assert.equal(created[0].rule.title,'workflow: morning');
 }finally{await ctx.fiber.dispose();}
});

test('additional retirement applies at assembly and execution without hiding nightly tools',async()=>{
 const hooks=new Map(),guards=[];let additional=['skill','mcp__tutor__service_skill'];
 installTutorToolSurface({tools:{guard:g=>guards.push(g)},on:(name,fn)=>hooks.set(name,fn)},{additional:()=>additional});
 const value={tools:['skill','mcp__tutor__skill_read','mcp__memory__memory_consolidation_commit'].map(name=>({name})),sections:[]};
 const output=await hooks.get('system-prompt/assemble')({},null,async()=>value);
 assert.deepEqual(output.tools.map(x=>x.name),['mcp__tutor__skill_read','mcp__memory__memory_consolidation_commit']);
 assert.match(guards[0]({name:'skill'}),/retired/);assert.throws(()=>assertTutorToolSurface(value.tools,additional),/retired/);
 additional=[];assert.equal(guards[0]({name:'skill'}),undefined);
});

test('internal-task delivery filter changes per request and guards stale calls',async()=>{
 const hooks=new Map(),guards=[];let hidden=['mcp__tutor__send_message'];
 installTutorToolSurface({tools:{guard:g=>guards.push(g)},on:(name,fn)=>hooks.set(name,fn)},{additional:()=>hidden});
 const original={tools:['mcp__tutor__send_message','mcp__tutor__workflow_commit','mcp__tutor__service_task_finish'].map(name=>({name})),sections:[]};
 const internal=await hooks.get('system-prompt/assemble')({},null,async()=>original);
 assert.deepEqual(internal.tools.map(x=>x.name),['mcp__tutor__workflow_commit','mcp__tutor__service_task_finish']);
 assert.match(guards[0]({name:'mcp__tutor__send_message'}),/retired/);
 assert.throws(()=>assertTutorToolSurface(original.tools,hidden),/retired/);
 hidden=[];
 const delivery=await hooks.get('system-prompt/assemble')({},null,async()=>original);
 assert.deepEqual(delivery.tools,original.tools);assert.equal(guards[0]({name:'mcp__tutor__send_message'}),undefined);
});
