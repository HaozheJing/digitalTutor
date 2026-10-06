"""Create an explicit, reviewed backup inventory; never stage whole work/state trees."""
from pathlib import Path
import collections
import hashlib
import json
import os
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
BASE = ROOT / 'work/stage4/intraday-implementation-20261005'
LROOT = ROOT / 'work/stage4/4c-development-l'
MANIFEST = OUT / 'git-backup-manifest.json'
AUDIT = OUT / 'content-scan.json'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def sensitive_findings(path, raw):
    text = raw.decode('utf-8-sig')
    patterns = {
        'private_key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        'provider_key': r'\bsk-[A-Za-z0-9_-]{20,}',
        'github_token': r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})',
        'runtime_capability': r'\bcap-[0-9a-f]{32}\b',
        'bearer_value': r'(?i)Bearer\s+[A-Za-z0-9_./+=-]{24,}',
        'literal_credential': r'''(?i)["']?(?:api_key|password|access_token|auth_token|secret_key|capability_token)["']?\s*[:=]\s*["'][A-Za-z0-9_./+=-]{24,}["']''',
        'jwt_value': r'\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}',
    }
    findings = []
    for kind, pattern in patterns.items():
        for match in re.finditer(pattern, text):
            findings.append({'path': path, 'kind': kind, 'line': text.count('\n', 0, match.start()) + 1})
    known = os.environ.get('DIGITALTUTOR_SSH_PASSWORD')
    if known and known in text:
        findings.append({'path': path, 'kind': 'known_ssh_credential'})
    return findings


def selection():
    paths = {ROOT / '.gitignore', ROOT / '.gitattributes'}
    for folder in ('backend', 'agent'):
        paths.update(p for p in (ROOT / folder).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and not p.name.endswith('.pyc'))
    frozen = json.loads((BASE / 'intraday-l-test15/source-manifest.json').read_text(encoding='utf8'))
    assert len(frozen) == 189
    for name, expected in frozen.items():
        p = LROOT / name
        assert sha(p.read_bytes()) == expected, 'Frozen L drift: ' + name
        paths.add(p)
    paths.update((ROOT / 'docs').glob('*.md'))
    paths.update((LROOT / 'docs').glob('*.md'))
    for folder in ('work/design-inputs', 'work/stage4/intraday-design-20261005', 'work/stage4/plan-research-20261005'):
        paths.update((ROOT / folder).glob('*.md'))
    material = ROOT / 'build/openviking/courses/source-c764d8b325cc-nosplit-v2'
    paths.update(material / name for name in ('access-map.json', 'manifest.json', 'navigation.json'))
    for folder in ('content', 'navigation'):
        paths.update(p for p in (material / folder).rglob('*') if p.is_file())
    for name in ('prepare_course_resources.py', 'prepare_course_nosplit.py', 'build_course_navigation.py',
                 'finalize_course_nosplit.py', 'finalize_course_nosplit_local.py', 'finalize_course_resources.py',
                 'course_cli.py', 'publish_course_navigation.py'):
        paths.add(ROOT / 'ops' / name)
    for name in ('service-skills-20261003T013936Z.json', 'stage3-materials-files-20261004.json'):
        paths.add(ROOT / 'deployments' / name)
    paths.add(ROOT / 'work/stage4/remote.py')
    patterns = ('engineering-native06*.md', 'engineering-native06*.json', 'semantic-native06*.md',
                'semantic-native06*.json', 'engineering-test15-freeze.*', 'implementation-scope-final*',
                'test15-model-runs.json', 'native06*assembly.json', 'native06*manifest.json',
                'native06-processes.json', 'native06-final-server-audit.json', '原文对话-test15.md')
    for pattern in patterns:
        paths.update(BASE.glob(pattern))
    for group in ('native06-intraday-complete', 'native06-daily-complete', 'native06-behavior-complete'):
        paths.update((BASE / group).rglob('*.json'))
    for name in ('run_frozen.py', 'sync_l.py', 'export_evidence.py', 'summarize_exports.py',
                 'build_review_dialogues.py', 'run_native06.sh'):
        paths.add(BASE / name)
    paths.add(BASE / 'intraday-l-test15/source-manifest.json')
    (OUT / 'test15-unit-tests.txt').write_bytes((BASE / 'intraday-l-test15/tests.log').read_bytes())
    paths.update(p for p in OUT.glob('*.py'))
    paths.update(OUT.glob('server-source-audit-*.json'))
    paths.add(OUT / 'test15-unit-tests.txt')
    for p in paths:
        assert p.is_file() and not p.is_symlink(), 'Missing/linked selected file: ' + str(p)
        assert p.suffix not in {'.zip', '.sqlite', '.db', '.xlsx', '.log', '.pem', '.key'}
        assert not {'state', 'runtime', 'vendor', '.git', '__pycache__'} & set(p.relative_to(ROOT).parts)
    return sorted(paths)


if __name__ == '__main__':
    files = selection()
    findings = []
    inventory = {}
    for path in files:
        name = path.relative_to(ROOT).as_posix();raw = path.read_bytes()
        findings.extend(sensitive_findings(name, raw))
        inventory[name] = {'sha256': sha(raw), 'bytes': len(raw)}
    AUDIT.write_text(json.dumps({'selected_files': len(files), 'findings': findings,
        'scope': 'Selected working-tree text only; staged blob review still required.'}, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'selected_files': len(files), 'bytes': sum(v['bytes'] for v in inventory.values()),
        'findings': findings}, ensure_ascii=False))
    if findings:
        raise SystemExit('Review findings before staging; secret values intentionally suppressed')
    prior = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    result = {'created_date': '2026-10-06', 'source_head_before_backup': prior,
        'remote': 'https://github.com/HaozheJing/digitalTutor.git', 'branch': 'main',
        'scope': 'H source + immutable L test15 source + pinned knowledge + selected docs and sanitized evidence',
        'frozen_l_files': 189, 'file_count': len(inventory), 'files': inventory,
        'self_excluded': 'This manifest and scan/receipt files are excluded from their own content hashes.',
        'not_backed_up': ['credentials', 'runtime profiles', 'databases', 'real learning workbook',
                          'raw runtime logs', 'unreviewed archives', 'vendor and virtual environments',
                          'other historical untracked work'],
        'test_log_copy': {'source': 'work/stage4/intraday-implementation-20261005/intraday-l-test15/tests.log',
                          'target': 'work/handoff-20261006/test15-unit-tests.txt'}}
    MANIFEST.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    names = list(inventory) + [MANIFEST.relative_to(ROOT).as_posix(), AUDIT.relative_to(ROOT).as_posix()]
    (ROOT / 'tmp/handoff-20261006').mkdir(parents=True, exist_ok=True)
    (ROOT / 'tmp/handoff-20261006/stage-paths.nul').write_bytes(('\0'.join(names) + '\0').encode('utf8'))
    print(json.dumps({'inventory_written': True, 'stage_paths': len(names)}, ensure_ascii=False))
