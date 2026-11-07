import test from 'node:test';
import assert from 'node:assert/strict';
import {Readable} from 'node:stream';
import {workbenchHandler} from './workbench-route.mjs';

async function request({path='clock',token='secret',body={run_id:'r1',as_of:'2025-10-15T20:30'},busy=false,method='POST',headers={}}={}){
 const calls=[];
 const handler=workbenchHandler({root:'.',token:()=> 'secret',state:()=>({run_id:'r1',as_of:'2025-10-15T16:00:00+08:00'}),busy:()=>busy,
  run:()=>({clock:{run_id:'r1'}}),mutate:async(...args)=>{calls.push(args);return {status:'switched'};}});
 const req=Readable.from([JSON.stringify(body)]);
 req.url='/workbench/api/'+path;req.method=method;req.headers={authorization:'Bearer '+token,'content-type':'application/json','x-workbench':'1',...headers};
 const res={setHeader(){},writeHead(status){this.status=status;},end(body){this.value=JSON.parse(body);}};
 assert.equal(await handler(req,res),true);
 return {res,calls};
}
test('authentication precedes reads and writes',async()=>{
 for(const method of ['GET','POST']){const {res,calls}=await request({method,token:'wrong'});assert.equal(res.status,401);assert.equal(calls.length,0);}
});
test('stale tabs and busy model cannot switch or send',async()=>{
 for(const path of ['clock','reset','highlights','automatic','advance','replan']){
  assert.equal((await request({path,busy:true,body:{run_id:'r1',enabled:true}})).res.status,409);
  const {res,calls}=await request({path,body:{run_id:'old',enabled:true}});assert.equal(res.status,409);assert.equal(calls.length,0);
 }
});
test('advance supports later days and rejects time reversal',async()=>{
 for(const as_of of ['2025-10-14T20:00','2025-10-15T15:59','2025-10-15T16:00']){
  const {res,calls}=await request({path:'advance',body:{run_id:'r1',as_of}});assert.equal(res.status,400);assert.equal(calls.length,0);
 }
 assert.equal((await request({path:'advance',body:{run_id:'r1',as_of:'2025-10-16T06:30'}})).res.status,200);
 const {res,calls}=await request({path:'advance'});assert.equal(res.status,200);assert.equal(calls[0][0],'advance');
});
test('invalid and out of window dates cannot mutate',async()=>{
 for(const as_of of ['2025-10-12T20:30','2025-11-17T20:30','2025-10-32T20:30','2025-10-15T24:00',null]){
  const {res,calls}=await request({body:{run_id:'r1',as_of}});assert.equal(res.status,400);assert.equal(calls.length,0);
 }
});
test('authenticated valid date invokes shared mutation exactly once',async()=>{
 const {res,calls}=await request();assert.equal(res.status,200);assert.equal(calls.length,1);
 assert.equal(calls[0][0],'clock');
});
test('browser form submissions cannot mutate',async()=>{
 const {res,calls}=await request({headers:{'x-workbench':undefined}});assert.equal(res.status,403);assert.equal(calls.length,0);
});
test('read state is not a mutation',async()=>{
 const {res,calls}=await request({path:'state',method:'GET'});assert.equal(res.status,200);assert.equal(calls.length,0);
});
test('inspection endpoints require authentication and reject path traversal',async()=>{
 for(const path of ['history?archive='+'a'.repeat(32),'plan?day=2025-10-13']){
  assert.equal((await request({path,method:'GET',token:'wrong'})).res.status,401);
  const {res,calls}=await request({path,method:'GET'});assert.equal(res.status,200);assert.equal(calls.length,0);
 }
 assert.equal((await request({path:'history?archive=../live',method:'GET'})).res.status,400);
});
test('automatic mode requires an explicit boolean',async()=>{
 assert.equal((await request({path:'automatic',body:{run_id:'r1',enabled:'true'}})).res.status,400);
 const {res,calls}=await request({path:'automatic',body:{run_id:'r1',enabled:true}});
 assert.equal(res.status,200);assert.equal(calls.length,1);
});

test('Skill publishing is authenticated and idle-gated without resetting test clock',async()=>{
 const body={version:'a'.repeat(32),expected:'b'.repeat(64),author:'Teacher'};
 assert.equal((await request({path:'skills-publish',body,token:'wrong'})).res.status,401);
 assert.equal((await request({path:'skills-publish',body,busy:true})).res.status,409);
 assert.equal((await request({path:'skills-publish',body:{...body,version:'../secret'}})).res.status,400);
 const {res,calls}=await request({path:'skills-publish',body});assert.equal(res.status,200);
 assert.equal(calls.length,1);assert.equal(calls[0][0],'skills-publish');
});
