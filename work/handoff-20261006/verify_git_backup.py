"""Verify staged backup bytes and detect secrets without printing their values."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
from prepare_git_backup import ROOT, OUT, BASE, LROOT, sensitive_findings

manifest = json.loads((OUT / 'git-backup-manifest.json').read_text(encoding='utf8'))
index = set(p for p in subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0') if p)
findings = [];mismatches = [];checked = 0
for name, expected in manifest['files'].items():
    if name not in index:
        mismatches.append({'path': name, 'reason': 'not_in_index'});continue
    raw = subprocess.check_output(['git', 'show', ':' + name], cwd=ROOT)
    if hashlib.sha256(raw).hexdigest() != expected['sha256']:
        mismatches.append({'path': name, 'reason': 'index_sha_differs_from_inventory'})
    if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected['sha256']:
        mismatches.append({'path': name, 'reason': 'working_tree_changed_after_inventory'})
    findings.extend(sensitive_findings(name, raw));checked += 1
frozen = json.loads((BASE / 'intraday-l-test15/source-manifest.json').read_text(encoding='utf8'))
frozen_bad = []
for name, expected in frozen.items():
    staged_name = (LROOT / name).relative_to(ROOT).as_posix()
    raw = subprocess.check_output(['git', 'show', ':' + staged_name], cwd=ROOT)
    if hashlib.sha256(raw).hexdigest() != expected:frozen_bad.append(name)
changed = set(p for p in subprocess.check_output(['git', 'diff', '--cached', '--name-only', '-z'], cwd=ROOT).decode().split('\0') if p)
approved = set(manifest['files']) | {'work/handoff-20261006/git-backup-manifest.json', 'work/handoff-20261006/content-scan.json'}
unexpected = sorted(changed - approved)
for name in approved - set(manifest['files']):
    findings.extend(sensitive_findings(name, subprocess.check_output(['git', 'show', ':' + name], cwd=ROOT)))
result = {'checked_staged_files': checked, 'mismatches': mismatches, 'findings': findings,
          'frozen_l_files': len(frozen), 'frozen_l_mismatches': frozen_bad,
          'unexpected_staged_paths': unexpected, 'git_diff_whitespace_check': None}
check = subprocess.run(['git', '-c', 'core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol',
                        'diff', '--cached', '--check'], cwd=ROOT, capture_output=True,
                       text=True, encoding='utf8', errors='replace')
result['git_diff_whitespace_check'] = check.returncode == 0
if check.returncode:
    result['whitespace_issue_count'] = len(check.stdout.splitlines())
new_docs = ['docs/第四阶段4D持续执行开发与验收计划.md', 'docs/第四阶段4D会话移交提示词.md',
            'docs/第四阶段4D移交与Git备份说明.md', '.gitattributes', '.gitignore',
            'work/handoff-20261006/prepare_git_backup.py', 'work/handoff-20261006/verify_git_backup.py']
current_check = subprocess.run(['git', '-c', 'core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol',
                               'diff', '--cached', '--check', '--', *new_docs], cwd=ROOT,
                              capture_output=True, text=True, encoding='utf8', errors='replace')
result['new_handoff_files_whitespace_check'] = current_check.returncode == 0
result['historical_whitespace_policy'] = 'Preserve immutable historical files; do not reformat snapshot evidence.'
print(json.dumps(result, ensure_ascii=False))
(ROOT / 'tmp/handoff-20261006/staged-audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
if mismatches or findings or frozen_bad or unexpected or current_check.returncode:
    raise SystemExit('Staged backup verification failed; no secret values printed')
scan_path = OUT / 'content-scan.json'
scan = json.loads(scan_path.read_text(encoding='utf8'))
scan['scope'] = 'Selected working-tree text and staged Git blobs; no credential values retained.'
scan['staged_verification'] = result
scan_path.write_text(json.dumps(scan, ensure_ascii=False, indent=2), encoding='utf8')
