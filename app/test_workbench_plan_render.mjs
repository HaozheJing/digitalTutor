import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

function page(){
 const nodes=new Map();
 const element=id=>{if(!nodes.has(id))nodes.set(id,{id,value:'',innerHTML:'',textContent:'',dataset:{},classList:{toggle(){}},querySelectorAll:()=>[],setAttribute(){},addEventListener(){},checkValidity:()=>true});return nodes.get(id);};
 const context=vm.createContext({document:{getElementById:element,querySelectorAll:()=>[],addEventListener(){},body:element('body')},sessionStorage:{getItem:()=>'',setItem(){}},location:{hash:'',origin:'https://82.156.162.52'},URL,URLSearchParams,Intl,setInterval(){},setTimeout,clearTimeout,AbortController});
 vm.runInContext(readFileSync(new URL('../adapters/workbench-ui.js',import.meta.url),'utf8'),context);
 return {nodes,run:(code,value)=>{context.input=value;return vm.runInContext(code,context);}};
}
const plan=p=>({source:'model_with_skill_checks',day:'2025-11-07',items:[],model_plans:[p]});
const state={clock:{as_of:'2025-11-07T06:30:00+08:00'},highlights:[],lessons:[]};

test('workflow v4 renders decisions and task references without legacy timestamps',()=>{
 const ui=page();ui.run('data=input',state);
 ui.run('renderPlan(input)',plan({schema_version:4,business_date:'2025-11-07',planned_at:'2026-10-29T14:40:30Z',items:[{decision:'create',skill:'remind-reading',task_id:'service-one'}],decisions:[{decision:'create',skill:'remind-reading',run_at:'2025-11-07T06:30:00+08:00',reason:'当天阅读邀请'},{decision:'skip',skill:'support-zero-completion',reason:'不符合条件'}]}));
 const html=ui.nodes.get('daily-plan').innerHTML;
 for(const text of ['2025-11-07','06:30','当天阅读邀请','不符合条件','跳过'])assert.ok(html.includes(text),text);
 assert.ok(!html.includes('undefined'));assert.ok(!html.includes('14:40'));
});
test('legacy plans retain their own business time and task checks',()=>{
 const ui=page();ui.run('data=input',state);
 ui.run('renderPlan(input)',plan({as_of:'2025-11-07T07:30:00+08:00',reason:'旧计划依据',items:[{due_at:'2025-11-07T19:00:00+08:00',title:'晚间服务',reason:'已核验',checks:'复核学情'}]}));
 const html=ui.nodes.get('daily-plan').innerHTML;for(const text of ['07:30','19:00','旧计划依据','复核学情'])assert.ok(html.includes(text));
});
test('partial saved plans show unavailable fields without inventing times',()=>{
 const ui=page();ui.run('data=input',state);ui.run('renderPlan(input)',plan({items:[{task_id:'service-ref'}]}));
 const html=ui.nodes.get('daily-plan').innerHTML;assert.ok(html.includes('时间未记录'));assert.ok(!html.includes('undefined'));
});
if(process.env.WORKBENCH_STATE_SNAPSHOT)test('full page renders captured live state and preserves receipts',()=>{
 const captured=JSON.parse(readFileSync(process.env.WORKBENCH_STATE_SNAPSHOT,'utf8'));const before=JSON.stringify(captured);const ui=page();
 ui.run('data=input;render();',captured);assert.equal(JSON.stringify(captured),before);assert.ok(ui.nodes.get('daily-plan').innerHTML.includes('remind-reading'));
});

 test('deferral chain groups only matching task and deferral, retaining every raw record',()=>{
  const ui=page();const records=[{id:'a',task_id:'one',name:'send_message',output:{deferral_id:'d',reason:'联系间隔60分钟',retry_at:'20:00',causes:[{}]}},{id:'b',task_id:'one',name:'deliver_proactive',output:{deferral_id:'d'}},{id:'c',task_id:'one',name:'host_service_task_finish',input:{deferral_id:'d'}},{id:'other',task_id:'two',output:{deferral_id:'d'}},{id:'later',task_id:'one',output:{deferral_id:'e'}}];
  const groups=ui.run('groupToolRecords(input)',records);assert.equal(groups.length,3);assert.equal(groups[0].records.length,3);
  const html=ui.run('renderToolRecords(input,new Set())',records);for(const name of ['send_message','deliver_proactive','host_service_task_finish','联系间隔60分钟','不代表执行了 3 次'])assert.ok(html.includes(name),name);
 });
