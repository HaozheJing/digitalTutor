import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const html=readFileSync(new URL('../adapters/p0-acceptance.html',import.meta.url),'utf8');
const code=readFileSync(new URL('../adapters/p0-acceptance.js',import.meta.url),'utf8');
function dom(){
 class E{constructor(id=''){this.id=id;this.value='';this.children=[];this.textContent='';}replaceChildren(...x){this.children=x;if(x[0]?.tag==='option'&&!x.some(y=>y.value===this.value))this.value=x[0].value;}append(x){this.children.push(x);}createTHead(){const x=new E();this.children.push(x);return x;}insertRow(){const x=new E();this.children.push(x);return x;}insertCell(){const x=new E();this.children.push(x);return x;}querySelectorAll(){return buttons;}}
 const nodes=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(x=>[x[1],new E(x[1])]));
 const buttons=[...html.matchAll(/<button id="([^"]+)"/g)].map(x=>nodes[x[1]]);
 const ctx=vm.createContext({document:{getElementById:id=>{assert.ok(nodes[id],id);return nodes[id];},createElement:tag=>Object.assign(new E(),{tag})},JSON,setInterval:()=>1,crypto:{randomUUID:()=>''}});
 vm.runInContext(code,ctx);return {nodes,ctx};
}
test('P0 task time uses persisted due_at and resets when switching scenarios',()=>{
 const {nodes,ctx}=dom();
 const state={cases:{cancel:{steps:[]},reschedule:{steps:[]}},current:{case:'cancel',experiment_run_id:'one',running:true},snapshot:{business_time:'2025-10-13T07:00:00+08:00',tasks:[{id:'t1',skill:'remind-reading',status:'planned',due_at:'2025-10-13T07:00:00+08:00'}],parts:[],alerts:{items:[]},receipts:[],messages:[]}};
 ctx.fixture=state;vm.runInContext('state=fixture;render()',ctx);
 const cells=nodes.tasks.children[0].children[1].children.map(x=>x.textContent);
 assert.ok(cells.includes(state.snapshot.tasks[0].due_at));assert.equal(nodes.time.value,'2025-10-13T07:00');
 state.current={case:'reschedule',experiment_run_id:'two',running:true};state.snapshot.business_time='2025-10-22T19:00:00+08:00';
 vm.runInContext('render()',ctx);assert.equal(nodes.time.value,'2025-10-22T19:00');
 assert.equal(nodes['human-controls'].hidden,true);assert.equal(nodes['task-controls'].hidden,false);
});
test('P0 controls disable mutations after scenario stops',()=>{
 const {nodes,ctx}=dom();ctx.fixture={cases:{human:{steps:[]}},current:{case:'human',experiment_run_id:'one',running:false}};
 vm.runInContext('state=fixture;render()',ctx);assert.equal(nodes.send.disabled,true);assert.equal(nodes['human-send'].disabled,true);assert.equal(nodes.create.disabled,false);
});
