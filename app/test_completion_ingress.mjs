import test from 'node:test';
import assert from 'node:assert/strict';
import {Readable} from 'node:stream';
import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {workbenchHandler} from './workbench-route.mjs';
async function req({token='a'.repeat(40),source='course-a',method='POST',body={run_id:'run-1',event:{event_id:'event-1'}},busy=false,path='course-completion',enabled=true}={}){
 const root=mkdtempSync(tmpdir()+'/completion-api-');mkdirSync(root+'/config');
 writeFileSync(root+'/config/completion-ingress.json',JSON.stringify({enabled,sources:[{id:'course-a',enabled:true,adapter:'normalized-v1',token:'a'.repeat(40)}]}));
 const calls=[];const h=workbenchHandler({root,token:()=> 'workbench-secret',state:()=>({run_id:'run-1'}),busy:()=>busy,run:(...args)=>{calls.push(['read',...args]);return {cases:[]};},mutate:async(...args)=>{calls.push(args);return {status:'recorded'};}});
 const input=Readable.from([JSON.stringify(body)]);input.url='/workbench/api/'+path;input.method=method;input.headers={authorization:'Bearer '+token,'x-course-source':source,'content-type':'application/json',...(path==='service-ledger'?{'x-workbench':'1'}:{})};
 const res={setHeader(){},writeHead(code){this.code=code;},end(body){this.body=JSON.parse(body);}};
 try{await h(input,res);return {res,calls};}finally{rmSync(root,{recursive:true});}
}
test('machine POST uses configured source identity, never caller trusted_source',async()=>{
 const {res,calls}=await req({body:{run_id:'run-1',event:{event_id:'one'},trusted_source:'workbench'}});
 assert.equal(res.code,200);assert.equal(calls[0][0],'course-completion');assert.equal(calls[0][1].trusted_source,'course-a');
});
test('machine auth rejects unknown source, wrong token and disabled config',async()=>{
 for(const options of [{token:'workbench-secret'},{source:'unknown'}]){const {res,calls}=await req(options);assert.equal(res.code,401);assert.equal(calls.length,0);}
 assert.equal((await req({enabled:false})).res.code,503);
});
test('machine requires POST and retains busy and run conflict retry boundaries',async()=>{
 assert.equal((await req({method:'GET'})).res.code,404);
 assert.equal((await req({busy:true})).res.code,409);
 assert.equal((await req({body:{run_id:'old',event:{}}})).res.code,409);
});
test('ledger POST is authenticated read-only, supports history and busy viewing',async()=>{
 const {res,calls}=await req({path:'service-ledger',token:'workbench-secret',busy:true,body:{ledger_run_id:'b'.repeat(32)}});
 assert.equal(res.code,200);assert.equal(calls[0][0],'read');assert.equal(calls[0][1],'service_ledger.py');
 assert.equal((await req({path:'service-ledger',token:'workbench-secret',body:{ledger_run_id:'../live'}})).res.code,400);
});
