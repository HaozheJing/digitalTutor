"""Media preparation with optional real providers and durable mock-channel receipts."""
import importlib,os,re,hashlib,json
from typing import Protocol,Literal
from pydantic import Field
from .domain import Strict,ID,require
from .storage import digest

class MediaRequest(Strict):
    request_id: ID = Field(description='本次媒体准备操作的唯一编号；与发送、进展报告等其他操作使用不同编号。同一次生成重试才沿用本编号，例如run-id:audio:invitation。')
    content: str = Field(min_length=1,max_length=10000,description='语音的完整讲稿，或图片的内容与构图说明。这里只准备媒体，不发送、不算完成。')
    event_id: ID | None = None
    part_id: ID | None = None

class ImageRequest(MediaRequest):
    content: str = Field(min_length=1,max_length=6000,description='随图片一起交付、可展开阅读的卡片文字，含必要课表等事实；不得包含构图和制作指令。')
    caption: str = Field(min_length=1,max_length=600,description='聊天中直接展示的简短随图正文，通常2至3句；发送时body必须等于caption。')
    design_prompt: str = Field(min_length=1,max_length=4000,description='仅供生图的视觉设计说明；不在家庭聊天中展示，不新增事实。图片文字以content为准。')

class MediaProvider(Protocol):
    def generate_audio(self, *, content:str, idempotency_key:str) -> dict: ...
    def generate_image(self, *, content:str, idempotency_key:str) -> dict: ...

class UnconfiguredProvider:
    def generate_audio(self, **kwargs):return {'status':'placeholder'}
    def generate_image(self, **kwargs):return {'status':'placeholder'}

def content_delivery_enabled(app,family):
    return app.store.family(family)['mock']==1 and any(p.get('enabled') is True and p.get('policy')=='audio-image-content-delivery-v1' for p in app.store.list('lab_media_policy',family))

def content_artifact_valid(app,family,artifact,operations):
    return content_delivery_enabled(app,family) and artifact.get('representation')=='content_only' and any(
      o['id']==artifact.get('operation_id') and (o.get('media') or {}).get('status')=='placeholder' and
      (o.get('media') or {}).get('kind')==artifact.get('kind') and bool(o.get('body','').strip()) and
      hashlib.sha256(o['body'].encode('utf8')).hexdigest()==artifact.get('sha256') for o in operations)

def provider(app):
    if getattr(app,'media_provider',None) is not None:return app.media_provider
    setting=os.environ.get('DIGITAL_TUTOR_MEDIA_PROVIDER','')
    if not setting:return UnconfiguredProvider()
    module,factory=setting.split(':',1)
    app.media_provider=getattr(importlib.import_module(module),factory)()
    return app.media_provider

def prepare(app,token,args,kind):
    cmd=(ImageRequest if kind=='image' else MediaRequest).model_validate(args)
    def validate_scope(family,binding):
        require(app.store.family(family)['mock']==1,'REAL_CHANNEL_DISABLED','媒体预留工具仅供实验渠道')
        from .family_expression import require_family_expression
        require_family_expression(cmd.content)
        if kind=='image':require_family_expression(cmd.caption)
        from .family_expression import require_family_address
        require_family_address(app,family,cmd.caption if kind=='image' else cmd.content,binding)
        revision=None
        if cmd.event_id:
            e=app.event(family,cmd.event_id);revision=e['requirement_revision']
            from .first_lesson_check import require_verified
            require_verified(app,family,e)
            from .family_expression import require_course_date
            require_course_date(app,e,cmd.content)
            if kind=='image':require_course_date(app,e,cmd.caption)
            require(cmd.part_id in e['completion']['required_part_ids'],'MEDIA_SCOPE','媒体须关联原交付部分')
            require(not binding.get('event_id') or binding['event_id']==cmd.event_id,'MEDIA_SCOPE','只能准备当前事项的媒体')
            from .action_completion import require_response_expression
            require_response_expression(app,family,e,cmd.part_id)
        else:require(cmd.part_id is None,'MEDIA_SCOPE','part_id须与event_id一起使用')
        return revision
    # Validate before spending, then release SQLite's write lock during external I/O.
    # The host heartbeat and newer family inputs must remain able to commit.
    with app.store.transaction() as db:
        family,binding=app.auth(token,{'service'},allow_inactive=True)
        old=db.execute('SELECT digest,result FROM receipts WHERE family_id=? AND request_id=?',(family,cmd.request_id)).fetchone()
        if old:
            require(old['digest']==digest({'command':'media.generate_'+kind,'payload':cmd.model_dump()}),'IDEMPOTENCY_CONFLICT','相同请求身份不能携带不同内容')
            return {**json.loads(old['result']),'outcome':'duplicate'}
        app.auth(token,{'service'})
        from .steering import require_current
        require_current(app,family,binding)
        revision=validate_scope(family,binding)
        key='media:'+digest([family,kind,cmd.request_id])
    provider_content=cmd.content
    if kind=='image':
        provider_content=cmd.design_prompt+'\n请制作清晰易读的中文图卡，大字号、分区、充足留白。装饰插画不得含任何文字或伪字；书封、背景、角标不要小字。不要添加下面文字以外的事实、姓名、成绩或承诺。只呈现以下卡片文字，不呈现设计指令：\n'+cmd.content
    try:result=getattr(provider(app),'generate_'+kind)(content=provider_content,idempotency_key=key)
    except Exception:result={'status':'failed','reason':'provider_failed'}
    def apply(family,binding):
        current_revision=validate_scope(family,binding)
        require(current_revision==revision,'MEDIA_SCOPE','生成过程中交付要求已变化')
        require(isinstance(result,dict) and result.get('status') in {'ready','placeholder','failed'},'MEDIA_PROVIDER_INVALID','供应商结果不符合预留协议')
        if result['status']=='ready':
            url=result.get('url','')
            local=isinstance(url,str) and bool(re.fullmatch(r'/media/[a-f0-9]{64}\.(png|wav)',url))
            require(isinstance(url,str) and (url.startswith('https://') or local),'MEDIA_PROVIDER_INVALID','成品须有HTTPS或实验媒体资源地址')
            if local:
                require(url.split('/')[-1].split('.')[0]==result.get('sha256'),'MEDIA_PROVIDER_INVALID','本地媒体地址须匹配内容校验值')
            require(bool(re.fullmatch('[a-f0-9]{64}',result.get('sha256',''))),'MEDIA_PROVIDER_INVALID','须有真实媒体内容SHA256')
            require(str(result.get('mime_type','')).startswith('audio/' if kind=='audio' else 'image/'),'MEDIA_PROVIDER_INVALID','媒体格式不匹配')
            if kind=='audio':require(type(result.get('duration_seconds')) in (int,float) and 0<result['duration_seconds']<=60,'MEDIA_PROVIDER_INVALID','语音须有0至60秒的真实时长')
        asset={k:result[k] for k in ('url','sha256','mime_type','duration_seconds') if result['status']=='ready' and k in result}
        return app.store.put('media_draft',key,family,{'kind':kind,'status':result['status'],'content':cmd.content,
          **({'caption':cmd.caption,'design_prompt':cmd.design_prompt,'presentation_version':2} if kind=='image' else {}),
          'event_id':cmd.event_id,'part_id':cmd.part_id,'requirement_revision':revision,'created_at':app.clock.iso(),
          'asset':asset,'provider_error':result.get('reason') if result['status']=='failed' else None,
          'notice':'准备媒体不等于发送；使用wechat.send_message引用media_id。ready为真实成品，placeholder为内容占位，failed为生成失败。','media_id':key})
    return app.write(token,{'service'},'media.generate_'+kind,cmd.model_dump(),apply)

def for_send(app,family,cmd):
    """Metadata is persisted with the real mock operation, never inferred as successful generation."""
    if cmd.media_id:
        d=app.store.get('media_draft',cmd.media_id,family)
        require(d.get('caption',d['content'])==cmd.body,'MEDIA_CONTENT_CHANGED','发送正文须与图片caption或语音讲稿一致')
        require(d['status']!='failed','MEDIA_NOT_READY','媒体生成失败，不能作为成品发送；保留原责任并说明可用安排')
        require(d.get('event_id')==cmd.event_id and d.get('part_id')==cmd.part_id,'MEDIA_SCOPE','媒体不能跨事项或交付部分使用')
        if cmd.event_id:require(d['requirement_revision']==app.event(family,cmd.event_id)['requirement_revision'],'MEDIA_SCOPE','媒体要求版本已变化')
        return {**{k:d[k] for k in ('media_id','kind','status','content','asset')},
          **({k:d[k] for k in ('caption','presentation_version')} if d.get('presentation_version')==2 else {}),
          'content_delivery':d['status']=='placeholder' and content_delivery_enabled(app,family)}
    if cmd.event_id:
        from .action_completion import RULES
        e=app.event(family,cmd.event_id);rule=RULES.get(e.get('service_action_id'),{})
        contracts=[c for c in app.store.list('action_contract',family) if c['event_id']==cmd.event_id]
        kinds={{**rule.get('asset_slots',{}),**c.get('asset_slots',{})}.get(slot) for c in contracts for slot,part in c['slot_parts'].items() if part==cmd.part_id}
        kinds.discard(None)
        if len(kinds)==1 and next(iter(kinds)) in {'audio','image'}:
            return {'media_id':None,'kind':next(iter(kinds)),'status':'placeholder','content':cmd.body,'asset':{},'content_delivery':content_delivery_enabled(app,family)}
    return None

def record_artifact(app,family,op):
    media=op.get('media') or {}
    if not op.get('event_id'):return
    content_only=media.get('status')=='placeholder' and media.get('kind') in {'audio','image'} and content_delivery_enabled(app,family) and bool(op.get('body','').strip())
    if media.get('status')!='ready' and not content_only:return
    if op.get('stage') not in {'sent','delivered','read'}:return
    from .action_completion import RULES
    e=app.event(family,op['event_id']);rule=RULES.get(e.get('service_action_id'),{})
    for c in app.store.list('action_contract',family):
        if c['event_id']!=e['id']:continue
        for slot,part in c['slot_parts'].items():
            if part==op['part_id'] and {**rule.get('asset_slots',{}),**c.get('asset_slots',{})}.get(slot)==media['kind']:
                rid='media-artifact:'+digest([op['id'],slot])
                if any(a['id']==rid for a in app.store.list('action_artifact',family)):continue
                app.store.put('action_artifact',rid,family,{'event_id':e['id'],'requirement_revision':op['requirement_revision'],
                  'slot':slot,'kind':media['kind'],'host_verified':True,'operation_id':op['id'],**({'representation':'content_only','sha256':hashlib.sha256(op['body'].encode('utf8')).hexdigest(),'policy':'audio-image-content-delivery-v1'} if content_only else media['asset'])})

SPECS={
 'media.generate_audio':({'service'},MediaRequest,'合成可播放语音。传入直接对家庭说的完整讲稿（单条不超过60秒，建议180字以内），不朗读构图、算法或内部记录。ready代表真实音频；须用wechat.send_message携带media_id发送。placeholder只代表内容，failed不是成品。准备不授予发送权限。'),
 'media.generate_image':({'service'},ImageRequest,'生成真实图片：content为可展开阅读的卡片文字，caption为短聊天正文，design_prompt仅用于制作。ready后用wechat.send_message携带media_id、body=caption发送。图片配套文字随媒体保留，内容核验可引用该操作media.content；制作说明不作为交付证据。placeholder仅占位，failed不可当成品发送。准备不授予发送权限。')}
HANDLERS={'media.generate_audio':lambda a,t,x:prepare(a,t,x,'audio'),'media.generate_image':lambda a,t,x:prepare(a,t,x,'image')}
