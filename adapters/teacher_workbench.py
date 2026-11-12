"""Authenticated teacher drafts and model tests. No production publish capability.

Reads the deployed Skill tree and publication pointer; all writes belong to a
separate workspace. The existing Studio sandbox implements simulated tools.
"""
import concurrent.futures
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

import skill_studio as legacy
from service_catalog import ACTION_SKILLS, canonical_skill
from service_content import DISABLED_REFERENCES

MODEL = 'deepseek-v4.1-flash-economy'
SAFE_NAME = re.compile(r'^[a-z][a-z0-9-]{0,99}$')
SAFE_FILE = re.compile(r'^references/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+\.md$')
SHARED_TITLES = {'tutor-main':'阅读服务主规则','reply-style':'消息表达规范',
 'plan-today':'当日服务规划','proactive-service':'主动服务规则','service-policy':'服务政策与约束',
 'review-context':'上下文回顾','remember-family':'家庭信息记忆','student-service-tier':'学生服务分层'}
PUBLISHED = ACTION_SKILLS | {'care-calendar-day','send-festival-wishes'}


class TeacherStudio(legacy.Studio):
    def __init__(self, root=legacy.ROOT, model=None, native_runner=None):
        self.root=Path(root); self.skills=self.root/'skills'
        self.source_home=self.root/'runtime/skill-studio'
        self.home=self.root/'runtime/teacher-skill-workbench'
        self.home.mkdir(parents=True,exist_ok=True)
        self.db=self.home/'studio.sqlite';self.lock=threading.RLock();self.local=threading.local()
        # Copy, never move or mutate, historical teacher drafts and credentials.
        if not self.db.exists() and (self.source_home/'studio.sqlite').is_file():
            with closing(sqlite3.connect((self.source_home/'studio.sqlite').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(self.db)) as dst:src.backup(dst)
        self.model=model or self.call_model
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self._reply_lab = None
        self.native_runner = native_runner
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,token_hash TEXT UNIQUE,admin INTEGER,enabled INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY,skill TEXT,files TEXT,digest TEXT,author TEXT,note TEXT,parent TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT,author TEXT,version TEXT,input TEXT,status TEXT,result TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS publications(id TEXT PRIMARY KEY,skill TEXT,version TEXT,previous TEXT,author TEXT,created TEXT,status TEXT);
            CREATE TABLE IF NOT EXISTS assemblies(id TEXT PRIMARY KEY,author TEXT,version TEXT,payload TEXT,digest TEXT,created TEXT);
            ''')
            c.execute("UPDATE jobs SET status='failed',result=? WHERE status IN ('queued','running')",(legacy.encoded({'error':'工作台重启中断了本次试测，可从历史记录重新运行。'}),))

    def active(self):
        p=self.source_home/'active.json'
        return json.loads(p.read_text(encoding='utf8')) if p.is_file() else {}

    def installed(self):
        names={p.parent.name for p in self.skills.glob('*/SKILL.md')}
        names.update(name for name in self.active() if name in PUBLISHED)
        hidden = set() if os.environ.get('TUTOR_EXPERIMENT_INTERNAL_DRAFTS') == '1' else {'plan-after-lesson-completion','review-service-case'}
        return sorted(name for name in names if name not in hidden and SAFE_NAME.fullmatch(name) and canonical_skill(name)==name)

    def source(self,name):
        if not isinstance(name,str) or not SAFE_NAME.fullmatch(name) or name not in self.installed():
            raise legacy.Fault('请选择当前已上线的 Skill。',403)
        active=self.active().get(name) if name in PUBLISHED else None
        directory=self.skills/name
        if active:
            ident=active.get('version','')
            if not legacy.ID.fullmatch(ident):raise legacy.Fault('线上 Skill 版本指针无效，请联系维护人员。',409)
            directory=self.source_home/'bundles'/ident/name
        if not (directory/'SKILL.md').is_file():raise legacy.Fault('线上 Skill 文件缺失，请联系维护人员。',409)
        paths=[directory/'SKILL.md',*sorted((directory/'references').rglob('*.md'))]
        if directory.is_symlink() or any(p.is_symlink() or not p.resolve().is_relative_to(directory.resolve()) for p in paths):raise legacy.Fault('不读取链接到外部的 Skill 文件。',403)
        files={p.relative_to(directory).as_posix():p.read_text(encoding='utf8') for p in paths}
        return files

    def catalog(self):
        known={row['skill']:row for row in legacy.ITEMS};items=[]
        for name in self.installed():
            files=self.source(name);body=files['SKILL.md'];heading=re.search(r'^#\s+(.+)',body,re.M)
            row=dict(known.get(name,{'id':name,'skill':name,'family':'G','title':SHARED_TITLES.get(name,heading[1] if heading else name),'trigger':'共享规则或运行支持 Skill'}))
            row.update(live_digest=legacy.digest(files),file_count=len(files),published_bundle=bool(name in PUBLISHED and self.active().get(name)))
            items.append(row)
        return sorted(items,key=lambda r:('SIHQG'.find(r['family']),r['id']))

    def validate(self,name,files):
        if name not in self.installed():raise legacy.Fault('只能保存已上线 Skill 的测试版本。',403)
        if not isinstance(files,dict) or not 1<=len(files)<=40 or 'SKILL.md' not in files:raise legacy.Fault('需要 Skill 正文及关联 Markdown 文件。')
        for path,body in files.items():
            if path!='SKILL.md' and not SAFE_FILE.fullmatch(path):raise legacy.Fault('只允许正文和 references 下的 Markdown 文件。')
            if not isinstance(body,str) or len(body)>100000 or '\0' in body:raise legacy.Fault('文件格式错误或过大。')
        if sum(map(len,files.values()))>250000:raise legacy.Fault('一个版本不能超过25万字。')
        text=files['SKILL.md'].replace('\r\n','\n')
        if not text.startswith('---\n') or '\n---' not in text[4:]:raise legacy.Fault('请保留 Skill 开头的信息区。')
        front=text.split('---',2)[1]
        if not re.search(r'^name:\s*'+re.escape(name)+r'\s*$',front,re.M) or not re.search(r'^description:\s*\S',front,re.M):raise legacy.Fault('Skill 名称不能更改，且需要 description。')
        if len(text.split('---',2)[-1].strip())<30:raise legacy.Fault('正文过短，请补充执行要求。')
        for path in re.findall(r'\]\((references/[A-Za-z0-9_./-]+\.md)\)',text):
            if path not in files and path not in DISABLED_REFERENCES.get(name, set()):
                raise legacy.Fault('缺少关联文件：'+path)
        return files

    def detail(self,name):
        files=self.source(name)
        with self.connect() as c:versions=[dict(row) for row in c.execute('SELECT id,skill,digest,author,note,parent,created FROM versions WHERE skill=? ORDER BY created DESC,rowid DESC',(name,))]
        return {'skill':name,'files':files,'live_digest':legacy.digest(files),'active':self.active().get(name),'versions':versions,'publications':[], 'can_publish':False}

    def dependencies(self,name):
        row=next((r for r in legacy.ITEMS if r['skill']==name),None)
        if row:return legacy.action_dependencies(row['id'] if row['id']!='I05-calendar' else 'I05')['skills']
        return ['reply-style'] if name!='reply-style' and 'reply-style' in self.installed() else []

    def assemble(self,actor,data):
        edited=self.version(data.get('version'));self.source(edited['skill'])
        primary=data.get('primary_skill') or edited['skill'];self.source(primary)
        overrides=data.get('overrides',{})
        if not isinstance(overrides,dict) or len(overrides)>16:raise legacy.Fault('最多组合16个额外测试版本。')
        resolved={}
        for name,ident in overrides.items():
            self.source(name);v=self.version(ident)
            if v['skill']!=name:raise legacy.Fault('测试版本与 Skill 名称不一致。')
            resolved[name]=v
        # The editor's saved version is always included, including when testing
        # reply-style together with a business action selected as primary.
        resolved[edited['skill']]=edited
        names=list(dict.fromkeys([primary,*self.dependencies(primary),*resolved]))
        bundle={};manifest=[]
        for name in names:
            if name in resolved:files=resolved[name]['files'];version_id=resolved[name]['id'];origin='saved_version'
            else:files=self.source(name);version_id=None;origin='live_snapshot'
            omitted=[path for path in files if path in DISABLED_REFERENCES.get(name,set())]
            files={path:body for path,body in files.items() if path not in omitted}
            bundle[name]=files
            manifest.append({'skill':name,'version':version_id,'origin':origin,'digest':legacy.digest(files),'files':list(files),'omitted_inactive_files':omitted,'characters':sum(map(len,files.values()))})
        text='\n\n'.join('【'+name+' / '+path+'】\n'+body for name,files in bundle.items() for path,body in files.items())
        if len(text)>350000:raise legacy.Fault('上下文超过35万字，请减少额外 Skill。')
        payload={'version':edited['id'],'edited_skill':edited['skill'],'primary_skill':primary,'bundle':bundle,'manifest':manifest,'text':text,'model':MODEL,'simulation':True,'can_publish':False}
        ident=uuid.uuid4().hex;digest=hashlib.sha256(legacy.encoded(payload).encode()).hexdigest()
        with self.connect() as c:c.execute('INSERT INTO assemblies VALUES(?,?,?,?,?,?)',(ident,actor['name'],edited['id'],legacy.encoded(payload),digest,legacy.now()))
        return {**payload,'id':ident,'digest':digest,'characters':len(text),'notice':'冻结当前正文、案例和关联规则，后续试测读取此快照。expected 与测试摘要不会传给被测模型。'}

    def assembly(self,ident):
        if not isinstance(ident,str) or not legacy.ID.fullmatch(ident):raise legacy.Fault('上下文编号无效。')
        with self.connect() as c:row=c.execute('SELECT * FROM assemblies WHERE id=?',(ident,)).fetchone()
        if not row:raise legacy.Fault('上下文不存在，请重新组装。',404)
        data=json.loads(row['payload']);digest=hashlib.sha256(legacy.encoded(data).encode()).hexdigest()
        if digest!=row['digest']:raise legacy.Fault('上下文快照校验失败。',409)
        return {**data,'id':row['id'],'digest':digest}

    def context(self,version,include_dependencies=True):
        if '_assembly' in version:return version['_assembly']['text']
        names=list(dict.fromkeys([version['skill'],*(self.dependencies(version['skill']) if include_dependencies else [])]))
        return '\n\n'.join('【'+name+' / '+path+'】\n'+body for name in names for path,body in (version['files'] if name==version['skill'] else self.source(name)).items())

    def mock_tool(self,name,args,dataset,version):
        if name in ('service_skill','service_reference') and '_assembly' in version:
            if not isinstance(args,dict):return {'status':'invalid_arguments','simulation':True}
            skill=canonical_skill(args.get('name'));path='SKILL.md' if name=='service_skill' else args.get('path','')
            if path!='SKILL.md' and (not isinstance(path,str) or not SAFE_FILE.fullmatch(path)):return {'status':'blocked','simulation':True,'reason':'只可读取已组装的 Skill Markdown。'}
            body=version['_assembly']['bundle'].get(skill,{}).get(path)
            return {'name':skill,'path':path,'body':body,'status':'ok' if body else 'unknown','simulation':True,'context_digest':version['_assembly']['digest']}
        return super().mock_tool(name,args,dataset,version)

    @staticmethod
    def validate_dataset(data):
        # Compatible models occasionally place the requested simulation marker
        # inside tool_results. It is metadata, never an executable tool.
        if isinstance(data,dict) and isinstance(data.get('tool_results'),dict) and 'simulation' in data['tool_results']:
            marker=data['tool_results']['simulation']
            if not isinstance(marker,bool):raise legacy.Fault('simulation 标记必须为布尔值。')
            data={**data,'simulation':True,'tool_results':{name:value for name,value in data['tool_results'].items() if name!='simulation'}}
        return legacy.Studio.validate_dataset(data)

    def publish(self,*args,**kwargs):raise legacy.Fault('教师工作台没有上线权限；版本仅用于保存、对比和试测。',403)
    def apply(self,*args,**kwargs):raise legacy.Fault('教师工作台没有修改线上版本的权限。',403)

    def restore(self,actor,data):
        previous=self.version(data.get('version'))
        return self.save(actor,{'skill':previous['skill'],'files':previous['files'],'parent':previous['id'],'note':str(data.get('note') or '从 '+previous['id'][:8]+' 恢复的测试版本')})

    def call_model(self,messages,tools=None):
        cfg=json.loads((self.root/'config/provider.json').read_text())
        payload={'model':MODEL,'messages':messages,'max_tokens':6000,'temperature':0.3,'thinking':{'type':'disabled'},'response_format':{'type':'json_object'}}
        if tools:payload['tools']=tools
        req=Request(cfg['base_url'].rstrip('/')+'/chat/completions',data=legacy.encoded(payload).encode(),headers={'Authorization':'Bearer '+cfg['api_key'],'Content-Type':'application/json'})
        with urlopen(req,timeout=120) as response:result=json.loads(response.read(2000000))
        usage={'model':result.get('model',MODEL),'usage':result.get('usage'),'at':legacy.now()}
        if hasattr(self.local,'calls'):
            self.local.calls.append(usage)
            if getattr(self.local,'job',None):
                with self.connect() as c:c.execute("UPDATE jobs SET result=? WHERE id=? AND status='running'",(legacy.encoded({'progress':{'model_calls':len(self.local.calls),'model':MODEL}}),self.local.job))
        choice=result['choices'][0]
        if choice.get('finish_reason')=='length':raise legacy.Fault('模型输出被截断，请缩小测试场景。')
        return choice['message']

    @staticmethod
    def usage_summary(calls):
        total=output=cached=0;available=bool(calls);cache_known=True
        for call in calls:
            u=call.get('usage')
            if not isinstance(u,dict) or not isinstance(u.get('prompt_tokens'),int) or not isinstance(u.get('completion_tokens'),int):available=False;continue
            total+=u['prompt_tokens'];output+=u['completion_tokens']
            details=u.get('prompt_tokens_details') or {}
            hit=u.get('prompt_cache_hit_tokens',details.get('cached_tokens') if isinstance(details,dict) else None)
            if not isinstance(hit,int):cache_known=False
            else:cached+=max(0,min(hit,u['prompt_tokens']))
        estimate=((total-cached)*1.6+cached*.032+output*6.4)/1000000 if available else None
        return {'model':MODEL,'model_calls':len(calls),'usage_available':available,'input_tokens':total if available else None,
            'output_tokens':output if available else None,'cached_input_tokens':cached if available and cache_known else None,
            'estimated_cny':estimate,'price_note':'高估价格：高峰输入1.6元/百万Token、缓存命中0.032元、输出6.4元；非实际扣款，未计缓存存储。缓存量缺失时按全部未缓存输入估算。','calls':calls}

    def start_job(self,actor,data,kind):
        if kind not in ('generate','simulate'):raise legacy.Fault('测试类型无效。')
        if kind=='simulate':data={**data,'dataset':self.validate_dataset(data.get('dataset'))}
        assembly=self.assembly(data['assembly_id']) if data.get('assembly_id') else self.assemble(actor,data)
        if assembly['version']!=data.get('version'):raise legacy.Fault('上下文与所选版本不一致，请重新组装。',409)
        data={**data,'assembly_id':assembly['id']}
        with self.lock:
            with self.connect() as c:
                if c.execute("SELECT COUNT(*) FROM jobs WHERE author=? AND status IN ('queued','running')",(actor['name'],)).fetchone()[0]:raise legacy.Fault('您已有试测在进行，请等待完成。',429)
            result=super().start_job(actor,data,kind)
        return {**result,'assembly_id':assembly['id'],'context_digest':assembly['digest'],'model':MODEL}

    def run_job(self,ident,kind,version,data):
        self.local.calls=[];self.local.job=ident
        with self.connect() as c:c.execute("UPDATE jobs SET status='running' WHERE id=?",(ident,))
        try:
            assembly=self.assembly(data['assembly_id']);primary=assembly['primary_skill']
            effective={**version,'skill':primary,'files':assembly['bundle'][primary],'digest':legacy.digest(assembly['bundle'][primary]),'_assembly':assembly}
            value=self.generate(effective,data['prompt']) if kind=='generate' else self.simulate(effective,data['dataset'])
            result={'dataset':value} if kind=='generate' else value
            result.update(context_id=assembly['id'],context_digest=assembly['digest'],context_manifest=assembly['manifest'],primary_skill=primary,edited_skill=assembly['edited_skill'],model=MODEL,can_publish=False)
            status='done'
        except legacy.Fault as error:result={'error':str(error)};status='failed'
        except Exception as error:result={'error':'模型服务超时或返回异常；未修改线上 Skill，也未发送微信。','error_kind':type(error).__name__};status='failed'
        result['cost']=self.usage_summary(self.local.calls)
        with self.connect() as c:c.execute('UPDATE jobs SET status=?,result=? WHERE id=?',(status,legacy.encoded(result),ident))
        self.local.job=None;self.local.calls=[]

    def dispatch(self,actor,path,data=None):
        data=data or {}
        if path.startswith('reply-lab/'):
            # Lazily initialized under the existing server lock. Track B may
            # provide a trusted native runner; clients cannot select a URL.
            with self.lock:
                if self._reply_lab is None:
                    from reply_lab import ReplyLab
                    self._reply_lab = ReplyLab(self, runner=self.native_runner)
            return self._reply_lab.dispatch(actor,path[len('reply-lab/'):],data)
        if path in ('publish','apply','rollback','skills-publish'):return self.publish(actor,data)
        if path=='catalog':return {'items':self.catalog(),'user':actor,'model':MODEL,'can_publish':False,'notice':'当前线上 Skill 只读作为基线；保存、恢复和真实模型试测均在独立工作区，不发送微信。'}
        if path=='assemble':return self.assemble(actor,data)
        if path=='assembly':return self.assembly(data.get('id'))
        if path=='restore':return self.restore(actor,data)
        return super().dispatch(actor,path,data)


def handler(studio):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,value,mime='application/json; charset=utf-8'):
            raw=(legacy.encoded(value) if mime.startswith('application/json') else value).encode()
            self.send_response(status)
            for k,v in {'Content-Type':mime,'Content-Length':str(len(raw)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; connect-src 'self'; img-src 'self' data:; media-src 'self' blob: data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"}.items():self.send_header(k,v)
            self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            pages={'/skill-studio/':('teacher-workbench.html','text/html; charset=utf-8'),'/skill-studio/app.js':('teacher-workbench.js','text/javascript; charset=utf-8'),'/skill-studio/style.css':('teacher-workbench.css','text/css; charset=utf-8')}
            pages.update({'/skill-studio/reply-lab/':('reply-lab.html','text/html; charset=utf-8'),'/skill-studio/reply-lab/app.js':('reply-lab.js','text/javascript; charset=utf-8'),'/skill-studio/reply-lab/style.css':('reply-lab.css','text/css; charset=utf-8')})
            if self.path not in pages:self.send(404,{'error':'入口不存在。'});return
            name,mime=pages[self.path];self.send(200,(Path(__file__).parent/name).read_text(encoding='utf8'),mime)
        def do_POST(self):
            try:
                if not self.path.startswith('/skill-studio/api/'):raise legacy.Fault('入口不存在。',404)
                if self.headers.get('X-Skill-Studio')!='1' or not self.headers.get('Content-Type','').startswith('application/json'):raise legacy.Fault('请从 Skill 工作台操作。',403)
                actor=studio.authenticate(self.headers.get('Authorization',''));length=int(self.headers.get('Content-Length','0'))
                limit=12000000 if self.path=='/skill-studio/api/reply-lab/start' else 3000000 if self.path=='/skill-studio/api/reply-lab/asset-save' else 1000000
                if not 0<length<=limit:raise legacy.Fault('请求过大或缺少数据。',413)
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):raise legacy.Fault('请求必须为 JSON 对象。')
                self.send(200,studio.dispatch(actor,self.path[len('/skill-studio/api/'):],data))
            except legacy.Fault as error:self.send(error.status,{'error':str(error)})
            except (ValueError,KeyError,TypeError):self.send(400,{'error':'请求格式错误，请核对所选 Skill、版本与数据。'})
            except Exception:self.send(500,{'error':'操作未确认，请刷新核对工作区记录。'})
    return Handler


if __name__=='__main__':
    studio=TeacherStudio()
    ThreadingHTTPServer(('127.0.0.1',19033),handler(studio)).serve_forever()
