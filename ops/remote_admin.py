"""Run an authorized remote command; SSH password is process-environment only."""
import argparse
import base64
import hashlib
import os
import pathlib
import sys
import time

import paramiko

parser = argparse.ArgumentParser()
parser.add_argument("--script")
parser.add_argument("--upload", nargs=2, action="append", metavar=("LOCAL", "REMOTE"))
parser.add_argument("--timeout", type=int, default=1200)
parser.add_argument("--provider-json", metavar="REMOTE_PATH")
args = parser.parse_args()
client = paramiko.SSHClient()
known_hosts = pathlib.Path(__file__).parent / ".ssh_known_hosts"
if known_hosts.exists():
    client.load_host_keys(str(known_hosts))
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(
    "connect.westc.seetacloud.com", port=11385, username="root",
    password=os.environ.pop("DIGITALTUTOR_SSH_PASSWORD"),
    look_for_keys=False, allow_agent=False, timeout=20, auth_timeout=20,
)
client.save_host_keys(str(known_hosts))
key = client.get_transport().get_remote_server_key()
fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
print("SSH connected; host key SHA256:" + fingerprint, flush=True)
try:
    if args.provider_json:
        payload = os.environ.pop("DIGITALTUTOR_PROVIDER_JSON")
        with client.open_sftp() as sftp:
            with sftp.open(args.provider_json, "w") as target:
                target.write(payload)
            sftp.chmod(args.provider_json, 0o600)
        print("Provider configuration saved with mode 0600", flush=True)
    if args.upload:
        with client.open_sftp() as sftp:
            for source, destination in args.upload:
                remote_path = pathlib.PurePosixPath(destination)
                root = pathlib.PurePosixPath('/root/autodl-tmp/digitalTutor')
                if not remote_path.is_relative_to(root) or '..' in remote_path.parts:
                    raise ValueError('Upload must remain under the authorized deployment root')
                for parent in reversed(remote_path.parents):
                    if parent == root or parent.is_relative_to(root):
                        try: sftp.stat(str(parent))
                        except FileNotFoundError: sftp.mkdir(str(parent), mode=0o700)
                sftp.put(source, destination)
                sftp.chmod(destination, 0o700 if destination.endswith('.sh') else 0o600)
                print("Uploaded " + destination, flush=True)
    if args.script:
        script = pathlib.Path(args.script).read_text(encoding="utf-8")
        channel = client.get_transport().open_session()
        channel.exec_command("bash -s")
        channel.sendall(script.encode("utf-8"))
        channel.shutdown_write()
        started = time.monotonic()
        while True:
            if channel.recv_ready():
                sys.stdout.write(channel.recv(65536).decode("utf-8", errors="replace"))
                sys.stdout.flush()
            if channel.recv_stderr_ready():
                sys.stdout.write(channel.recv_stderr(65536).decode("utf-8", errors="replace"))
                sys.stdout.flush()
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic() - started > args.timeout:
                channel.close()
                raise TimeoutError("Remote command exceeded timeout")
            time.sleep(0.05)
        code = channel.recv_exit_status()
        print("\nRemote exit code:", code, flush=True)
        sys.exit(code)
finally:
    client.close()
