import test from 'node:test';
import assert from 'node:assert/strict';
import {installServiceTurnCompletion} from './service-turn-completion.mjs';

for(const name of ['reply_to_message','reply_with_highlights','deliver_message','deliver_proactive','workflow_fulfill','workflow_highlight','deliver_festival','send_image']){
 test(`${name}: trusted fence receipt concludes native generation`,async()=>{
  let middleware,concluded=0;
  const ctx={on:(event,fn)=>{if(event==='tools/execute')middleware=fn;}};
  installServiceTurnCompletion(ctx,new Map());
  const result={isError:false,value:{content:[{type:'text',text:JSON.stringify({status:'replan_required',blocked_by:'message_hooks'})}]}};
  const returned=await middleware({name:'mcp__tutor__'+name,concludeTurn:()=>concluded++},async()=>result);
  assert.equal(concluded,1);assert.deepEqual(returned,result);
 });
}
test('errors and ordinary results do not conclude a turn',async()=>{
 let middleware,concluded=0;
 installServiceTurnCompletion({on:(event,fn)=>{if(event==='tools/execute')middleware=fn;}},new Map());
 for(const result of [{isError:true,value:{content:[{type:'text',text:'{}'}]}},{isError:false,value:{content:[{type:'text',text:'{"status":"accepted"}'}]}}])
  await middleware({name:'mcp__tutor__reply_to_message',concludeTurn:()=>concluded++},async()=>result);
 assert.equal(concluded,0);
});
