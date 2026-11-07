import {createServer} from 'node:http';
import {installWorkingSurface} from './working-surface.mjs';
import {checkRequestBudget} from './request-budget.mjs';
import {installTutorToolSurface,assertTutorToolSurface} from './tutor-tool-surface.mjs';
import {workspaceOwner} from './workspace-owner.mjs';
import {execFileSync} from 'node:child_process';
import {readFileSync,writeFileSync,appendFileSync,existsSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {workbenchHandler} from './workbench-route.mjs';
import {highlightEventsHandler} from './highlight-events-route.mjs';
import {learningEventsAdapter} from './learning-events.mjs';
import {syncWorkflowSchedules,workflowMode} from './workflow-schedules.mjs';
import {installServiceTurnCompletion} from './service-turn-completion.mjs';
import {experimentHandler} from './experiment-route.mjs';
import {memoryPresetId,memoryPreset,installMemoryTurnCompletion} from './memory-runtime.mjs';
export function adapterOutputLimit(file,args=[]){return file==='workbench.py'||(file==='experiment_runtime.py'&&args[0]==='snapshot')?32*1024*1024:1024*1024;}
export const name='tutor-live';
export const inject=['sessionController','systemPrompt','schedule','agentPresets','tokenMeter','agents','commands','tools'];
const root=fileURLToPath(new URL('..',import.meta.url)).replace(/\/$/,'');
export function apply(ctx){
 const run=(file,args,input)=>JSON.parse(execFileSync('python3',[root+'/adapters/'+file,...args],{input,encoding:'utf8',maxBuffer:adapterOutputLimit(file,args),timeout:file==='experiment_dialogue_context.py'?300000:(file==='operator_alerts.py'&&args[0]==='reply')||(file==='wechat_test.py'&&args[0]==='flush')?75000:30000}));
 const testState=()=>run('test_mode.py',['state']);
 const experiment=run('experiment_runtime.py',['verify']);
 const manual=run('manual_workbench.py',['verify']),familyLab=run('family_lab_bridge.py',['verify']),governed=Boolean(experiment||manual||familyLab);
 const extraRetired=()=>{const value=run('capability_surface.py',['retired']);return [...value.native,...value.tools];};
 if(governed)installTutorToolSurface(ctx,{additional:extraRetired});
 const memoryEnabled=existsSync(root+'/config/preference-memory.json')&&JSON.parse(readFileSync(root+'/config/preference-memory.json','utf8')).enabled===true;
 if(governed)ctx.systemPrompt.context({name:'tool-result-semantics',order:510,required:true,interpolate:false,text:()=>run('tool_result_contract.py',[]).guidance});
 const allowPlanning=()=>!experiment||experiment.mode!=='passive';
 const acceptLearningEvent=learningEventsAdapter({run});
 const day=()=>testState()?.as_of.slice(0,10)||new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const live=(cmd,...args)=>run('live_runtime.py',[cmd,...args]);
 const gate=(mode='read')=>run('sql_tools.py',['gate','--mode',mode]);
 ctx.systemPrompt.context({name:'tutor-live-context',order:500,required:true,interpolate:false,text:()=>JSON.stringify(run('task_context.py',['runtime']))});
 const state=root+'/runtime/service-session.json';const saved=existsSync(state)?JSON.parse(readFileSync(state)):{};let sessionId=saved.sessionId||null;
 if(testState()&&saved.testRunId!==testState().run_id)sessionId=null;
 const bindFamily=id=>{if(governed&&existsSync(root+'/config/tool-surface.json')&&JSON.parse(readFileSync(root+'/config/tool-surface.json')).identity===true)run('family_session.py',[id]);return id;};
 let creating;const ensure=()=>sessionId?Promise.resolve(bindFamily(sessionId)):(creating??=ctx.sessionController.create({cwd:root,agentPreset:'standard'}).then(s=>{sessionId=s.sessionId;writeFileSync(state,JSON.stringify({...s,testRunId:testState()?.run_id}));return bindFamily(sessionId);}).finally(()=>creating=null));
 const passive=m=>m.source?.kind==='user'&&!String(m.source?.rpcId||'').startsWith('lab-run-');
 const requests=new Map(),serviceRequests=new Map(),passiveRequests=new Map(),memoryRequests=new Map();let generation=0,draining=false,ready=false,lastError=null,lastPoll=null,modelBusy=0;
 const surface=governed?installWorkingSurface(ctx,{root,serviceRequests,passiveRequests,checkpoint:(taskId,requestId)=>run('context_lifecycle.py',[taskId,requestId])}):{clean(){}};
 ctx.on('llm/stream',async function*(options,next){if(governed)assertTutorToolSurface(options.tools,extraRetired());checkRequestBudget(options);yield* next();});
 const memoryCompletion=installMemoryTurnCompletion(ctx,memoryRequests,(eventId,token)=>run('memory_session.py',['completion',eventId,token]));
 installServiceTurnCompletion(ctx,serviceRequests,(owner,name,key,receipt)=>
  run('host_receipt.py',[owner.taskId,owner.requestId,name,key||'',...(receipt?[JSON.stringify(receipt)]:[])]));
 ctx.on('agent/inbox/inserted',({agent,message})=>{
  modelBusy++;setTimeout(()=>agent.whenIdle().catch(()=>{}).finally(()=>modelBusy--),0);
  const eventId=requests.get(message.source?.rpcId);const isPassive=passive(message);const own=isPassive?++generation:generation;
  const serviceId=serviceRequests.get(message.source?.rpcId);
  if(serviceId)setTimeout(()=>agent.whenIdle().then(()=>{run('agent_plan.py',['settle',serviceId]);surface.clean(agent,serviceId);serviceRequests.delete(message.source?.rpcId);}).catch(()=>{lastError='service_completion_failed';}).finally(()=>{try{if(governed)run('skill_capabilities.py',['release',message.source.rpcId]);}catch{lastError='tool_scope_release_failed';}}),0);
  if(isPassive){if(memoryEnabled){run('preference_memory.py',['pause']);memoryCompletion.interrupt();}gate('start');}
  if(isPassive||eventId)setTimeout(()=>agent.whenIdle().then(()=>{if(eventId){if(governed)run('host_tool_steps.py',['finish-event',eventId]);if(passiveRequests.has(message.source?.rpcId)){surface.clean(agent,'passive:'+eventId);passiveRequests.delete(message.source?.rpcId);}live('retry',eventId);requests.delete(message.source?.rpcId);memoryRequests.delete(message.source?.rpcId);}if(isPassive&&own===generation)gate('end');}).catch(()=>{lastError='idle_completion_failed';}),0);
 });
 ctx.on('agent/pre-step',async({messages},next)=>{
  if(experiment)run('experiment_runtime.py',['verify']);if(manual)run('manual_workbench.py',['verify']);
  if(memoryEnabled&&messages.some(passive))run('preference_memory.py',['pause']);
  if(messages.some(m=>m.source?.kind==='schedule')&&!run('service_hours.py',[]).open){
   const remaining=messages.filter(m=>m.source?.kind!=='schedule');
   return remaining.length?{kind:'enter',messages:remaining}:{kind:'reject'};
  }
  if(testState()?.service_workflow_mode===workflowMode&&messages.some(m=>m.source?.kind==='schedule')){
   run('service_workflow.py',['wake']);
   const remaining=messages.filter(m=>m.source?.kind!=='schedule');
   return remaining.length?{kind:'enter',messages:remaining}:{kind:'reject'};
  }
  if(testState()&&messages.some(m=>m.source?.kind==='schedule')){
   const remaining=messages.filter(m=>m.source?.kind!=='schedule');
   return remaining.length?{kind:'enter',messages:remaining}:{kind:'reject'};
  }
  const scheduled=messages.filter(m=>m.source?.kind==='schedule'),g=gate();
  const pendingChat=scheduled.length&&live('status').events.some(e=>e.kind==='passive'&&['pending','dispatched'].includes(e.status));
  if(scheduled.length&&(messages.some(passive)||g.active||g.until>Date.now()/1000||pendingChat)){
   await ctx.schedule.create(await ensure(),{title:'live: 家长交流后复查',prompt:scheduled.map(m=>m.content.filter(x=>x.type==='text').map(x=>x.text).join('\n')).join('\n'),after_seconds:Math.max(121,Math.ceil((g.until||0)-Date.now()/1000)+1)});
   const remaining=messages.filter(m=>!scheduled.includes(m));return remaining.length?{kind:'enter',messages:remaining}:{kind:'reject'};
  }
  if(scheduled.length){
   const hook=run('message_hooks.py',['begin','schedule',String(scheduled[0].source?.rpcId||'native-schedule')]);
   if(hook.ready===false){
    await ctx.schedule.create(await ensure(),{title:'live: 消息静默后重新核验',prompt:scheduled.map(m=>m.content.filter(x=>x.type==='text').map(x=>x.text).join('\n')).join('\n'),after_seconds:Math.max(1,Math.ceil(hook.quiet_until-Date.now()/1000))});
    const remaining=messages.filter(m=>!scheduled.includes(m));return remaining.length?{kind:'enter',messages:remaining}:{kind:'reject'};
   }
   if(hook.prompt)return {kind:'enter',messages:messages.map(m=>scheduled.includes(m)?{...m,content:[...m.content,{type:'text',text:hook.prompt}]}:m)};
  }
  return next();
 });
 let workspaceLease;
 const bootstrap=async()=>{
  if(governed){workspaceLease=await workspaceOwner(root,()=>{ready=false;lastError='workspace_ownership_lost';});ctx.on('dispose',()=>workspaceLease.close());}
  if(memoryEnabled){const dispose=await ctx.agentPresets.register(memoryPreset(root));ctx.on('dispose',dispose);}
  live('recover');run('proactive.py',['recover']);run('agent_plan.py',['recover']);if(governed)run('skill_capabilities.py',['reset']);const id=await ensure();
  const list=await ctx.schedule.list({sessionId:id});
  const rows=Array.isArray(list)?list:(list.schedules||[]);
  if(!testState())for(const old of rows.filter(r=>['live: 每日规划 07:30','live: S01 今日完课 18:00','live: S01 今日课前 07:00'].includes(r.title)))await ctx.schedule.delete({sessionId:id,id:old.id});
  for(const rule of [
   {title:'live: 每日规划 06:30',daily:{time:'06:30:00',time_zone:'Asia/Shanghai'},prompt:'你是这个家庭的阅读辅导老师。请使用tutor-main与plan-today，结合当前家庭、课程、对话、偏好与已有安排，检查启用的非周期动作，选择今天必要的事项并合理排期。说明为何做、为何选这个时间，保存实际回执。固定周期任务不重复安排；没有适用事项保存空计划。这是内部规划，无需向家长汇报。'},
   {title:'live: S01 今日课前 06:30',daily:{time:'06:30:00',time_zone:'Asia/Shanghai'},prompt:'现在是今日阅读邀请时间。使用当前remind-reading与reply-style，结合已核实的今日课程和近期家庭安排组织邀请；不把尚未核验的完课状态写成孩子还没完成。由交付接口即时复核完课、暂停、当前交流和重复发送。只有确认送达才记录发送完成。'},
   {title:'live: 周五补读 20:00',daily:{time:'20:00:00',time_zone:'Asia/Shanghai'},prompt:'仅周五检查plan-weekend-makeup。使用当前S09与reply-style，读取本周实际学情和家庭安排，满足本周至少一节未完课时邀请补读；check_proactive通过才交付。不重复已有回执，不发送不存在的入口。'},
   {title:'live: 周末补读与周报 09:00',daily:{time:'09:00:00',time_zone:'Asia/Shanghai'},prompt:'使用当前tutor-main与proactive-service。仅周六检查weekly-learning-review，其它日期不联系。读取daily_context/action_candidates，依实际候选与最新学情、已有交付决定；加载对应Skill和reply-style，check_proactive通过才交付，不补发重复内容。'},
   {title:'live: 日历关怀检查 09:30',daily:{time:'09:30:00',time_zone:'Asia/Shanghai'},prompt:'你是这个家庭的阅读辅导老师。使用tutor-main与proactive-service，读取当前daily_context与action_candidates，只评估生日和家庭适用的节日/考试关怀。已有等价排期时沿用，不重复执行；到点且有明确依据才加载动作Skill和reply-style，凝练一条关怀消息并通过deliver_proactive交付。没有匹配信号就不联系。'},
   {title:'live: 学习与周月节点检查 19:00',daily:{time:'19:00:00',time_zone:'Asia/Shanghai'},prompt:'你是这个家庭的阅读辅导老师。使用tutor-main与proactive-service，读取当前daily_context与action_candidates，检查白天新产生的完课、首课、连续学习、书目/答题/表达成就及今日适用的周月回顾、补读和已确认节点。周几及月末由条件工具判断。尊重已排期和已交付，优先最有价值的事项；相关事项可合并，最多三个。加载相应动作Skill及reply-style后通过deliver_proactive交付一条消息。未知、不适用、额度用完或重复时不联系，不向家长汇报内部检查。'}
  ])if(!testState()&&!rows.some(r=>r.title===rule.title))await ctx.schedule.create(id,rule);
  if(testState()?.service_workflow_mode===workflowMode)await syncWorkflowSchedules(ctx.schedule,id,testState(),run('service_workflow.py',['scheduled']));
  ready=true;
 };
 const tick=async()=>{if(draining||!ready)return;draining=true;try{
  if(familyLab){const control=run('family_lab_bridge.py',['tick',String(Boolean(modelBusy))]);if(!control.dispatch)return;}
  if(experiment)run('experiment_runtime.py',['verify']);if(manual)run('manual_workbench.py',['verify']);
  run('service_hours.py',[]);
  if(testState()){
   if(testState()?.service_workflow_mode===workflowMode&&!modelBusy)run('service_workflow.py',['wall-clock']);
   run('wechat_test.py',['flush','1']);
   run('operator_alerts.py',['reconcile']);
   const control=run('wechat_test.py',['receive']);
   if(control?.callback_id){
    if(!modelBusy&&!live('status').events.some(e=>['pending','dispatched'].includes(e.status))){
     const result=run('wechat_test.py',['control',JSON.stringify(control)]);
     if(result.changed){
      writeFileSync(root+'/runtime/session-before-'+result.test_mode.run_id+'.json',JSON.stringify({sessionId}));
      sessionId=null;await ensure();
     }
     return;
    }
   }
   lastPoll=control||{status:'listening_test_wechat'};
   if(!modelBusy&&!live('status').events.some(e=>['pending','dispatched'].includes(e.status))){
    run('weekly_materials.py',['tick']);
    run('host_highlights.py',['tick']);
    run('wechat_test.py',['flush','1']);
    if(testState()?.agent_planning&&allowPlanning())run('agent_plan.py',['sync']);
    if(testState()?.service_workflow_mode===workflowMode)await syncWorkflowSchedules(ctx.schedule,await ensure(),testState(),run('service_workflow.py',['scheduled']));
   }
  }else lastPoll=live('ingest');
  if(memoryEnabled)run('preference_memory.py',['tick']);
  lastError=null;
  const g=gate();if(!g.active&&!(g.until>Date.now()/1000)){
   if(!testState()){
   for(const row of run('proactive.py',['pending'])){
    const title='active-resume:'+row.id;
    const schedules=await ctx.schedule.list({sessionId:await ensure()});
    if(!schedules.some(s=>s.title===title))await ctx.schedule.create(await ensure(),{title,prompt:run('proactive.py',['task',row.candidate_ids]).prompt,after_seconds:2});
    run('proactive.py',['scheduled',row.id]);
   }
   for(const row of live('pending-s01')){
    const payload=JSON.parse(row.payload);if(payload.not_before>Date.now()/1000)continue;
    const businessDay=payload.day;
    if(businessDay===day()){
     const title='live-resume:'+row.id;
     const schedules=await ctx.schedule.list({sessionId:await ensure()});
     if(!schedules.some(s=>s.title===title))await ctx.schedule.create(await ensure(),{title,prompt:`加载 remind-reading，当前上海日期为 ${businessDay}，复查今日S01，需要联系时使用reply-style检查表达后交付；完成或未知不发。`,after_seconds:2});
    }
    live('scheduled-s01',row.id);
   }
   }
  }
  // A workspace is one family actor. Inbound is ingested above, but never executes alongside another turn.
  if(governed&&modelBusy)return;
  const event=live('claim',memoryEnabled&&modelBusy?'passive':'all');if(event){
   if(memoryEnabled&&event.kind==='passive')run('preference_memory.py',['pause']);
   if(event.kind==='passive'&&testState()?.service_workflow_mode===workflowMode)run('service_workflow.py',['dialogue']);
   const requestId=(event.kind==='passive'?'live-inbound-':'lab-run-')+randomUUID();requests.set(requestId,event.id);
   let hook;try{hook=event.kind==='memory_consolidation'?{prompt:''}:run('message_hooks.py',['begin','event',event.id]);}
   catch(error){requests.delete(requestId);live('retry',event.id);throw error;}
   if(hook.ready===false){requests.delete(requestId);live('retry',event.id);return;}
   try{
    const packet=event.kind==='memory_consolidation'?run('memory_session.py',['launch',event.id]):(governed?run('experiment_dialogue_context.py',[event.id]):run('business.py',['task',event.id]));
    if(packet.completed){requests.delete(requestId);return;}
    let target;
    if(event.kind==='memory_consolidation'){
     const created=await ctx.sessionController.create({cwd:root,agentPreset:memoryPresetId});target=created.sessionId;
     memoryRequests.set(requestId,event.id);
     appendFileSync(root+'/runtime/memory-sessions.jsonl',JSON.stringify({eventId:event.id,requestId,sessionId:target,testRunId:testState()?.run_id})+'\n');
    }else target=await ensure();
    if(event.kind==='passive'&&governed){surface.prepare?.(ctx.agents.get(target));passiveRequests.set(requestId,event.id);}
    await ctx.sessionController.prompt({sessionId:target,requestId,mode:event.kind==='passive'?'steer':'queue',content:[{type:'text',text:packet.prompt+(hook.prompt||'')}],clientTimeZone:'Asia/Shanghai'},new AbortController().signal);
    if(testState())run('wechat_test.py',['flush','1']);
   }catch(e){requests.delete(requestId);passiveRequests.delete(requestId);memoryRequests.delete(requestId);live('retry',event.id);throw e;}
  }else if(!modelBusy&&testState()?.agent_planning&&allowPlanning()){
   const task=run('agent_plan.py',['claim']);
   if(task){
     if(familyLab&&run('family_lab_bridge.py',['parent-task',JSON.stringify(task)]))return;
    if(task.kind==='highlight'){run('host_highlights.py',['execute',task.id]);return;}
    const requestId='lab-run-service-'+randomUUID();serviceRequests.set(requestId,task.id);
    let hook;try{hook=run('message_hooks.py',['begin','task',task.id]);}
    catch(error){serviceRequests.delete(requestId);run('message_hooks.py',['defer',task.id]);throw error;}
    if(hook.ready===false){serviceRequests.delete(requestId);run('message_hooks.py',['defer',task.id]);return;}
    run('agent_plan.py',['dispatched',task.id,requestId]);
    try{if(governed)run('skill_capabilities.py',['bind',task.id,requestId]);const target=await ensure();surface.prepare?.(ctx.agents.get(target));
     await ctx.sessionController.prompt({sessionId:target,requestId,mode:'queue',
     content:[{type:'text',text:run('agent_plan.py',['task',task.id]).prompt+(hook.prompt||'')}],clientTimeZone:'Asia/Shanghai'},new AbortController().signal);}
    catch(e){serviceRequests.delete(requestId);if(governed)run('skill_capabilities.py',['release',requestId]);run('agent_plan.py',['settle',task.id]);throw e;}
   }
  }
 }catch(e){lastError=e.name+': polling_or_dispatch_failed';}finally{draining=false;}};
 const timer=setInterval(tick,5000);
 bootstrap().then(tick).catch(e=>{lastError=e.name+': bootstrap_failed';});
 const isBusy=()=>!ready||draining||modelBusy>0||live('status').events.some(e=>['pending','dispatched'].includes(e.status));
 const workbench=workbenchHandler({root,run,state:testState,busy:isBusy,
  token:()=>{const p=root+'/config/workbench.json';return existsSync(p)?JSON.parse(readFileSync(p)).token:null;},
  mutate:async(action,input)=>{
   // Stopping future execution is allowed while another operation owns the gate.
   // It must not release that operation's lock while a new session is being created.
   if(action==='automatic'){if(manual&&input.enabled)return {status:'rejected',error:'此工作台仅供手动检验，不能开启自动发送'};return run('test_scheduler.py',['automatic',JSON.stringify(input.enabled)]);}
   draining=true;
   try{
    if(action==='alert-reply')return run('operator_alerts.py',['reply',JSON.stringify({expected_run_id:input.run_id,alert_id:input.alert_id,body:input.body,revision:input.revision,request_id:input.request_id})]);
    if(action==='alert-resolve')return run('operator_alerts.py',['dismiss',JSON.stringify({expected_run_id:input.run_id,alert_id:input.alert_id,revision:input.revision,reason:input.reason})]);
    if(action==='course-completion')return run('completion_events.py',['external',JSON.stringify(input)]);
    if(action==='simulate-completion'||action==='completion-events')return acceptLearningEvent(action,input);
    if(action==='skills-publish')return run('skill_studio.py',['apply',JSON.stringify(input)]);
    if(action==='highlights')return run('host_highlights.py',['queue']);
    if(action==='replan')return run('agent_plan.py',['request','plan']);
    if(action==='advance')return run('clock_execution.py',['advance',input.as_of+':00+08:00']);
    if(action==='execute')return run('console_control.py',['execute',input.task_id]);
    if(action==='clear-context'){
     const result=run('console_control.py',['clear-context']);
     if(result.status==='rejected')return result;
     writeFileSync(root+'/runtime/session-before-'+result.archive_id+'.json',JSON.stringify({sessionId,testRunId:testState().run_id}));
     sessionId=null;await ensure();return result;
    }
    const result=run('clock_execution.py',['activate',action==='reset'?testState().as_of:input.as_of+':00+08:00']);
    if(result.status==='rejected')return result;
    writeFileSync(root+'/runtime/session-before-'+result.run_id+'.json',JSON.stringify({sessionId}));
    sessionId=null;await ensure();
    return {status:'switched',as_of:result.as_of,run_id:result.run_id};
   }finally{draining=false;}
  }});
 const experimentRoute=experimentHandler({root,run,busy:isBusy,compact:async()=>{modelBusy++;try{const agent=ctx.agents.get(await ensure());if(!agent)throw Error('family_agent_unavailable');const result=await ctx.commands.execute(agent,'/compact',[],AbortSignal.timeout(120000));if(!result)throw Error('compact_command_unavailable');return {status:result.result?.kind==='error'?'failed':'completed',...result};}finally{modelBusy--;}}});
 const server=createServer(async(req,res)=>{try{
  if(await experimentRoute(req,res))return;
  if(await highlightEventsHandler(req,res,{root,run,experiment,state:testState}))return;
  if(experiment&&req.method==='POST'){res.writeHead(403);res.end(JSON.stringify({error:'use_scoped_experiment_api'}));return;}
  if(await workbench(req,res))return;
  res.setHeader('Content-Type','application/json');
  if(req.method==='GET'&&req.url==='/health'){res.end(JSON.stringify({ok:ready&&!lastError,sessionId,lastError,lastPoll,testMode:testState(),...live('status'),...(experiment?{simulation:true,experiment_run_id:experiment.experiment_run_id,delivery_mode:'isolated_simulation'}:manual?{simulation:false,...manual}:{})}));return;}
  if(req.method!=='POST'){res.writeHead(404);res.end();return;}
  let raw='';for await(const chunk of req){raw+=chunk;if(raw.length>20000)throw Error('input too large');}const input=JSON.parse(raw||'{}');let result;
  const id=await ensure();
  if(req.url==='/test-clock'){
   if(draining||modelBusy||live('status').events.some(e=>['pending','dispatched'].includes(e.status))){res.writeHead(409);res.end(JSON.stringify({error:'agent_busy'}));return;}
   draining=true;
   try{
    result=run('test_mode.py',['activate',input.as_of]);
    if(result.status!=='rejected'){
     writeFileSync(root+'/runtime/session-before-'+result.run_id+'.json',JSON.stringify({sessionId}));sessionId=null;await ensure();
    }
   }
   finally{draining=false;}
  }
  else if(req.url==='/run'){
   const requestId='lab-run-'+randomUUID(),hook=run('message_hooks.py',['begin','manual',requestId]);
   if(hook.ready===false){res.writeHead(409);res.end(JSON.stringify({error:'inbound_quiet_window'}));return;}
   result=await ctx.sessionController.prompt({sessionId:id,requestId,mode:'queue',content:[{type:'text',text:input.prompt+(hook.prompt||'')}],clientTimeZone:'Asia/Shanghai'},new AbortController().signal);
  }
  else if(req.url==='/schedules')result=await ctx.schedule.list({sessionId:id});
  else if(req.url==='/schedule')result=await ctx.schedule.create(id,input);
  else if(req.url==='/schedule-delete')result=await ctx.schedule.delete({sessionId:id,id:input.id});
  else{res.writeHead(404);res.end();return;}
  res.end(JSON.stringify(result));
 }catch(e){res.writeHead(500);res.end(JSON.stringify({error:String(e)}));}});
 const workbenchPort=Number(process.env.TUTOR_WORKBENCH_PORT||19032);
 if(!Number.isInteger(workbenchPort)||workbenchPort<1024||workbenchPort>65535)throw Error('Invalid workbench port');
 server.listen(workbenchPort,'127.0.0.1');ctx.on('dispose',()=>{clearInterval(timer);server.close();});
}
