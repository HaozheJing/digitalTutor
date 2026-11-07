import test from 'node:test';
import assert from 'node:assert/strict';
import {workflowScheduleRules,syncWorkflowSchedules,workflowMode} from './workflow-schedules.mjs';
test('frozen clock never registers wall-clock business timers',()=>{
 assert.deepEqual(workflowScheduleRules({service_workflow_mode:workflowMode,frozen:true},[]),[]);
});
test('real clock has one internal daily wake and stable task IDs only',()=>{
 const due=new Date(Date.now()+3600000).toISOString();
 const rules=workflowScheduleRules({service_workflow_mode:workflowMode,frozen:false},[{id:'service-one',kind:'s01',status:'planned',due_at:due,body:'old prose'}]);
 assert.equal(rules.length,2);assert.equal(rules[0].daily.time,'06:30:00');
 assert.equal(rules[1].prompt,'digitalTutor-workflow:task:service-one');
 assert.equal(rules[1].at,due);assert.ok(!JSON.stringify(rules).includes('old prose'));
});
test('new mode removes old fixed business recurrences and cancelled wakeups',async()=>{
 const removed=[],created=[];
 await syncWorkflowSchedules({list:async()=>[{id:'old',title:'live: S01 今日课前 07:00'},{id:'cancelled',title:'workflow: task service-old'}],delete:async r=>removed.push(r.id),create:async(s,r)=>created.push(r)},'session',{service_workflow_mode:workflowMode,frozen:false},[]);
 assert.deepEqual(removed,['old','cancelled']);assert.equal(created.length,1);assert.equal(created[0].title,'workflow: morning');
});
