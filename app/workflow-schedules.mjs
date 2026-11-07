// Native schedule is a wakeup service, never a business-task factory.
export const workflowMode='agent-workflow-v1';
export function workflowScheduleRules(state,tasks){
 if(state?.service_workflow_mode!==workflowMode||state.frozen)return [];
 return [{title:'workflow: morning',daily:{time:'06:30:00',time_zone:'Asia/Shanghai'},prompt:'digitalTutor-workflow:morning'},
  ...tasks.filter(t=>['planned','deferred'].includes(t.status)&&t.kind!=='plan'&&Date.parse(t.due_at)>Date.now()).map(t=>({
   title:'workflow: task '+t.id,at:t.due_at,prompt:'digitalTutor-workflow:task:'+t.id}))];
}
export async function syncWorkflowSchedules(schedule,sessionId,state,tasks){
 if(state?.service_workflow_mode!==workflowMode)return;
 const response=await schedule.list({sessionId});
 const current=Array.isArray(response)?response:(response.schedules||[]);
 const wanted=workflowScheduleRules(state,tasks);
 for(const old of current){
  const target=wanted.find(r=>r.title===old.title);
  if(old.title?.startsWith('live:')||old.title?.startsWith('active-resume:')||old.title?.startsWith('live-resume:')||
    (old.title?.startsWith('workflow:')&&(!target||(target.at&&Date.parse(old.scheduledAt)!==Date.parse(target.at))))){
   await schedule.delete({sessionId,id:old.id});
  }else if(target)wanted.splice(wanted.indexOf(target),1);
 }
 for(const rule of wanted)await schedule.create(sessionId,rule);
}
