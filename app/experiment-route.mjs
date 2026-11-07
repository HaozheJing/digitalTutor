import {timingSafeEqual} from 'node:crypto';
import {existsSync,readFileSync} from 'node:fs';

// Only mounted by an isolated, frozen experiment workspace. Existing production
// routes and business actions retain their original permissions and semantics.
export function experimentHandler({root,run,busy,compact}) {
 return async(req,res)=>{
  if(!req.url?.startsWith('/experiment/v1/'))return false;
  const send=(code,value)=>{res.writeHead(code,{'Content-Type':'application/json'});res.end(JSON.stringify(value));return true;};
  const path=root+'/config/experiment.json';
  if(!existsSync(path))return send(404,{error:'experiment_not_enabled'});
  const expected=JSON.parse(readFileSync(root+'/config/workbench.json')).token;
  const a=Buffer.from(req.headers.authorization||''),b=Buffer.from('Bearer '+expected);
  if(!expected||a.length!==b.length||!timingSafeEqual(a,b))return send(401,{error:'unauthorized'});
  try {
   const manifest=run('experiment_runtime.py',['verify']);
   if(req.method==='GET'&&req.url==='/experiment/v1/context')return send(200,run('experiment_runtime.py',['snapshot']));
   if(req.method!=='POST')return send(405,{error:'method_not_allowed'});
   const limit=req.url==='/experiment/v1/messages'?12000000:65536;
   let raw='';for await(const part of req){raw+=part;if(Buffer.byteLength(raw)>limit)return send(413,{error:'input_too_large'});}
   const input=JSON.parse(raw||'{}');
   if(input.experiment_run_id!==manifest.experiment_run_id||input.run_id!==manifest.business_run_id||input.family_uid!==manifest.family_uid)return send(409,{error:'experiment_scope_mismatch'});
   if(req.url==='/experiment/v1/messages')return send(200,(input.audio||input.image)?run('experiment_runtime.py',['inbound-stdin'],raw):run('experiment_runtime.py',['inbound',JSON.stringify(input)]));
   if(busy())return send(409,{error:'agent_busy'});
   if(req.url==='/experiment/v1/compact'&&compact)return send(200,await compact());
   if(req.url==='/experiment/v1/advance')return send(200,run('test_mode.py',['advance',input.as_of]));
   if(req.url==='/experiment/v1/completion')return send(200,run('completion_events.py',['simulate',JSON.stringify({run_id:input.run_id,event_id:input.request_id,lesson_id:input.lesson_id,completed_at:input.completed_at})]));
   if(req.url==='/experiment/v1/plan'&&manifest.mode!=='passive')return send(200,run('agent_plan.py',['request','plan']));
   if(req.url==='/experiment/v1/execute')return send(200,run('console_control.py',['execute',input.task_id]));
   return send(404,{error:'unknown_experiment_operation'});
  }catch(error){return send(409,{error:'experiment_request_rejected',detail:String(error)});}
 };
}
