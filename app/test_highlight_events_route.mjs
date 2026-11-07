import test from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';import {Readable} from 'node:stream';import {highlightEventsHandler} from './highlight-events-route.mjs';
test('producer authentication, bounded payload, and event acceptance are independent from delivery',async()=>{
 const root=mkdtempSync(join(tmpdir(),'highlight-route-'));mkdirSync(root+'/config');writeFileSync(root+'/config/highlight-events.json',JSON.stringify({enabled:true,token:'a'.repeat(40)}));let calls=0;
 const run=()=>{calls++;return {status:'accepted',delivery_status:'not_confirmed'};};
 async function request(auth,body='{}',method='POST'){
  const req=Readable.from([Buffer.from(body)]);req.url='/workbench/api/highlight-events';req.method=method;req.headers={'authorization':auth,'content-type':'application/json'};
  let code,value;const res={writeHead:c=>code=c,end:t=>value=JSON.parse(t)};await highlightEventsHandler(req,res,{root,run});return {code,value};
 }
 try{
  assert.equal((await request('Bearer wrong')).code,401);assert.equal(calls,0);
  const good=await request('Bearer '+'a'.repeat(40));assert.equal(good.code,202);assert.equal(good.value.delivery_status,'not_confirmed');
  assert.equal((await request('Bearer '+'a'.repeat(40),'x'.repeat(17000))).code,413);assert.equal(calls,1);
  assert.equal((await request('Bearer '+'a'.repeat(40),'{}','DELETE')).code,405);
 }finally{rmSync(root,{recursive:true,force:true});}
});

test('isolated experiments additionally require their exact run and family binding',async()=>{
 const root=mkdtempSync(join(tmpdir(),'highlight-scope-'));mkdirSync(root+'/config');writeFileSync(root+'/config/highlight-events.json',JSON.stringify({enabled:true,token:'a'.repeat(40)}));let calls=0;
 try{for(const scoped of [false,true]){
  const req=Readable.from([Buffer.from('{}')]);req.url='/workbench/api/highlight-events';req.method='POST';req.headers={authorization:'Bearer '+'a'.repeat(40),'content-type':'application/json',...(scoped?{'x-experiment-run-id':'exp','x-business-run-id':'run','x-family-uid':'family'}:{})};let status;
  await highlightEventsHandler(req,{writeHead:x=>status=x,end:()=>{}},{root,run:()=>{calls++;return {status:'accepted'};},experiment:{experiment_run_id:'exp'},state:()=>({run_id:'run',uid:'family'})});assert.equal(status,scoped?202:409);
 }assert.equal(calls,1);}finally{rmSync(root,{recursive:true,force:true});}
});
