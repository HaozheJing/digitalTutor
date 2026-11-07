import {existsSync,readFileSync} from 'node:fs';
import {timingSafeEqual} from 'node:crypto';
export async function highlightEventsHandler(req,res,{root,run,experiment=null,state=()=>null}){
 const url=new URL(req.url,'http://localhost');
 if(url.pathname!=='/workbench/api/highlight-events')return false;
 const reply=(code,value)=>{res.writeHead(code,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
 const file=root+'/config/highlight-events.json';
 const cfg=existsSync(file)?JSON.parse(readFileSync(file,'utf8')):{};
 if(!cfg.enabled){reply(503,{error:'highlight_ingress_disabled'});return true;}
 const provided=Buffer.from(req.headers.authorization||''),expected=Buffer.from('Bearer '+(cfg.token||''));
 if(typeof cfg.token!=='string'||cfg.token.length<32||provided.length!==expected.length||!timingSafeEqual(provided,expected)){reply(401,{error:'unauthorized'});return true;}
 if(experiment&&(req.headers['x-experiment-run-id']!==experiment.experiment_run_id||req.headers['x-business-run-id']!==state()?.run_id||req.headers['x-family-uid']!==state()?.uid)){reply(409,{error:'experiment_scope_mismatch'});return true;}
 if(!['POST','GET'].includes(req.method)){reply(405,{error:'method_not_allowed'});return true;}
 try{
  let value;
  if(req.method==='GET'){
   const id=url.searchParams.get('event_id');if(!id||id.length>128){reply(400,{error:'event_id_required'});return true;}
   value=run('highlight_events.py',['status',id]);
  }else{
   if(!(req.headers['content-type']||'').startsWith('application/json')){reply(415,{error:'application_json_required'});return true;}
   let size=0,chunks=[];for await(const chunk of req){size+=Buffer.byteLength(chunk);if(size>16384){reply(413,{error:'event_too_large'});return true;}chunks.push(Buffer.from(chunk));}
   const parsed=JSON.parse(Buffer.concat(chunks).toString('utf8'));value=run('highlight_events.py',['ingest',JSON.stringify(parsed)]);
  }
  reply(value.http_status||(value.status==='not_found'?404:req.method==='POST'?202:200),value);
 }catch{reply(400,{error:'invalid_highlight_request'});}
 return true;
}
