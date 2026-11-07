"""Public PAYG equivalent, not Coding Plan credits or an invoice."""
import datetime as dt,json
TZ=dt.timezone(dt.timedelta(hours=8))
VERSION='ark-public-20261030-v1'
SOURCE='https://docs.volcengine.com/docs/ark/model-pricing?lang=zh'

def estimate(model,usage,finished):
    if not isinstance(usage,dict):return None,'missing_usage'
    prompt=usage.get('prompt_tokens',usage.get('input_tokens'))
    output=usage.get('completion_tokens',usage.get('output_tokens'))
    details=usage.get('prompt_tokens_details') or usage.get('input_tokens_details') or {}
    cached=details.get('cached_tokens',usage.get('prompt_cache_hit_tokens',0))
    if any(type(x) is not int or x<0 for x in (prompt,output,cached)) or cached>prompt:return None,'incomplete_usage'
    wall=dt.datetime.fromtimestamp(finished,TZ);hour=wall.hour+wall.minute/60
    peak=wall.weekday()<5 and (9<=hour<12 or 14<=hour<18)
    rates={'deepseek-v4.1-flash-economy':(1.60,.032,6.40) if peak else (.80,.016,3.20),
           'deepseek-v4-pro':(9,.30,27)}.get(model)
    if rates is None:return None,'unpriced_model'
    return round(((prompt-cached)*rates[0]+cached*rates[1]+output*rates[2])/1_000_000,9),VERSION+(':peak' if peak else ':offpeak')

def summary(lab,run):
    rows=lab.read('SELECT * FROM model_attempts WHERE run_id=?',(run,));parts=lab.read("SELECT DISTINCT id,family_id,mode FROM outbox WHERE run_id=? AND status='sent'",(run,))
    groups=[]
    for fid in sorted({x['family_id'] for x in rows}|{x['family_id'] for x in parts}):
        requests=[x for x in rows if x['family_id']==fid];cost=0;missing=0;unpriced=0;blocked=0
        for x in requests:
            if x['status'] in ('queued','running'):continue
            if (x['error'] or '').endswith(':before_provider'):blocked+=1;continue
            value,reason=estimate(x['model'],json.loads(x['usage']) if x['usage'] else None,x['finished'] or x['created'])
            if value is None:
                if reason=='unpriced_model':unpriced+=1
                else:missing+=1
            else:cost+=value
        sent=sum(x['family_id']==fid for x in parts)
        real_sent=sum(x['family_id']==fid and x['mode']=='wechat' for x in parts)
        groups.append(dict(family_id=fid,requests=len(requests),blocked_before_provider=blocked,known_cost_cny=round(cost,6),missing_usage=missing,unpriced=unpriced,sent_parts=sent,real_sent_parts=real_sent,cost_per_sent_part=round(cost/real_sent,6) if real_sent else None))
    return dict(basis='公开按量 API 等值估算，非套餐积分或账单；缺失项未计入已知费用；单位成本仅用真实微信 sent 分片',source=SOURCE,price_version=VERSION,families=groups,total_known_cost_cny=round(sum(x['known_cost_cny'] for x in groups),6),requests=len(rows),missing_usage=sum(x['missing_usage'] for x in groups),unpriced=sum(x['unpriced'] for x in groups),sent_parts=len(parts),real_sent_parts=sum(x['mode']=='wechat' for x in parts))
