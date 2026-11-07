import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {experimentHandler} from './experiment-route.mjs';

test('experiment route rejects absent auth, wrong run and production operations',async()=>{
 const root=mkdtempSync(join(tmpdir(),'native-experiment-'));
 try {
  mkdirSync(join(root,'config'));writeFileSync(join(root,'config','experiment.json'),'{}');
  writeFileSync(join(root,'config','workbench.json'),JSON.stringify({token:'test-key'}));
  const binding={experiment_run_id:'exp-test',run_id:'business',family_uid:'family'};
  const calls=[];
  const handler=experimentHandler({root,busy:()=>false,run:(file,args)=>{calls.push(args);return args[0]==='verify'?{...binding,business_run_id:binding.run_id}:{status:'recorded'};}});
  async function request(url,body,authorization='Bearer test-key',method='POST'){
   const req={url,method,headers:{authorization},async *[Symbol.asyncIterator](){yield JSON.stringify(body);}};
   const res={writeHead(code){this.code=code;},end(body){this.body=JSON.parse(body);}};
   assert.equal(await handler(req,res),true);return res;
  }
  assert.equal((await request('/experiment/v1/messages',binding,'')).code,401);
  assert.equal((await request('/experiment/v1/messages',{...binding,run_id:'other'})).code,409);
  assert.equal((await request('/experiment/v1/messages',binding)).code,200);
  assert.equal((await request('/experiment/v1/arbitrary-command',binding)).code,404);
  assert.equal((await request('/experiment/v1/context',{},'Bearer test-key','GET')).code,200);
  assert.equal(calls.filter(x=>x[0]==='inbound').length,1);
  assert.equal((await request('/experiment/v1/messages',{...binding,audio:{format:'wav',file_base64:'A'.repeat(200000)}})).code,200);
  assert.equal(calls.filter(x=>x[0]==='inbound-stdin').length,1);
  assert.equal((await request('/experiment/v1/messages',{...binding,image:{file_base64:'A'.repeat(200000)}})).code,200);
  assert.equal(calls.filter(x=>x[0]==='inbound-stdin').length,2);
  assert.equal((await request('/experiment/v1/messages',{...binding,audio:{file_base64:'A'.repeat(12000000)}})).code,413);
 }finally{rmSync(root,{recursive:true});}
});
