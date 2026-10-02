"""Provision a data-plane identity; never pass the ROOT key to Hermes."""
import json,os,pathlib,urllib.request,urllib.error
base=pathlib.Path('/root/autodl-tmp/digitalTutor')
os.umask(0o077)
root=(base/'config/openviking-api-key').read_text().strip()
def post(path,payload):
 req=urllib.request.Request('http://127.0.0.1:1933/api/v1/admin/'+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','X-API-Key':root})
 with urllib.request.urlopen(req,timeout=15) as r:return json.load(r)['result']
adminfile=base/'config/openviking-admin-key'
if not adminfile.exists():
 try:result=post('accounts',{'account_id':'digitaltutor','admin_user_id':'deployment-admin'})
 except urllib.error.HTTPError as e:
  if e.code!=409:raise
  result=post('accounts/digitaltutor/users/deployment-admin/key',{})
 adminfile.write_text(result['user_key']);adminfile.chmod(0o600)
userfile=base/'config/openviking-user-key'
if not userfile.exists():
 try:result=post('accounts/digitaltutor/users',{'user_id':'teacher','role':'user'})
 except urllib.error.HTTPError as e:
  if e.code!=409:raise
  result=post('accounts/digitaltutor/users/teacher/key',{})
 userfile.write_text(result['user_key']);userfile.chmod(0o600)
print('OpenViking data identity provisioned: digitaltutor / teacher (USER)')
