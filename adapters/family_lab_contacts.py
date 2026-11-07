"""Teacher-operated contact discovery and atomic roster binding. No model or send calls."""
import hashlib,json,threading,time
from pathlib import Path
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,build_opener,ProxyHandler,HTTPRedirectHandler
from family_lab import Lab,encode

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def setting(c,key,default):
    row=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(c,key,value):c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,encode(value)))


def contact_key(org,bot,contact):return hashlib.sha256(encode([org,bot,contact]).encode()).hexdigest()[:32]


def initialize(home,lab):
    """Import only the previously authorized private allowlist; never auto-enroll new friends."""
    home=Path(home)
    with lab.transaction() as c:
        if c.execute("SELECT 1 FROM settings WHERE key='contact_roster'").fetchone():return
        routes=list(json.loads((home/'registered-contacts.private.json').read_text()).values())
        bots={(r['orgId'],r['imBotId']) for r in routes}
        if len(bots)!=1:raise ValueError('明确选择一个已接通的企业微信账号后再配置联系人')
        org,bot=next(iter(bots))
        roster={x['family_id']:({'nickname':x['nickname'],'route':json.loads(x['route'])} if x['mode']=='wechat' else None) for x in c.execute('SELECT b.* FROM bindings b JOIN runs r ON r.id=b.run_id WHERE r.active=1')}
        put(c,'contact_account',{'orgId':org,'imBotId':bot})
        put(c,'contact_authorized_routes',routes)
        put(c,'contact_roster',roster)
        lab.audit(c,'contact_directory_initialized',{'source':'previous_private_allowlist','count':len(routes)})


def authorized(lab,route):
    rows=lab.read("SELECT value FROM settings WHERE key='contact_authorized_routes'")
    return bool(rows and route in json.loads(rows[0]['value']))


class Contacts:
    def __init__(self,home,lab,fetch=None):
        self.home=Path(home);self.lab=lab;self.lock=threading.Lock();self.fetch=fetch or self.request
        initialize(home,lab)

    def request(self,path,query):
        cfg=json.loads((self.home/'gateway.private.json').read_text());base=cfg['api_base_url'];origin=urlsplit(base)
        if origin.scheme!='https' or origin.username or origin.query or origin.fragment:raise ValueError('invalid_provider_origin')
        if path not in ('/api/v2/customer/list','/api/v2/getChatId'):raise ValueError('contact_read_api_only')
        req=Request(base.rstrip('/')+path+'?'+urlencode({'token':cfg['expected_token'],**query}))
        try:
            with build_opener(ProxyHandler({}),NoRedirect()).open(req,timeout=20) as response:
                raw=response.read(4*1024*1024+1)
                if len(raw)>4*1024*1024:raise ValueError('response_too_large')
                value=json.loads(raw)
        except Exception:raise ValueError('企微联系人查询失败，请稍后刷新；没有修改绑定或发送消息') from None
        if value.get('errcode')!=0:raise ValueError('企微接口未返回成功，请检查客户查询权限；没有修改绑定')
        return value

    def account(self):return json.loads(self.lab.read("SELECT value FROM settings WHERE key='contact_account'")[0]['value'])

    def refresh(self):
        if not self.lock.acquire(blocking=False):raise ValueError('联系人正在刷新，请稍候')
        try:return self._refresh()
        finally:self.lock.release()

    def _refresh(self):
        account=self.account();rows=[];seen=set();seq=None;seen_seq=set();complete=False
        for page in range(1,21):
            query={'imBotId':account['imBotId'],'coworker':'false','friendshipStatus':3,'pageSize':100,'current':page}
            if seq:query['seq']=seq
            response=self.fetch('/api/v2/customer/list',query);batch=response.get('data')
            if response.get('errcode')!=0 or not isinstance(batch,list):raise ValueError('客户列表格式不符，保留原联系人列表')
            before=len(seen)
            for item in batch:
                bot=item.get('botInfo') or {};info=item.get('imInfo') or {};cid=item.get('imContactId')
                if bot.get('imBotId')!=account['imBotId'] or item.get('friendshipStatus')!=3 or not isinstance(cid,str) or not cid or cid==account['imBotId']:continue
                key=contact_key(account['orgId'],account['imBotId'],cid)
                if key in seen:continue
                seen.add(key)
                rows.append({'id':key,'name':str(item.get('name') or '未命名联系人')[:100],'remark':str(item.get('remark') or info.get('remark') or '')[:120],
                    'avatar':item.get('avatar') if isinstance(item.get('avatar'),str) and item['avatar'].startswith('https://') else None,
                    'imContactId':cid,'friendshipStatus':3,'chatId':item.get('chatId'),'refreshed_at':time.time()})
            next_seq=response.get('next_seq');total=response.get('total')
            if next_seq:
                if next_seq in seen_seq:raise ValueError('客户列表分页未前进，请重试')
                seen_seq.add(next_seq);seq=next_seq
            elif (isinstance(total,int) and page*100>=total) or len(batch)<100:complete=True;break
            elif len(seen)==before:raise ValueError('客户列表分页未前进，请重试')
        if not complete:raise ValueError('客户列表超过本演示系统查询上限，请按托管账号缩小范围')
        with self.lab.transaction() as c:
            put(c,'contact_directory',{'updated':time.time(),'account':account,'items':rows})
            self.lab.audit(c,'contact_directory_refreshed',{'count':len(rows),'binding_changed':False,'model_called':False})
        return self.snapshot()

    def snapshot(self):
        with self.lab.transaction() as c:
            directory=setting(c,'contact_directory',{'updated':None,'items':[]});roster=setting(c,'contact_roster',{})
            account=setting(c,'contact_account',{})
        current=self.lab.current();bindings=self.lab.read('SELECT family_id,nickname,mode,route,version FROM bindings WHERE run_id=?',(current['id'],))
        items=[{k:v for k,v in item.items() if k not in ('chatId','imContactId')}|{'identity_hint':item['imContactId'][-6:]} for item in directory['items']]
        return {'updated':directory['updated'],'items':items,'account_hint':account.get('imBotId','')[-6:],'run_id':current['id'],'paused':bool(current['paused']),
            'bindings':[{'family_id':b['family_id'],'name':b['nickname'],'mode':b['mode'],'version':b['version'],'contact_id':contact_key(*[json.loads(b['route'])[k] for k in ('orgId','imBotId','imContactId')]) if b['mode']=='wechat' else None} for b in bindings],
            'saved_roster_count':sum(v is not None for v in roster.values())}

    def apply(self,run,mapping,versions):
        if not isinstance(mapping,dict) or set(mapping)!={'F01','F02','F03','F04','F05'}:raise ValueError('请为五个家庭分别选择联系人或模拟通道')
        if any(v is not None and (not isinstance(v,str) or len(v)!=32) for v in mapping.values()):raise ValueError('请选择当前好友列表中的联系人')
        selected=[v for v in mapping.values() if v is not None]
        if len(selected)!=len(set(selected)):raise ValueError('同一个微信联系人不能同时绑定两个家庭')
        if not isinstance(versions,dict):raise ValueError('请刷新绑定状态')
        if not self.lock.acquire(blocking=False):raise ValueError('联系人正在更新，请稍候')
        try:
            with self.lab.transaction() as c:self.check_idle(c,run)
            self._refresh() # Recheck current friendship; cached names never grant sending authority.
            directory=json.loads(self.lab.read("SELECT value FROM settings WHERE key='contact_directory'")[0]['value']);account=self.account()
            by_id={x['id']:x for x in directory['items']};roster={}
            for fid,key in mapping.items():
                if key is None:roster[fid]=None;continue
                item=by_id.get(key)
                if not item:raise ValueError('选中的联系人已不在当前好友列表，请刷新后重新选择')
                response=self.fetch('/api/v2/getChatId',{'imBotId':account['imBotId'],'imContactId':item['imContactId']})
                chat=response.get('chatId')
                if response.get('errcode')!=0 or not isinstance(chat,str) or not chat:raise ValueError('尚未取得该好友会话标识，请等待企微同步后重试；不需要对方发消息')
                if item.get('chatId') and item['chatId']!=chat:raise ValueError('联系人会话标识不一致，请刷新核对')
                roster[fid]={'nickname':item['name'],'route':{**account,'imContactId':item['imContactId'],'chatId':chat}}
            with self.lab.transaction() as c:
                self.check_idle(c,run)
                before={x['family_id']:dict(x) for x in c.execute('SELECT * FROM bindings WHERE run_id=?',(run,))}
                if any(versions.get(fid)!=b['version'] for fid,b in before.items()):raise ValueError('绑定已被其他操作更新，请刷新后重选')
                changed=[]
                for fid,value in roster.items():
                    b=before[fid];route=encode(value['route']) if value else 'simulation:'+fid;mode='wechat' if value else 'simulation'
                    if b['route']!=route or b['mode']!=mode:
                        if c.execute('SELECT 1 FROM ingress WHERE run_id=? AND family_id=?',(run,fid)).fetchone() or c.execute('SELECT 1 FROM outbox WHERE run_id=? AND family_id=?',(run,fid)).fetchone() or c.execute('SELECT 1 FROM model_attempts WHERE run_id=? AND family_id=?',(run,fid)).fetchone():
                            raise ValueError('该家庭已有本轮体验记录。请先新建暂停轮次，再更换体验者，避免把旧人的上下文交给新人')
                        changed.append(fid)
                    if value and c.execute("SELECT 1 FROM outbox WHERE route=? AND status IN ('submitting','accepted','unknown')",(route,)).fetchone():raise ValueError('该联系人存在未决回执，请先核对')
                # Deactivate all first so swapping two unused family bindings is atomic.
                c.execute('UPDATE bindings SET enabled=0 WHERE run_id=?',(run,))
                for fid,value in roster.items():
                    b=before[fid];name=value['nickname'] if value else c.execute('SELECT student FROM families WHERE run_id=? AND family_id=?',(run,fid)).fetchone()[0]
                    c.execute('UPDATE bindings SET nickname=?,route=?,mode=?,enabled=1,version=version+? WHERE run_id=? AND family_id=?',(name,encode(value['route']) if value else 'simulation:'+fid,'wechat' if value else 'simulation',int(fid in changed),run,fid))
                    if fid in changed:c.execute('UPDATE families SET epoch=epoch+1 WHERE run_id=? AND family_id=?',(run,fid))
                put(c,'contact_authorized_routes',[x['route'] for x in roster.values() if x]);put(c,'contact_roster',roster)
                self.lab.audit(c,'teacher_bound_contacts',{'mapping':mapping,'changed_families':changed,'model_called':False,'messages_sent':0,'contact_role':'student'},run)
            return {'status':'bound','paused':True,'real_families':len(selected),'messages_sent':0,'model_called':False}
        finally:self.lock.release()

    def check_idle(self,c,run):
        r=c.execute('SELECT * FROM runs WHERE id=? AND active=1',(run,)).fetchone()
        if not r or not r['paused'] or r['mode']!='wechat':raise ValueError('请先暂停实验，并使用微信体验轮次')
        if c.execute("SELECT 1 FROM settings WHERE key='new_round_request' AND json_extract(value,'$.status') IN ('queued','preparing')").fetchone():raise ValueError('新轮次正在创建，请稍候')
        if c.execute('SELECT 1 FROM families WHERE run_id=? AND busy=1',(run,)).fetchone() or c.execute("SELECT 1 FROM model_attempts WHERE run_id=? AND status IN ('running','queued')",(run,)).fetchone():raise ValueError('请等待模型任务结束后绑定')
        if c.execute("SELECT 1 FROM outbox WHERE run_id=? AND status IN ('queued','submitting','accepted','unknown')",(run,)).fetchone():raise ValueError('请等待发送队列和未决回执处理结束后绑定')
