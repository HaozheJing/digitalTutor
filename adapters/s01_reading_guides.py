"""Pinned S01 guide cards and receipt-grounded conversation context; no answer key."""
from pathlib import Path
from contextlib import closing
import hashlib,json,sqlite3
import sql_tools as s,test_mode as tm
REGISTRY=Path(__file__).with_suffix('.json')
PREFIX='https://experiment.invalid/reading-guide/'
GUIDANCE=('已送达导读单中的问题可能是孩子当前回答的对象。结合孩子原话、题号、课次与发送日期理解，不把短答案当作无来由的新话题；'
          '若多张导读单都可能对应，先确认是哪一题，不硬套最近一张。标准答案尚未接入，不凭模型记忆判对错、打分或声称标准答案已核验。'
          '这是孩子在回应已发的问题，先接住这次选择或理由；标准答案待接入时不作对错判定、评分，也不用“答得真好、真准”等话暗示已经判对。'
          '确需解释时可以按需读取对应课程正文，说明实际资料依据；未经业务审核的材料不升级为已核验答案，不凭一条描述编造原因。'
          '孩子只提交人物、选项或答案时，只承接这个选择，不据此夸读得细、认真、观察仔细或理解到位；题号说清楚不证明阅读表现。'
          '这种作答不自动转为讲解或训练：不追加回读、找依据、解释理由、复述、举一反三、另一题或汇报，也不改成可以、试试等建议。'
          '只有孩子明确提问或求助才补充相应解释；答案待接入时，不用书中结论间接判对。所选业务Skill的自然延伸或讲解流程也遵循这一区分。'
          '不向家庭讲内部答案接入流程。图片发送不代表孩子完成阅读或作答，图上进度是素材序号。')

def required():
    return __import__('service_workflow').planning_contract('remind-reading').get('required_reading_guide') is True

def table(c):
    c.execute('CREATE TABLE IF NOT EXISTS s01_guide_deliveries(action_key TEXT PRIMARY KEY,run_id TEXT,uid TEXT,day TEXT,asset_id TEXT,sha256 TEXT,public_url TEXT,card TEXT,created_at TEXT)')

def verify(asset):
    path=s.R/asset['path'];base=(s.R/'assets/reading-guides').resolve()
    if path.is_symlink() or path.resolve()!=path.absolute() or not path.resolve().is_relative_to(base) or not path.is_file():raise ValueError('导读单原图缺失或路径不符')
    raw=path.read_bytes()
    if len(raw)!=asset['size'] or hashlib.sha256(raw).hexdigest()!=asset['sha256']:raise ValueError('导读单原图哈希不符')
    if not (asset['mime']=='image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n') or asset['mime']=='image/jpeg' and raw.startswith(b'\xff\xd8')):raise ValueError('导读单格式不符')
    return raw

def select(schedule):
    if schedule.get('status')!='scheduled':return {'status':'unavailable','reason':'没有已核实的今日课次'}
    rows=[x for x in json.loads(REGISTRY.read_text())['assets'] if x['class_type']==schedule.get('class_type') and x['lesson_no']==schedule.get('lesson_no')]
    if len(rows)!=1:return {'status':'unavailable','reason':'没有唯一对应的导读单，不借用别课图片'}
    try:verify(rows[0])
    except ValueError as error:return {'status':'unavailable','reason':str(error)}
    return {'status':'ready','asset':rows[0]}

def public(asset):
    return {k:v for k,v in asset.items() if k not in ('path','size','transcription')}

def planned(schedule,day):
    if not required():return {'status':'not_required'}
    value=select(schedule)
    if value['status']!='ready':return value
    return {'status':'ready','card':public(value['asset']),'delivery_status':'not_sent','delivery_owner':'host_after_invitation','answer_status':'pending','guidance':'本次文字后程序会配发对应原图；正文仍只作阅读邀请，不重复图中题目或追加作答要求。只有图片分件sent后才认为图已送达。'}

def bind(c,key,day,asset):
    import experiment_runtime as er
    manifest=er.manifest()
    raw=verify(asset);table(c);state=tm.state()
    if manifest:url=PREFIX+manifest['experiment_run_id']+'/'+asset['sha256']+Path(asset['path']).suffix
    else:url=__import__('manual_media').publish(asset,raw)
    c.execute('INSERT INTO s01_guide_deliveries VALUES(?,?,?,?,?,?,?,?,?)',(key,state['run_id'],state['uid'],day,asset['id'],asset['sha256'],url,s.encoded(asset),state['as_of']))
    __import__('wechat_test').queue_images(c,key,[url])
    return url

def registered(url):
    if not url.startswith((PREFIX,__import__('manual_media').BASE)) or not tm.state():return False
    with closing(sqlite3.connect(s.DB)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='s01_guide_deliveries'").fetchone():return False
        state=tm.state();return bool(c.execute('SELECT 1 FROM s01_guide_deliveries WHERE public_url=? AND run_id=? AND uid=?',(url,state['run_id'],state['uid'])).fetchone())

def authorize(c,key,body):
    if not body.startswith((PREFIX,__import__('manual_media').BASE)):return False
    state=tm.state();manifest=__import__('experiment_runtime').manifest()
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='s01_guide_deliveries'").fetchone():
        if body.startswith(PREFIX):raise ValueError('缺少导读单绑定')
        return False
    row=c.execute('SELECT action_key,card,public_url FROM s01_guide_deliveries WHERE run_id=? AND uid=? AND public_url=?',(state['run_id'],state['uid'],body)).fetchone()
    if not row and body.startswith(__import__('manual_media').BASE):return False
    if not row or key!=state['run_id']+':'+row[0]+':image:0':raise ValueError('图片不属于这次S01交付')
    asset=json.loads(row[1]);verify(asset)
    current=[x for x in json.loads(REGISTRY.read_text())['assets'] if x['id']==asset['id']]
    if current!=[asset]:raise ValueError('导读单版本变化，停止旧交付')
    if manifest:
        if not body.startswith(PREFIX+manifest['experiment_run_id']+'/'):raise ValueError('图片轮次不符')
    elif not (__import__('manual_workbench').verify() or __import__('family_lab_bridge').verify()) or body!=__import__('manual_media').url(asset):raise ValueError('真实题图绑定不符')
    return True

def history(day):
    state=tm.state()
    if not state:return None
    with closing(sqlite3.connect(s.DB)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='s01_guide_deliveries'").fetchone():return None
        rows=c.execute('SELECT g.action_key,g.day,g.card,g.public_url,d.status,g.created_at FROM s01_guide_deliveries g JOIN deliveries d ON d.action_key=g.action_key WHERE g.run_id=? AND g.uid=? AND g.day<=? ORDER BY g.created_at DESC,g.rowid DESC',(state['run_id'],state['uid'],day)).fetchall()
        sent=[];pending=[]
        for key,date,card,url,status,at in rows:
            part=c.execute('SELECT o.id,o.status FROM test_outbox_parts p JOIN test_outbox o ON o.id=p.operation_id WHERE p.action_key=? AND o.message_type=6 AND o.body=?',(key,url)).fetchone()
            image_status=part[1] if part else 'not_sent';asset=json.loads(card)
            item={'action_key':key,'day':date,'created_at':at,'asset_id':asset['id'],'sha256':asset['sha256'],'lesson_no':asset['lesson_no'],'book':asset['book'],'image_status':image_status,'delivery_status':status,'image_part_id':part[0] if part else None}
            if image_status=='sent':
                item.update(questions=asset['questions'],recommended_pages=asset['recommended_pages'],answer_status='pending',printed_progress_is_child_completion=False);sent.append(item)
            else:pending.append(item)
        if not rows:return None
        return {'sent_guides':sent[:3],'earlier_sent_count':max(0,len(sent)-3),'pending_guides':pending[:3],'guidance':GUIDANCE,'answer_key_status':'pending'}
