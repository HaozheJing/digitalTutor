// Adapter seam inside tutor-live. No Harness kernel patch, model call or sender.
// A future signed course-system transport translates to the same v1 envelope.
export function learningEventsAdapter({run}) {
 return (action,input)=> {
  if(!['simulate-completion','completion-events'].includes(action))return null;
  return run('completion_events.py',[action==='simulate-completion'?'simulate':'accept',JSON.stringify(input)]);
 };
}
