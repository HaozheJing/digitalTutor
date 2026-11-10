import {timingSafeEqual} from 'node:crypto';
import {readFileSync} from 'node:fs';

// Only these routes are exposed by the reverse proxy. Other Harness APIs stay local.
export function workbenchHandler({root, run, busy, mutate, state, token}) {
 return async (req,res) => {
  if (!req.url.startsWith('/workbench/')) return false;
  const send=(status,value,type='application/json; charset=utf-8')=>{
   res.writeHead(status,{'Content-Type':type,'Cache-Control':'no-store',
    'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer'});
   res.end(type.startsWith('application/json')?JSON.stringify(value):value);
  };
  try {
   const pages={'/workbench/':['workbench.html','text/html; charset=utf-8'],
    '/workbench/app.js':['workbench-ui.js','text/javascript; charset=utf-8'],
    '/workbench/style.css':['workbench.css','text/css; charset=utf-8']};
   if(req.method==='GET'&&pages[req.url]){
    const [file,type]=pages[req.url];
    res.setHeader('Content-Security-Policy',"default-src 'self'; connect-src 'self'; img-src 'self' https://coolcdn.igetcool.com; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");
    send(200,readFileSync(root+'/adapters/'+file),type);return true;
   }
   const machine=req.url==='/workbench/api/course-completion';
   let source=null, wanted;
   if(machine){
    let config;try{config=JSON.parse(readFileSync(root+'/config/completion-ingress.json','utf8'));}catch{send(503,{error:'completion_ingress_not_configured'});return true;}
    if(config.enabled!==true){send(503,{error:'completion_ingress_disabled'});return true;}
    const entry=(config.sources||[]).find(x=>x.id===req.headers['x-course-source']);
    if(!entry||entry.enabled!==true||typeof entry.token!=='string'||entry.token.length<32){send(401,{error:'invalid_course_source'});return true;}
    source=entry.id;wanted=entry.token;
   }else wanted=token();
   const supplied=req.headers.authorization||'';
   const a=Buffer.from(supplied), b=Buffer.from('Bearer '+wanted);
   if(!wanted||a.length!==b.length||!timingSafeEqual(a,b)){send(401,{error:'请使用工作台访问密钥连接。'});return true;}
   if(req.method==='GET'&&req.url==='/workbench/api/state'){
    send(200,{...run('workbench.py',[]),busy:busy()});return true;
   }
   const url=new URL(req.url,'http://localhost');
   if(req.method==='GET'&&url.pathname==='/workbench/api/preview'){
    const at=url.searchParams.get('as_of')||'';
    const parsed=new Date(at+':00+08:00');
    if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(at)||!Number.isFinite(+parsed)||
      new Date(+parsed+8*3600000).toISOString().slice(0,16)!==at||at.slice(0,10)<'2025-10-13'||at.slice(0,10)>'2025-11-09'){
     send(400,{error:'请选择首月模拟数据范围内的有效时间'});return true;
    }
    send(200,run('workbench.py',['preview',at+':00+08:00']));return true;
   }
   if(req.method==='GET'&&url.pathname==='/workbench/api/history'){
    const id=url.searchParams.get('archive')||'';
    if(!/^[a-f0-9]{32}$/.test(id)){send(400,{error:'轮次编号无效'});return true;}
    send(200,run('workbench.py',['history',id]));return true;
   }
   if(req.method==='GET'&&url.pathname==='/workbench/api/plan'){
    const day=url.searchParams.get('day')||'';
    if(!/^\d{4}-\d{2}-\d{2}$/.test(day)||day<'2025-10-13'||day>'2025-11-09'){
     send(400,{error:'日期超出测试范围'});return true;
    }
    send(200,run('workbench.py',['plan',day]));return true;
   }
   if(req.method!=='POST'||!['clock','reset','clear-context','new-conversation','execute','highlights','automatic','advance','replan','skills-publish','simulate-completion','completion-events','course-completion','service-ledger','alert-reply','alert-resolve'].map(x=>'/workbench/api/'+x).includes(req.url)){
    send(404,{error:'入口不存在'});return true;
   }
   if((!machine&&req.headers['x-workbench']!=='1')||!String(req.headers['content-type']).startsWith('application/json')){
    send(403,{error:'请从工作台操作'});return true;
   }
   const chunks=[];let size=0;
   for await(const chunk of req){const bytes=Buffer.isBuffer(chunk)?chunk:Buffer.from(chunk);size+=bytes.length;if(size>16384){send(413,{error:'请求过大'});return true;}chunks.push(bytes);}
   let input;try{const raw=new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks));if(raw.length>4096){send(413,{error:'请求过大'});return true;}input=JSON.parse(raw);}catch{send(400,{error:'请求格式错误'});return true;}
   if(!input||typeof input!=='object'){send(400,{error:'请求格式错误'});return true;}
   if(req.url.endsWith('/alert-reply')||req.url.endsWith('/alert-resolve')){
    const reply=req.url.endsWith('/alert-reply');
    const allowed=reply?['run_id','alert_id','body','revision','request_id']:['run_id','alert_id','revision','reason'];
    if(Object.keys(input).some(k=>!allowed.includes(k))||
       !/^alert-[a-f0-9]{32}$/.test(input.alert_id||'')||
       !Number.isSafeInteger(input.revision)||input.revision<1||
       (reply&&(typeof input.body!=='string'||!input.body.trim()||input.body.length>604||!/^[-_a-zA-Z0-9]{8,100}$/.test(input.request_id||'')))||
       (!reply&&(typeof input.reason!=='string'||!input.reason.trim()||input.reason.length>2000))){
     send(400,{error:'请核对提醒版本、回复正文与发送请求编号；不能指定其他接收人'});return true;
    }
   }
   if(('auto_highlights' in input&&typeof input.auto_highlights!=='boolean')||
       (req.url.endsWith('/automatic')&&typeof input.enabled!=='boolean')){
    send(400,{error:'自动执行设置必须为开或关'});return true;
   }
   if(req.url.endsWith('/service-ledger')){
    if(input.ledger_run_id!==undefined&&!/^[a-f0-9]{32}$/.test(input.ledger_run_id)){send(400,{error:'轮次编号无效'});return true;}
    send(200,run('service_ledger.py',[JSON.stringify(input)]));return true;
   }
   if(req.url.endsWith('/new-conversation')&&(!input.lab_run_id||!input.family_id||
       typeof input.expected_context!=='string'||!Number.isSafeInteger(input.expected_seq)||
       !/^[a-f0-9-]{32,36}$/.test(input.request_id||''))){
    send(400,{error:'请刷新家庭会话后再操作'});return true;
   }
   const publishing=req.url.endsWith('/skills-publish');
   if(req.url.endsWith('/execute')&&(typeof input.task_id!=='string'||!/^service-[a-zA-Z0-9-]{1,150}$/.test(input.task_id))){
    send(400,{error:'请选择当前计划中的任务'});return true;
   }
   if(publishing&&(!/^[a-f0-9]{32}$/.test(input.version||'')||!/^[a-f0-9]{64}$/.test(input.expected||'')||typeof input.author!=='string'||input.author.length>80)){
    send(400,{error:'Skill发布参数无效'});return true;
   }
   if(!publishing&&input.run_id!==state()?.run_id){send(409,{error:'测试轮次已变化，请刷新后再操作。'});return true;}
   if(busy()&&!(req.url.endsWith('/automatic')&&input.enabled===false)){send(409,{error:'智能体正在处理微信消息或服务任务，请等结束后再试。'});return true;}
   if(req.url.endsWith('/simulate-completion')&&
       (typeof input.lesson_id!=='string'||typeof input.event_id!=='string'||typeof input.completed_at!=='string')){
    send(400,{error:'请选择课节、完课时间并提供事件编号'});return true;
   }
   if(req.url.endsWith('/completion-events')&&(!input.event||typeof input.event!=='object'||Array.isArray(input.event))){
    send(400,{error:'请提供完整的完课事件'});return true;
   }
   if(req.url.endsWith('/clock')||req.url.endsWith('/advance')){
    if(typeof input.as_of!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(input.as_of)){
     send(400,{error:'请选择日期和时间'});return true;
    }
    const canonical=input.as_of+':00+08:00';
    const parsed=new Date(canonical);
    // Reject normalization of invalid dates such as February 30.
    if(!Number.isFinite(+parsed)||new Date(+parsed+8*3600000).toISOString().slice(0,16)!==input.as_of||
      input.as_of.slice(0,10)<'2025-10-13'||input.as_of.slice(0,10)>'2025-11-09'){
     send(400,{error:'请选择 2025-10-13 至 2025-11-09 内的有效时间'});return true;
    }
    if(req.url.endsWith('/advance')&&canonical<=state()?.as_of){
     send(400,{error:'请选择更晚的时间；回退请开启新一轮。'});return true;
    }
   }
   const result=await mutate(machine?'course-completion':req.url.split('/').pop(),machine?{run_id:input.run_id,event:input.event,trusted_source:source}:input);
   send(result.status==='rejected'?400:200,result);
  }catch{send(500,{error:'操作未确认，请刷新查看当前状态；不要重复点击发送。'});}
  return true;
 };
}
