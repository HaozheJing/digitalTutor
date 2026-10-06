"""Run the installed ov CLI and retain redacted evidence; no SDK/HTTP calls."""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def decode_json(text):
    for i, char in enumerate(text):
        if char in '{[':
            try:
                value, end = json.JSONDecoder().raw_decode(text[i:])
                # ov echoes the command before its response. A query may itself
                # contain JSON; only accept the final complete JSON document.
                if not text[i + end:].strip():
                    return value
            except ValueError:
                pass
    return None


def main():
    request = json.load(sys.stdin)
    config = Path(sys.argv[1])
    key = json.loads(config.read_text())['api_key']
    root = Path(__file__).resolve().parents[1]
    label = request['label']
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', label):
        raise ValueError('Invalid evidence label')
    evidence = root / 'build/openviking/courses' / request['snapshot'] / 'cli-records'
    evidence.mkdir(parents=True, exist_ok=True)
    output_path = evidence / (label + '.json')
    if output_path.exists():
        raise ValueError('Evidence already exists; inspect it rather than blindly repeat operations')
    args = [str(root / 'runtime/openviking/bin/ov'), *request['args']]
    env = os.environ.copy()
    env['OPENVIKING_CLI_CONFIG_FILE'] = str(config)
    env['NO_COLOR'] = '1'
    at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        completed = subprocess.run(args, capture_output=True, text=True, env=env, timeout=request.get('timeout', 90))
        stdout = completed.stdout.replace(key, '[REDACTED]')
        stderr = completed.stderr.replace(key, '[REDACTED]')
        record = {'at_utc': at, 'args': request['args'], 'exit_code': completed.returncode,
                  'stdout': stdout, 'stderr': stderr, 'parsed': decode_json(stdout), 'timeout': False}
    except subprocess.TimeoutExpired as error:
        def clean(value):
            return (value.decode('utf-8', 'replace') if isinstance(value, bytes) else (value or '')).replace(key, '[REDACTED]')
        record = {'at_utc': at, 'args': request['args'], 'exit_code': None, 'timeout': True,
                  'stdout': clean(error.stdout), 'stderr': clean(error.stderr)}
    output_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = {k: record[k] for k in ['exit_code', 'timeout']}
    summary['record'] = str(output_path)
    summary['parsed'] = record.get('parsed')
    if summary['parsed'] is None:
        summary['output'] = (record['stdout'] + record['stderr'])[:6000]
    elif request.get('brief'):
        summary['parsed'] = {'keys': list(summary['parsed']) if isinstance(summary['parsed'], dict) else 'non-object',
                             'characters': len(record['stdout'])}
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
