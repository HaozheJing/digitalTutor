"""Host lifecycle regression; the process-group probe requires the Linux server."""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace

from digital_tutor.application import Application
from digital_tutor.domain import BusinessError
from digital_tutor.hermes_host import HermesHost, _stop_process


class HostTests(unittest.TestCase):
    def test_process_runtime_rejects_memory_database(self):
        app = Application(':memory:')
        try:
            with self.assertRaises(BusinessError) as caught:
                HermesHost('.', '.').run(SimpleNamespace(app=app), 'test')
            self.assertEqual(caught.exception.code, 'RUNTIME_DATABASE_REQUIRED')
        finally:
            app.close()

    @unittest.skipUnless(os.name == 'posix', 'Linux process-group acceptance runs on development server')
    def test_cleanup_kills_descendant_even_when_parent_exits_first(self):
        child = 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'
        parent = ('import subprocess,sys,time; '
                  'p=subprocess.Popen([sys.executable,"-c",sys.argv[1]]); '
                  'print(p.pid,flush=True); time.sleep(60)')
        proc = subprocess.Popen([sys.executable, '-c', parent, child],
                                stdout=subprocess.PIPE, text=True, start_new_session=True)
        try:
            child_pid = int(proc.stdout.readline().strip())
            time.sleep(0.2)
            _stop_process(proc)
            self.assertIsNotNone(proc.poll())
            # A reaped-or-zombie descendant is no longer executing. PID 1 may reap later.
            deadline = time.monotonic() + 2
            running = True
            while time.monotonic() < deadline:
                stat = Path('/proc') / str(child_pid) / 'stat'
                if not stat.exists() or stat.read_text().split()[2] == 'Z':
                    running = False
                    break
                time.sleep(0.02)
            self.assertFalse(running, 'The run left its descendant executing after cleanup')
        finally:
            _stop_process(proc)
            proc.stdout.close()


if __name__ == '__main__':
    unittest.main()
