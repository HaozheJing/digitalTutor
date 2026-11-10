'use strict';
const $=id=>document.getElementById(id),esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let token=sessionStorage.getItem('family-lab-token')||sessionStorage.getItem('tutor-workbench-key')||'',state=null,detail=null,selected='F01',caseId=null,transition=null,dirty=false,renderedDraftKey=null,busy=false;
let contacts=null,contactChoices={},contactLoading=false,advancing=false;
let requestVersion=0, composeMode=null, followLatest=true;
const pending=new Set(), cache=new Map(),uid=()=>crypto.randomUUID(),labels={queued:'待发送',submitting:'提交中',accepted:'平台已受理',sent:'发送成功',unknown:'结果未知，待核对',failed:'发送失败',cancelled:'已拦截',expired:'已过期',running:'模型处理中',finished:'已完成'};
function friendlyError(code){return ({clock_waits_for_previous_transition:'上一次事件尚未处理完，请查看执行进度；实验暂停时可点击继续处理当前事件。',clock_waits_for_models:'模型仍在处理，请等当前请求结束后再推进。',clock_waits_for_turns_and_ingress:'正在处理消息或同步学情，请稍后再推进。',clock_waits_for_receipts:'还有排队发送或未决回执，请查看执行进度；结果未知的分片不会自动重发。',invalid_clock_advance:'目标时间必须晚于当前业务时间，并位于本轮课程范围。'})[code]||code;}
function notice(text,error=false){$('notice').textContent=text;$('notice').className=error?'error':'';}
async function api(path,body){const r=await fetch('/five-family/api/'+path,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json','X-Workbench':'1'}:{})},body:body?JSON.stringify(body):undefined});const v=await r.json();if(!r.ok){const error=new Error(r.status===401?'访问密钥无效，请重新输入。':friendlyError(v.error||'请求未成功'));error.status=r.status;throw error;}return v;}
function draftKey(){return state?.run.id+':'+selected+':'+(caseId||'conversation')+(detail?.context_id?':'+detail.context_id:'');}
function remember(){if(renderedDraftKey){cache.set(renderedDraftKey,$('reply').value);localStorage.setItem('family-lab-draft:'+renderedDraftKey,$('reply').value);}}
function pane(name){
 document.body.dataset.pane=name;
 document.querySelectorAll('.mobile-nav button').forEach(b=>b.setAttribute('aria-current',b.dataset.pane===name?'page':'false'));
}
document.querySelectorAll('.mobile-nav button').forEach(b=>b.onclick=()=>pane(b.dataset.pane));
function ready(){return !!detail && detail.family.family_id===selected && detail.family.run_id===state?.run.id;}
function switchFamily(id,cid=null){
 remember();selected=id;caseId=cid;detail=null;requestVersion++;composeMode=cid?'teacher':null;followLatest=true;
 renderedDraftKey=null;dirty=false;$('reply').value='';$('sim-message').value='';$('answer').value='';$('note').value='';
 $('student').textContent='正在载入会话…';$('messages').replaceChildren();$('notes').replaceChildren();$('evidence').replaceChildren();$('learning').replaceChildren();$('planning').replaceChildren();$('identity').textContent='';$('role-card').textContent='';$('agent-activity').textContent='';$('draft-label').textContent='';$('draft-status').textContent='';
 controls();pane('conversation');renderState();refreshDetail().catch(e=>notice(e.message,true));
}
function controls(){
 const ok=ready(),f=detail?.family,b=state?.bindings.find(x=>x.family_id===selected);
 $('clear-context').disabled=!ok||pending.has('clear-context')||Boolean(f?.busy);
 const simulation=ok && b?.mode==='simulation', held=ok && Boolean(f.held);
 const active=detail?.cases.find(c=>c.id===caseId && c.status==='open');
 for(const id of ['takeover','resume','pause-family','send','save-draft','close-case','save-note','sim-send','complete','inject-highlight','inject-answer'])$(id).disabled=!ok;
 const mode=composeMode || (held||!simulation?'teacher':'sim');
 $('sim-compose').hidden=mode!=='sim';$('teacher-compose').hidden=mode!=='teacher';
 $('tab-sim').setAttribute('aria-pressed',String(mode==='sim'));$('tab-teacher').setAttribute('aria-pressed',String(mode==='teacher'));
 $('tab-sim').disabled=$('tab-teacher').disabled=!ok;
 $('sim-message').disabled=!simulation;$('sim-send').disabled=!simulation||!$('sim-message').value.trim()||pending.has('message');
 $('sim-hint').textContent=!ok?'正在载入家庭…':simulation?'模拟消息进入当前家庭会话，回复走模拟通道。':'当前家庭已绑定微信。请从绑定账号发来消息；纯模拟轮次可在这里输入。';
 $('sim-context').textContent=simulation?'模拟通道':'微信通道';
 $('takeover').hidden=held;$('resume').hidden=!held;
 $('send').disabled=!held||!$('reply').value.trim()||pending.has('human-reply');
 $('save-draft').disabled=!active||!$('reply').value.trim();$('close-case').disabled=!active;
 $('save-note').disabled=!ok||!$('note').value.trim();
 $('complete').disabled=!ok||!$('lesson').value;$('inject-answer').disabled=!ok||!$('lesson').value||!$('answer').value.trim();
}
$('tab-sim').onclick=()=>{composeMode='sim';controls();};$('tab-teacher').onclick=()=>{composeMode='teacher';controls();};
for(const id of ['sim-message','note','answer','lesson'])$(id).addEventListener('input',controls);

function renderState(){if(!state)return;const run=state.run,fs=state.families,waiting=state.cases.filter(c=>c.status==='open'),inflight=state.outbox.filter(o=>['submitting','accepted','unknown'].includes(o.status));
 $('overview').innerHTML=[['待我处理',waiting.length],['AI 正在处理',state.models.filter(m=>m.status==='running').length+' / 3'],['待发送',state.outbox.filter(o=>o.status==='queued').length],['待核对回执',inflight.length],['暂停 / 接管',fs.filter(f=>f.held||f.paused).length]].map(([k,v])=>`<div class="metric"><b>${esc(v)}</b><span>${k}</span></div>`).join('');
 $('family-count').textContent=fs.length;$('round-badge').textContent=run.mode==='simulation'?'纯模拟轮次':'微信体验轮次';
 $('day-label').textContent='业务日期  '+run.as_of.slice(0,10).replaceAll('-',' / ');
 const ov=state.overview||{};$('business-evidence').textContent=`当日观察：学生消息 ${ov.student_messages||0}，新增完课事件 ${ov.completion_events||0}，答题/表达 ${ov.answers||0}；人工记录 ${ov.manual_notes||0}（线下电话 ${ov.offline_phone||0}，问题反馈 ${ov.feedback||0}）；本轮发送失败 ${ov.failed_parts||0}。这些是可追溯事实，未自动认定业务问题已解决。`;
 $('families').innerHTML=fs.map(f=>{const b=state.bindings.find(x=>x.family_id===f.family_id);return `<button type="button" aria-pressed="${f.family_id===selected}" class="family ${f.family_id===selected?'active':''}" data-family="${esc(f.family_id)}"><div class="avatar">${esc(f.student.slice(-1))}</div><div><b>${esc(f.student)}</b><span class="tag">${b?.mode==='wechat'?'真实微信':'模拟通道'}</span><div class="status">${esc((state.activities||[]).find(a=>a.family_id===f.family_id)?.summary||(f.held?'老师接管中':f.paused?'主动服务已暂停':f.busy?'AI 正在处理':'等待事件'))} · ${esc(b?.nickname||'未绑定')}</div></div></button>`;}).join('');
 $('families').querySelectorAll('[data-family]').forEach(e=>e.onclick=()=>switchFamily(e.dataset.family));
 $('cases').innerHTML=waiting.map(c=>`<button class="case" data-case="${esc(c.id)}" data-family="${esc(c.family_id)}"><b>${esc(fs.find(f=>f.family_id===c.family_id)?.student)}</b> · 等待 ${Math.floor((Date.now()/1000-c.first_wait)/60)} 分钟<br>${esc(c.next_step)}</button>`).join('')||'<p class="hint" style="padding:14px">暂无待处理事项</p>';
 $('cases').querySelectorAll('[data-case]').forEach(e=>e.onclick=()=>switchFamily(e.dataset.family,e.dataset.case));
 $('clock').textContent=run.as_of.replace('T',' ').replace('+08:00','')+' 北京时间\n'+(run.paused?'实验已暂停':run.clock_mode==='realtime'?'1 倍速走时 · 可正常互动':'业务时间冻结 · 可正常互动');
 $('clock-mode').textContent=run.clock_mode==='realtime'?'冻结业务时间':'开始 1 倍速走时';
 $('pause-run').textContent=run.paused?'继续实验':'暂停实验';
 const chosen=$('history-round').value;const options=state.rounds.map(r=>`<option value="${esc(r.id)}">${esc(r.created?new Date(r.created*1000).toLocaleString('zh-CN'):'')} · ${r.mode==='wechat'?'微信体验':'模拟'} · ${r.active?'当前':'历史'}</option>`).join('');if($('history-round').innerHTML!==options){$('history-round').innerHTML=options;if(state.rounds.some(r=>r.id===chosen))$('history-round').value=chosen;}
 $('new-round').disabled=!run.paused;const rr=state.round_request?.length?JSON.parse(state.round_request[0].value):null;if(rr)$('round-status').textContent=rr.status==='ready'?'新轮次已就绪，请继续实验':rr.status==='failed'?'新建失败，原始证据保留：'+rr.error:'正在创建独立轮次，请稍候';
 const f=fs.find(x=>x.family_id===selected);$('pause-family').textContent=f?.paused?'恢复主动服务':'暂停主动服务';
 const misses=state.models.filter(m=>m.status!=='queued'&&m.status!=='running'&&!m.usage).length;
 const costs=state.costs;
 $('runtime').innerHTML=`模型请求 ${costs.requests} 次 · 缺 usage ${costs.missing_usage} 次<br>已知费用 ¥${costs.total_known_cost_cny.toFixed(4)}（公开按量等值）<br>五家庭时钟同步 ${fs.filter(f=>f.applied_clock===run.epoch).length}/5<br>真实已发送 ${costs.real_sent_parts} 片；模拟已发送 ${costs.sent_parts-costs.real_sent_parts} 片<br>`+costs.families.map(x=>`${esc(x.family_id)}：¥${x.known_cost_cny.toFixed(4)}；每真实 sent 分片 ${x.cost_per_sent_part===null?'—':'¥'+x.cost_per_sent_part.toFixed(4)}`).join('<br>')+'<br>缺 usage / 未定价项单列，不按零计费。真实微信仅限你在“绑定微信体验者”中登记的联系人。';
}
function renderProgress(){
 const activities=state.activities||[],t=state.transition,pending=t&&t.status!=='settled';
 $('next-event').disabled=advancing||Boolean(pending&&!state.run.paused);
 $('advance').disabled=advancing||Boolean(pending)||!$('target').validity.valid||!$('target').value||$('target').value<=state.run.as_of.slice(0,16);
 $('next-event').textContent=pending?(state.run.paused?'继续处理当前事件':'正在处理本次事件…'):'推进到下一事件并自动执行';
 $('transition').textContent=pending?`${state.run.paused?'实验已暂停，请点击“继续处理当前事件”。':'正在自动处理，到点服务会自动发送。'}待完成服务 ${t.due_tasks.length} 项；模型排队/处理中 ${t.pending_models||0}；待确认分片 ${t.pending_parts}；同步或处理中家庭 ${t.waiting_families.length}。`:t?'本次推进已处理完；业务时间保持冻结，可正常互动。':'点击推进后自动执行到点服务，无需逐条点击发送。';
 $('service-progress').innerHTML=activities.map(a=>`<div class="activity-card" data-phase="${esc(a.phase)}"><b>${esc(a.student)}</b><div>${esc(a.summary)}${a.elapsed_seconds!==null?' · 已用 '+a.elapsed_seconds+' 秒':''}</div>${a.due_tasks.length?'<small>到点：'+a.due_tasks.map(x=>esc(x.title)).join('、')+'</small>':a.next?'<small>下一项 '+esc(a.next.due_at.slice(5,16).replace('T',' '))+' · '+esc(a.next.title)+'</small>':''}</div>`).join('');
 const current=activities.find(a=>a.family_id===selected);$('agent-activity').textContent=current?current.summary+(current.elapsed_seconds!==null?' · 已用 '+current.elapsed_seconds+' 秒':''):'';
}
function renderDetail(){if(!detail)return;const f=detail.family;$('student-mark').textContent=f.student.slice(-1);const b=state.bindings.find(x=>x.family_id===selected),l=detail.learning; $('student').textContent=f.student+'的会话';$('identity').textContent=`${b?.mode==='wechat'?'微信：'+b.nickname:'模拟通道'} · 当前身份：学生 · L2 首月`;
 $('learning').innerHTML=`<span class="tag">已排 ${l.summary_as_of.scheduled} 课</span><span class="tag">已完成 ${l.summary_as_of.completed} 课</span><span class="tag warn">历史缺课 ${l.summary_as_of.overdue} 课</span>`+l.lessons_as_of.map(x=>`<span class="lesson ${x.status==='completed'?'done':x.overdue?'missing':''}" title="${esc(x.scheduled_date)}">${x.lesson_no} ${x.status==='completed'?'✓':x.overdue?'待补':'待学'}</span>`).join('');
 $('planning').innerHTML=detail.tasks.filter(t=>t.day===state.run.as_of.slice(0,10)).slice(0,15).map(t=>`<div>${esc(t.due_at.slice(11,16))} · ${esc(t.title)} <span class="tag">${esc(t.status)}</span>${t.reason?' · '+esc(t.reason):''}</div>`).join('')||'当日规划尚未生成';
 const messages=detail.messages.filter(m=>m.speaker==='user').map(m=>({at:m.created_at,body:m.voice_understanding?.transcript||m.body,out:false,status:m.voice_understanding?'学生语音 · 自动理解，请核对原音':'学生消息',media:m.media}));
 for(const o of detail.outbox)messages.push({at:new Date(o.created*1000).toISOString(),body:o.body,out:true,status:(o.kind==='human'?'老师 · ':'AI / 宿主 · ')+(labels[o.status]||o.status),kind:o.message_type,business:o.business_at,mode:o.mode});
 messages.sort((a,b)=>new Date(a.at)-new Date(b.at));const holder=$('messages'),bottom=holder.scrollHeight-holder.scrollTop-holder.clientHeight<80;
 const markup=messages.map(m=>{let content=esc(m.body);if([5,6].includes(m.kind)&&/^https:\/\//.test(m.body))content=`<img src="${esc(m.body)}" alt="本次消息图片" loading="lazy">`;if(m.media?.type===6)content=`<img src="${esc(m.media.url)}" alt="学生发来的原图" loading="lazy">`;if(m.media?.type===2)content=`<audio controls preload="none" src="${esc(m.media.url)}"></audio><br><a href="${esc(m.media.url)}" target="_blank" rel="noopener">打开原始语音</a><br>${content}`;if(m.kind===1){try{const file=JSON.parse(m.body);content=`<a href="${esc(file.url)}" target="_blank" rel="noopener">📄 ${esc(file.name)}</a>`;}catch{content='文件消息';}}return `<div class="message ${m.out?'out':''}"><div class="bubble">${content}</div><small>${esc(m.status)} · ${esc(new Date(m.at).toLocaleTimeString('zh-CN'))}${m.mode==='simulation'?' · 模拟交付':''}</small></div>`;}).join('')||'<p class="hint">'+(detail.context_id?'新会话已就绪，等待第一条消息。':'这里会显示学生消息、老师回复及每个发送分片的真实状态。')+'</p>';
 if(holder._rendered!==markup||!holder.childElementCount){holder.innerHTML=markup;holder._rendered=markup;if(bottom||followLatest)holder.scrollTop=holder.scrollHeight;}followLatest=false;
 const active=detail.cases.find(c=>c.id===caseId)||detail.cases.find(c=>c.status==='open');if(!caseId&&active)caseId=active.id;
 $('evidence').innerHTML=active?`<b>${esc(active.next_step)}</b><p>最初等待：${esc(new Date(active.first_wait*1000).toLocaleString('zh-CN'))} · ${active.status==='closed'?'事项已结束':'待老师处理'}</p>`+detail.alerts.filter(a=>(Array.isArray(active.evidence)?active.evidence:JSON.parse(active.evidence||'[]')).includes(a.id)).map(a=>`<p>${esc(a.reason)}<br>${esc(a.source_quote||a.evidence||a.summary||'')}</p>`).join('')+detail.messages.filter(m=>m.speaker==='user').slice(-3).map(m=>`<blockquote>学生原话：${esc(m.body)}</blockquote>`).join(''):'暂无转人工事项';
 const key=draftKey();if(renderedDraftKey!==key){$('reply').value=cache.get(key)??localStorage.getItem('family-lab-draft:'+key)??active?.edited_body??active?.ai_body??'';renderedDraftKey=key;dirty=false;}
 $('draft-label').textContent=active?(active.status==='closed'?'事项已结束 · 保留历史草稿，未自动发送':active.edited_body?'人工编辑草稿，尚未发送':active.ai_body?'AI 参考草稿，尚未发送':'待老师处理 · 可先保存草稿'):'人工回复 · 发送前请接管';
 $('draft-status').textContent=active?.draft_epoch&&active.draft_epoch!==f.epoch?'依据已变化，请核对草稿；已编辑内容保留':dirty?'修改已暂存本机':'草稿不会因刷新或新消息被覆盖';
 $('send').disabled=!f.held;$('close-case').disabled=!active||active.status==='closed';$('save-draft').disabled=!active||active.status==='closed';
 $('role-card').textContent=detail.role_card.profile;$('sim-send').disabled=b?.mode==='wechat';
 $('notes').innerHTML=detail.notes.slice(-5).map(n=>{const b=JSON.parse(n.body);return `<div class="note">${esc(b.method)} · ${esc(b.text)}</div>`;}).join('');
}
async function refreshDetail(){
 if(!token||!state)return;
 const id=selected,runId=state.run.id,version=++requestVersion,v=await api('family?id='+encodeURIComponent(id));
 if(version!==requestVersion||id!==selected||runId!==state.run.id||v.family.run_id!==runId||v.family.family_id!==id)return;
 if(detail && detail.context_id!==v.context_id){
  caseId=null;renderedDraftKey=null;dirty=false;followLatest=true;
  for(const field of ['reply','sim-message','note','answer'])$(field).value='';
 }
 detail=v;renderDetail();
 const lessons=(detail.learning.lessons_as_of||[]).filter(x=>x.scheduled_date<=state.run.as_of.slice(0,10));
 const choices=lessons.map(x=>`<option value="${Number(x.lesson_no)}">第 ${Number(x.lesson_no)} 课</option>`).join('');
 if($('lesson').innerHTML!==choices){const old=$('lesson').value;$('lesson').innerHTML=choices;if(lessons.some(x=>String(x.lesson_no)===old))$('lesson').value=old;}
 controls();
}
async function refresh(){
 if(busy||!token)return;busy=true;
 try{
  const previousRun=state?.run.id,next=await api('state');
  if(previousRun&&previousRun!==next.run.id){
   remember();caseId=null;renderedDraftKey=null;dirty=false;detail=null;transition=null;
   $('transition').textContent='';$('history-round').value='';
  }
  state=next;$('connection').hidden=true;$('workspace').hidden=false;$('open-contacts').disabled=false;$('key').value='';
  if(previousRun!==next.run.id){const stamp=new Date(Date.parse(next.run.as_of)+60000+8*3600000).toISOString().slice(0,16);$('target').value=stamp>$('target').max?$('target').max:stamp;$('round-time').value=next.run.as_of.slice(0,16);composeMode=null;followLatest=true;}
  if(!previousRun)notice('');
  renderState();renderProgress();await refreshDetail();
  if(state.transition?.status==='settled')transition=null;
 }catch(e){notice(e.message,true);if(e.status===401){token='';sessionStorage.removeItem('family-lab-token');sessionStorage.removeItem('tutor-workbench-key');state=null;detail=null;$('connection').hidden=false;$('workspace').hidden=true;$('open-contacts').disabled=true;}}finally{busy=false;}
}
async function act(path,extra={}){
 if(!state||pending.has(path))throw new Error('请等待上一项操作完成');
 const familyAction=['message','note','draft','close-case','human-reply','answer','completion','test-highlight'].includes(path)||path==='control'&&extra.action!=='experiment_pause';
 if(familyAction&&!ready()){notice('会话正在更新，请稍后再操作。',true);throw new Error('family_not_ready');}
 pending.add(path);controls();try{const result=await api(path,{run_id:state.run.id,family_id:selected,...extra});notice(({message:'模拟来信已接收，可在会话中查看处理结果。',note:'内部记录已保存。','human-reply':'人工回复已提交，可在会话中查看发送状态。','close-case':'事项已结束。',draft:'草稿已保存。'})[path]||'操作已提交。');await refresh();return result;}catch(e){notice(e.message,true);throw e;}finally{pending.delete(path);controls();}}
$('login-form').onsubmit=async(e)=>{e.preventDefault();token=$('key').value.trim();if(!token)return;sessionStorage.setItem('family-lab-token',token);await refresh();};
$('reply').oninput=()=>{dirty=true;remember();controls();$('draft-status').textContent='修改已暂存本机';};
$('takeover').onclick=()=>act('control',{action:'takeover',value:true}).catch(()=>{});
$('resume').onclick=()=>act('control',{action:'takeover',value:false,reason:'老师明确恢复 AI'}).catch(()=>{});
$('clear-context').onclick=async()=>{
 if(!ready()||pending.has('clear-context'))return;
 const family=detail.family,run=family.run_id,id=family.family_id;
 pending.add('clear-context');controls();
 try{
  await api('clear-context',{run_id:run,family_id:id,expected_seq:family.inbox_seq,expected_context:detail.context_id||'',request_id:uid()});
  if(state.run.id===run && selected===id){await refreshDetail();notice('已开启新会话，旧聊天记录已归档。');}
 }catch(e){notice(e.message,true);}finally{pending.delete('clear-context');controls();}
};
$('pause-family').onclick=()=>act('control',{action:'service_pause',value:!detail.family.paused}).catch(()=>{});
$('pause-run').onclick=()=>act('control',{action:'experiment_pause',value:!state.run.paused}).catch(()=>{});
$('clock-mode').onclick=()=>act('clock-mode',{mode:state.run.clock_mode==='realtime'?'frozen':'realtime'}).catch(()=>{});
$('new-round').onclick=()=>act('new-round',{mode:$('round-mode').value,as_of:$('round-time').value+':00+08:00'}).catch(()=>{});
async function advanceEvent(path){if(advancing)return;advancing=true;renderProgress();try{const t=await act(path,path==='advance'?{target:$('target').value+':00+08:00'}:{});transition=t.transition_id;$('target').value=t.target.slice(0,16);notice(t.continued?'已继续处理当前事件，将自动规划并发送适用服务。':'已推进并启动自动执行，无需逐条点击发送。');}catch{}finally{advancing=false;await refresh();renderProgress();}}
$('advance').onclick=()=>advanceEvent('advance');
$('next-event').onclick=()=>advanceEvent('next-event');$('target').oninput=()=>{if(state)renderProgress();};
$('complete').onclick=()=>act('completion',{lesson_no:Number($('lesson').value),request_id:uid()}).catch(()=>{});
$('inject-answer').onclick=()=>act('answer',{lesson_no:Number($('lesson').value),answer_kind:$('answer-kind').value,body:$('answer').value,request_id:uid()}).catch(()=>{});
$('inject-highlight').onclick=()=>act('test-highlight',{request_id:uid()}).catch(()=>{});
$('export-round').onclick=async()=>{try{const r=await api('report?run_id='+encodeURIComponent($('history-round').value)),url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='five-family-review-'+r.run.id+'.json';a.click();URL.revokeObjectURL(url);notice('已导出复盘；没有回放消息。');}catch(e){notice(e.message,true);}};
$('sim-send').onclick=async()=>{
 const input=$('sim-message'),body=input.value.trim(),family=selected,run=state?.run.id;
 if(!body||!ready()||state.bindings.find(x=>x.family_id===selected)?.mode!=='simulation')return;
 const key='family-lab-sim:'+run+':'+family;let p=JSON.parse(sessionStorage.getItem(key)||'null');
 if(!p||p.body!==body){p={body,request_id:uid()};sessionStorage.setItem(key,JSON.stringify(p));}
 try{await act('message',p);sessionStorage.removeItem(key);if(run===state?.run.id&&family===selected&&input.value.trim()===body)input.value='';}catch{}finally{controls();}
};
$('save-note').onclick=async()=>{try{await act('note',{body:$('note').value,method:$('note-method').value,request_id:uid()});$('note').value='';}catch{}};
$('save-draft').onclick=async()=>{const c=detail.cases.find(c=>c.id===caseId);if(!c)return;try{await act('draft',{case_id:caseId,body:$('reply').value,revision:c.draft_revision||0});dirty=false;}catch{}};
$('close-case').onclick=()=>act('close-case',{case_id:caseId,expected_seq:detail.family.inbox_seq}).catch(()=>{});
$('send').onclick=async()=>{const body=$('reply').value,originalKey=draftKey(),key='family-lab-pending-send:'+draftKey();let pending=JSON.parse(localStorage.getItem(key)||'null');if(!pending||pending.body!==body){pending={body,request_id:uid()};localStorage.setItem(key,JSON.stringify(pending));}$('send').disabled=true;try{await act('human-reply',{...pending,case_id:caseId});localStorage.removeItem(key);if(draftKey()===originalKey && $('reply').value===body){$('reply').value='';remember();dirty=false;}}catch{}finally{controls();}};
$('toggle-learning').onclick=()=>{const hidden=$('learning').hidden;$('learning').hidden=!hidden;$('planning').hidden=!hidden;$('toggle-learning').setAttribute('aria-expanded',String(hidden));};
$('lesson').innerHTML=Array.from({length:20},(_,i)=>`<option value="${i+1}">第 ${i+1} 课</option>`).join('');
setInterval(refresh,3000);if(token)refresh();else notice('');

function contactStatus(message,error=false){$('contacts-status').textContent=message;$('contacts-status').className=error?'error':'hint';}
function renderContacts(){if(!contacts||!state)return;const query=$('contact-search').value.trim().toLowerCase();
 const bindingReason=contactLoading?'正在核对联系人，请稍候':state.run.id!==contacts.run_id?'轮次已变化，请关闭并重新打开此窗口':state.run.mode!=='wechat'?'当前是纯模拟轮次，请在下方建立微信体验轮次后保存绑定':!state.run.paused?'请先点击“暂停实验以配置”':'';
 $('apply-contacts').disabled=Boolean(bindingReason);$('binding-reason').textContent=bindingReason;
 $('contact-roster').innerHTML=state.families.map(f=>{const binding=contacts.bindings.find(b=>b.family_id===f.family_id),value=contactChoices[f.family_id]||'';const choices=contacts.items.filter(c=>c.id===value||!query||(c.name+' '+c.remark).toLowerCase().includes(query));
 const missing=value&&!contacts.items.some(c=>c.id===value);return `<div class="contact-row"><label for="contact-${esc(f.family_id)}"><b>${esc(f.student)}</b> · ${esc(f.family_id)}<small>当前：${binding?.mode==='wechat'?esc(binding.name):'模拟通道'}</small></label><select id="contact-${esc(f.family_id)}" data-contact-family="${esc(f.family_id)}"><option value="">模拟通道（不实发）</option>${missing?`<option value="${esc(value)}" selected>原绑定 ${esc(binding?.name)} · 请刷新好友核对</option>`:''}${choices.map(c=>`<option value="${esc(c.id)}" ${c.id===value?'selected':''}>${esc(c.name)}${c.remark?' · '+esc(c.remark):''} · 身份尾号 ${esc(c.identity_hint)}</option>`).join('')}</select></div>`;}).join('');
 $('contact-roster').querySelectorAll('[data-contact-family]').forEach(e=>e.onchange=()=>{contactChoices[e.dataset.contactFamily]=e.value;});
}
async function loadContacts(){contacts=await api('contacts');contactChoices=Object.fromEntries(contacts.bindings.map(b=>[b.family_id,b.contact_id||'']));renderContacts();contactStatus(`已读取 ${contacts.items.length} 位好友；企微账号尾号 ${contacts.account_hint}。${contacts.updated?'列表更新时间 '+new Date(contacts.updated*1000).toLocaleString('zh-CN'):'请先刷新企微好友。'} 保存前需暂停实验。`);}
$('open-contacts').onclick=async()=>{if(!state){notice('请先连接工作台',true);return;}$('contacts-dialog').showModal();try{await loadContacts();}catch(e){contactStatus(e.message,true);}};
$('close-contacts').onclick=()=>$('contacts-dialog').close();
$('contact-search').oninput=renderContacts;
$('refresh-contacts').onclick=async()=>{if(contactLoading)return;contactLoading=true;renderContacts();$('refresh-contacts').disabled=true;contactStatus('正在从企微读取好友，不会发消息…');try{await act('contacts-refresh');await loadContacts();}catch(e){contactStatus(e.message,true);}finally{contactLoading=false;$('refresh-contacts').disabled=false;renderContacts();}};
$('pause-for-binding').onclick=async()=>{try{await act('control',{action:'experiment_pause',value:true});renderContacts();contactStatus('实验已暂停。等待正在执行的模型与回执完成后即可保存；绑定本身不会发消息。');}catch(e){contactStatus(e.message,true);}};
$('apply-contacts').onclick=async()=>{if(contactLoading)return;if(contacts.run_id!==state.run.id){contactStatus('轮次已变化，请重新打开绑定窗口',true);return;}contactLoading=true;renderContacts();contactStatus('正在核对好友关系和会话身份，并保存绑定…');try{const result=await act('contacts-bind',{mapping:Object.fromEntries(Object.entries(contactChoices).map(([k,v])=>[k,v||null])),versions:Object.fromEntries(contacts.bindings.map(b=>[b.family_id,b.version]))});await loadContacts();contactStatus(`已保存 ${result.real_families} 个微信家庭，其余使用模拟通道。未发送消息、未调用模型。关闭本窗口后点击“继续实验”开始服务。`);}catch(e){contactStatus(e.message,true);}finally{contactLoading=false;renderContacts();}};
$('contacts-new-round').onclick=async()=>{try{if(!state.run.paused)await act('control',{action:'experiment_pause',value:true});await act('new-round',{mode:'wechat',as_of:$('contact-round-time').value+':00+08:00'});contactStatus('正在建立干净轮次，保持暂停。右侧显示“新轮次已就绪”后，重新打开本窗口选择体验者。');$('apply-contacts').disabled=true;}catch(e){contactStatus(e.message,true);}};
