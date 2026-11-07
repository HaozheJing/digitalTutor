// Experiment-only: end the native loop after the one expression delivery.
// Existing tutor hooks execute first and keep ownership/receipt validation.
export const name='experiment-reply-phase';
export function apply(ctx){
 const delivery=new Set(['reply_material_ready','reply_to_message','reply_with_highlights','deliver_message','deliver_proactive','deliver_festival','send_image','workflow_fulfill'].map(n=>'mcp__tutor__'+n));
 ctx.on('tools/execute',async(exec,next)=>{
  const result=await next();
  if(!delivery.has(exec.name)||result?.isError!==false||exec.signal?.aborted||typeof exec.concludeTurn!=='function')return result;
  let receipt;try{receipt=JSON.parse(result.value?.content?.[0]?.text);}catch{return result;}
  if(receipt?.reply_phase_terminal===true){exec.concludeTurn();return {...result};}
  return result;
 },{prepend:true});
}
