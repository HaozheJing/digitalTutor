"""Update existing generated sidecar bodies via ov CLI, then verify exact readback."""
import argparse,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('config');p.add_argument('revision');p.add_argument('--weeks',type=int,nargs='+',required=True)
    a=p.parse_args();base=ROOT/'build/openviking/courses'/a.revision
    nav=json.loads((base/'navigation.json').read_text(encoding='utf-8'));checks=[]
    def call(label,args):
        f=base/'cli-records'/(label+'.json')
        if not f.exists():
            r=subprocess.run([sys.executable,str(ROOT/'ops/course_cli.py'),a.config],input=json.dumps({'snapshot':a.revision,'label':label,'args':args,'brief':True}),text=True,capture_output=True)
            assert r.returncode==0,r.stderr
        rec=json.loads(f.read_text(encoding='utf-8'))
        assert rec['exit_code']==0 and rec['parsed']['ok'],rec
        return rec['parsed']['result']
    for card in [c for c in nav['cards'] if c['source_week'] in a.weeks]:
        for kind in ['abstract','overview']:
            # Existence and original body are preserved as pre-write evidence.
            call('before-nav-'+kind+'-'+card['unit_id'],[kind,card['scope_uri'],'-o','json'])
            result=call('write-nav-'+kind+'-'+card['unit_id'],['write',card['scope_uri']+'/.'+kind+'.md','--content',card[kind],'--wait','--timeout','60','-o','json'])
            after=call('after-nav-'+kind+'-'+card['unit_id'],[kind,card['scope_uri'],'-o','json'])
            same=after.rstrip('\n')==card[kind].rstrip('\n')
            checks.append({'unit_id':card['unit_id'],'kind':kind,'exact_readback_except_terminal_newline':same,'write_result':result})
            assert same, 'Summary readback differs beyond terminal newline'
    report={'weeks':a.weeks,'passed':all(c['exact_readback_except_terminal_newline'] for c in checks),'checks':checks,
            'note':'Only existing directory L0/L1 sidecar bodies updated; file-level find abstracts remain server-generated.'}
    name='navigation-check-'+'-'.join(f'w{w:02d}' for w in a.weeks)+'.json'
    (base/name).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':name,'passed':report['passed'],'sidecars':len(checks)}))

if __name__=='__main__':main()
