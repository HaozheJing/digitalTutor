import {LlmError,CONTEXT_WINDOW_EXCEEDED_CODE} from '../runtime/dependencies/node_modules/@deepseek-ai/dsh-llm/lib/index.js';
// Same calibrated text estimator as the final wire gate. Not an exact tokenizer.
export function checkRequestBudget(options,{contextWindow=131072,safety=4096}={}){
 const output=options.maxTokens??8192;
 if(!Number.isSafeInteger(output)||output<1024)throw Error('output_budget_exhausted');
 let images=0;
 const normalized=value=>{
  if(Array.isArray(value))return value.map(normalized);
  if(!value||typeof value!=='object')return value;
  if(['image','image_url','input_image'].includes(value.type)){images++;return {type:value.type,image:'reserved'};}
  return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,normalized(v)]));
 };
 const payload=normalized({system:options.system,messages:options.messages?.map(m=>({role:m.role,content:m.content})),tools:options.tools,toolHistory:options.toolHistory});
 const raw=JSON.stringify(payload);let ascii=0,wide=0;
 for(const ch of raw)ch.codePointAt(0)<128?ascii++:wide++;
 const estimate=Math.ceil((ascii/3+wide+1024)*1.25)+images*16384;
 if(estimate+output+safety>contextWindow)throw new LlmError('request_context_budget_exceeded',CONTEXT_WINDOW_EXCEEDED_CODE);
 return {estimator:'calibrated_text_v2',bytes:Buffer.byteLength(raw),estimate,output,safety,contextWindow,images};
}
