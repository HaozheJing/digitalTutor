"""Stage-four authorized SSH transport; credentials are environment-only."""
import argparse
import os
from pathlib import Path, PurePosixPath
import sys
import paramiko

p = argparse.ArgumentParser()
p.add_argument('--script')
p.add_argument('--upload', nargs=2, action='append', default=[])
p.add_argument('--download', nargs=2, action='append', default=[])
a = p.parse_args()
root = PurePosixPath('/root/autodl-tmp/digitalTutor')
c = paramiko.SSHClient()
c.load_host_keys('ops/.ssh_known_hosts')
c.set_missing_host_key_policy(paramiko.RejectPolicy())
c.connect('connect.westc.seetacloud.com', port=11385, username='root',
          password=os.environ.pop('DIGITALTUTOR_SSH_PASSWORD'),
          look_for_keys=False, allow_agent=False, timeout=20)
try:
    with c.open_sftp() as s:
        for local, remote in a.upload:
            rp = PurePosixPath(remote)
            if not rp.is_relative_to(root) or '..' in rp.parts:
                raise ValueError('Remote path outside project')
            for parent in reversed(rp.parents):
                if parent.is_relative_to(root):
                    try: s.stat(str(parent))
                    except FileNotFoundError: s.mkdir(str(parent), mode=0o700)
            s.put(local, remote)
            s.chmod(remote, 0o600)
        if a.script:
            channel = c.get_transport().open_session()
            channel.exec_command('bash -s')
            channel.sendall(Path(a.script).read_text(encoding='utf-8-sig').encode())
            channel.shutdown_write()
            import time
            while True:
                for ready, receive in ((channel.recv_ready, channel.recv),
                                       (channel.recv_stderr_ready, channel.recv_stderr)):
                    if ready():
                        sys.stdout.write(receive(65536).decode('utf-8', errors='replace'))
                        sys.stdout.flush()
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                time.sleep(.05)
            code = channel.recv_exit_status()
            if code:
                raise SystemExit(code)
        for remote, local in a.download:
            if not PurePosixPath(remote).is_relative_to(root):
                raise ValueError('Remote path outside project')
            def download(remote_path, local_path):
                import stat
                if stat.S_ISDIR(s.stat(remote_path).st_mode):
                    Path(local_path).mkdir(parents=True,exist_ok=True)
                    for entry in s.listdir(remote_path):
                        if entry not in {'__pycache__','.git'}:
                            download(remote_path.rstrip('/')+'/'+entry,str(Path(local_path)/entry))
                else:
                    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
                    s.get(remote_path, local_path)
            download(remote,local)
finally:
    c.close()
