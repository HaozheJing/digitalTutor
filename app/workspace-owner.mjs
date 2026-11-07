import {spawn} from 'node:child_process';
export function workspaceOwner(root,onLost){
 return new Promise((resolve,reject)=>{
  const child=spawn('python3',['-B',root+'/adapters/workspace_owner.py',root],{stdio:['pipe','pipe','ignore']});
  let owned=false,closed=false,buffer='';
  const timer=setTimeout(()=>{child.kill();reject(Error('workspace_ownership_timeout'));},10000);
  child.on('error',error=>{clearTimeout(timer);reject(error);});
  child.stdout.on('data',data=>{buffer+=data.toString();if(!buffer.includes('\n'))return;clearTimeout(timer);
   if(buffer.trim()!=='owned'){child.kill();reject(Error('workspace_already_owned'));return;}
   owned=true;resolve({close(){closed=true;child.stdin.end();}});
  });
  child.on('exit',()=>{clearTimeout(timer);if(owned&&!closed)onLost();else if(!owned)reject(Error('workspace_ownership_failed'));});
 });
}
