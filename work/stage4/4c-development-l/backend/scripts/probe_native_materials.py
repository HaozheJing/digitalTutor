"""Server-local native Skill and OpenViking reads; no model/API calls."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

parser=argparse.ArgumentParser(); parser.add_argument('--run',required=True)
args=parser.parse_args(); assert args.run.replace('-','').replace('_','').isalnum()
project=Path.cwd(); output=project/'evaluations/backend-stage3'/args.run
profile=project/'state/backend-stage3'/args.run/'material-profile'
profile.mkdir(parents=True,exist_ok=True); output.mkdir(parents=True,exist_ok=True)
os.environ['HERMES_HOME']=str(profile)
os.environ['HERMES_DISABLE_LAZY_INSTALLS']='1'
(profile/'config.yaml').write_text('skills:\n  external_dirs: '+json.dumps([str(project/'agent/skills')])+'\n'
    'security:\n  allow_lazy_installs: false\n',encoding='utf-8')
os.chdir(profile); sys.path.insert(0,str(project/'vendor/hermes'))
from digital_tutor.skill_loader import SkillCatalog
from digital_tutor.knowledge import KnowledgeCatalog, KnowledgeBinding, OpenVikingCLI
from digital_tutor.domain import BusinessError

skills=SkillCatalog(project/'agent')
reads=[]
for role,name in [('service','explain-lesson'),('planner_daily','plan-proactive-service'),('service','recommend-catch-up-reading')]:
    r=skills.read(role,name)
    reads.append({'role':role,'skill':r['canonical_skill_id'],'requested':name,'sha256':r['sha256'],
                  'native':r['native_reader'],'guides':[{'id':g['guide_id'],'sha256':g['sha256']} for g in r['required_guides']]})
course=json.loads((project/'build/openviking/courses/source-c764d8b325cc-nosplit-v2/access-map.json').read_text())
product_path=project/'build/openviking/products/reading-camp/source-52e8a8289d17-merged-v1/access-map.json'
product=json.loads(product_path.read_text())
catalog=KnowledgeCatalog([course,product],OpenVikingCLI(project/'runtime/openviking/bin/ov',
    project/'runtime/openviking/enterprise-ovcli.conf'))
unit=next(u for u in course['units'] if u.get('source_day') in (1,'1') and u.get('record_type')=='lesson')
nextunit=next(u for u in course['units'] if u.get('source_day') in (2,'2') and u.get('record_type')=='lesson')
binding=KnowledgeBinding(course['revision'],(unit['unit_id'],),simulation=True)
r=catalog.read('service',binding,unit['unit_id'])
knowledge=[{'kind':'course','id':r['document_id'],'sha256':r['content_sha256'],'characters':len(r['content']),
            'simulation_binding':True,'verified_current_business':False}]
try:
    catalog.read('service',binding,nextunit['unit_id'])
    raise AssertionError('Adjacent lesson escaped scope')
except BusinessError as exc:
    assert exc.code=='KNOWLEDGE_SCOPE_FORBIDDEN'
p=next(u for u in product['units'] if u.get('retrieval_scope')!='reference')
r=catalog.read('service',KnowledgeBinding(product['revision'],(p['unit_id'],),simulation=True),p['unit_id'])
knowledge.append({'kind':'product','id':r['document_id'],'sha256':r['content_sha256'],'characters':len(r['content']),
                  'verified_current_business':False})
result={'passed':True,'real_model_used':False,'internal_materials_sent_to_model':False,
        'skill_reads':reads,'knowledge_reads':knowledge,'adjacent_lesson_rejected':True,
        'native_profile':str(profile),'tested_at':datetime.now(timezone.utc).isoformat()}
(output/'native-materials.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':True,'skills':len(reads),'knowledge':len(knowledge),'model_calls':0}))
