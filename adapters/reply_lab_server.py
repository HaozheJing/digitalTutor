"""Start only the isolated teacher/reply lab UI. No model or delivery process."""
import argparse
import json
import os
from pathlib import Path
from http.server import ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, default=19233)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_absolute() or not (root / '.git').exists() or args.port not in {19231, 19232, 19233, 19433}:
        raise SystemExit('Use the assigned isolated worktree and an A-line or integration UI port.')
    if root == Path('/home/ubuntu/digitalTutor-dsh-lab'):
        raise SystemExit('Refusing production checkout.')
    # No outbound configuration is needed by the UI or fixture engine.
    for name in ['provider.json', 'test-wechat.json', 'wecom-callback.json']:
        if (root / 'config' / name).exists():
            raise SystemExit('Remove outbound configuration from the fixture UI root: ' + name)
    os.environ['TUTOR_ROOT'] = str(root)
    # Isolated draft-and-test UI only; TeacherStudio still cannot publish.
    os.environ['TUTOR_EXPERIMENT_INTERNAL_DRAFTS'] = '1'
    from teacher_workbench import TeacherStudio, handler
    from reply_lab import ReplyLab
    worker = root / 'runtime/quality-source-snapshot/native-worker.json'
    runner = None
    if worker.exists():
        from reply_lab_native import NativeRunner
        runner = NativeRunner(root, worker)
    studio = TeacherStudio(root, native_runner=runner)
    studio._reply_lab = ReplyLab(studio, runner=runner)
    print(json.dumps({'root': str(root), 'teacher_db': str(studio.db), 'experiment_db': str(studio._reply_lab.db),
                      'runs_root': str(studio._reply_lab.home / 'runs'), 'bind': '127.0.0.1', 'port': args.port,
                      'native_adapter': runner is not None, 'transport': 'simulation'}, ensure_ascii=False), flush=True)
    try:
        ThreadingHTTPServer(('127.0.0.1', args.port), handler(studio)).serve_forever()
    finally:
        studio._reply_lab.close()
        studio.pool.shutdown()


if __name__ == '__main__': main()
