"""Create a private repository using an existing Git credential, without logging secrets."""
import argparse,json,os,subprocess,sys
import requests

parser=argparse.ArgumentParser()
parser.add_argument('--create',action='store_true')
args=parser.parse_args()
env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never')
result=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',text=True,capture_output=True,env=env,timeout=40)
credential=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
secret=credential.get('password')
if result.returncode or not secret:
 print('No existing GitHub Git credential available.');sys.exit(2)
session=requests.Session()
session.headers.update({'Authorization':'Bearer '+secret,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
response=session.get('https://api.github.com/user',timeout=30)
if response.status_code!=200:
 print('GitHub authentication failed; status:',response.status_code);sys.exit(3)
user=response.json()['login']
print('GitHub account:',user)
if args.create:
 response=session.post('https://api.github.com/user/repos',json={'name':'digitalTutor','description':'少年得到阅读营教辅数字员工：业务知识与后端部署','private':True,'auto_init':False},timeout=30)
 if response.status_code!=201:
  print('Repository creation not confirmed; status:',response.status_code)
  if response.status_code==422:print('Repository name may already exist; no existing repository was changed.')
  sys.exit(4)
 repo=response.json()
 assert repo['private'] is True
 print(json.dumps({'url':repo['html_url'],'clone_url':repo['clone_url'],'private':repo['private']},ensure_ascii=False))
