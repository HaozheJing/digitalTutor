"""Scoped OpenViking retrieval; only pinned reviewed cards can authorize images."""
import hashlib,json,os,subprocess
from pathlib import Path
import sql_tools as s
from knowledge_search import CLI


def cli(args):
    config=Path(os.environ.get('TUTOR_EMOJI_OVCLI_CONFIG',str(s.R/'runtime/emoji-ovcli.conf')))
    if not config.is_file():raise RuntimeError('emoji_openviking_unavailable')
    process=subprocess.run([CLI,*args,'-o','json'],env={**os.environ,'OPENVIKING_CLI_CONFIG_FILE':str(config),'NO_COLOR':'1'},capture_output=True,text=True,timeout=45)
    if process.returncode or len(process.stdout)>1000000:raise RuntimeError('emoji_openviking_unavailable')
    for i,ch in enumerate(process.stdout):
        if ch not in '{["':continue
        try:
            value,end=json.JSONDecoder().raw_decode(process.stdout[i:])
            if process.stdout[i+end:].strip():continue
            if isinstance(value,dict):
                if value.get('ok') is False:raise RuntimeError('emoji_openviking_unavailable')
                return value.get('result',value.get('data',value))
            return value
        except ValueError:pass
    raise RuntimeError('emoji_openviking_unavailable')


def search(query,limit=3):
    if not isinstance(query,str) or not 1<=len(query.strip())<=500 or type(limit) is not int or not 1<=limit<=5:raise ValueError('invalid_emoji_query')
    from emoji_assets import resolve
    import emoji_policy
    file=s.R/'config/emoji-assets.json';registry=json.loads(file.read_text()) if file.exists() else {}
    info=registry.get('retrieval')
    if not info:return {'status':'unavailable','matches':[],'reason':'emoji_knowledge_not_configured'}
    available=emoji_policy.availability()
    if not available['allowed']:return {'status':'suppressed','matches':[],'policy':available,'instruction':'本轮只发文字，不需要继续找图。'}
    try:
        result=cli(['find','沟通场景：'+query.strip(),'--uri',info['uri'],'--limit',str(min(20,limit*3)),'-L','2'])
        if not isinstance(result,dict) or not isinstance(result.get('resources'),list):raise RuntimeError('invalid_retrieval')
        assets=[a for a in registry['assets'] if a.get('status')=='approved' and a.get('knowledge')]
        allowed={u:a for a in assets for u in [a['knowledge']['uri'],a['knowledge']['image_uri']]}
        matches=[];seen=set();changed=[]
        for hit in result['resources']:
            a=allowed.get(hit.get('uri'));ident=a['asset_id'] if a else None
            if not a or ident in seen:continue
            seen.add(ident);k=a['knowledge']
            raw=cli(['read',k['uri']]);raw=raw.get('content') if isinstance(raw,dict) else raw
            if not isinstance(raw,str) or hashlib.sha256(raw.encode()).hexdigest()!=k['sha256']:
                changed.append(ident);continue
            card=json.loads(raw.split('```json\n',1)[1].split('\n```',1)[0])
            if card['asset_id']!=ident or card['image_sha256']!=a['sha256'] or card['status']!='approved':
                changed.append(ident);continue
            resolve([{'asset_id':ident,'version':registry['version']}])
            matches.append({'asset_id':ident,'version':registry['version'],'source_uri':k['uri'],'document_sha256':k['sha256'],'live_read':True,
                **{key:card.get(key) for key in ['caption','visible_text','visual_description','source_category','intent','intensity','suitable','avoid','positive_example','negative_example','mime','animated']}})
            if len(matches)>=limit:break
        granted=emoji_policy.grant(matches,query) if matches else False
        return {'status':'ok' if matches else ('changed' if changed else 'no_match'),'matches':matches,'revision':info['revision'],
                'selection_authorized_for_current_turn':granted,'changed_assets':changed,'policy':available,
                'instruction':'结合本轮语境及正反例自主选0或1张，以emoji_refs附在原交付里，不单独发图。'}
    except Exception:
        return {'status':'unavailable','matches':[],'reason':'emoji_openviking_read_failed','instruction':'仅回复文字，不回退旧卡或猜测素材ID。'}
