"""Install a dormant auto-reply worker. Explicit platform configuration activates it."""
from pathlib import Path
import sys
import paramiko

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from ops.humanity_native_20261016.remote_ops import connect,python

UNIT='''[Unit]
Description=digitalTutor native Service WeCom auto reply
After=network-online.target digitaltutor-wecom-callback.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/digitalTutor/ops/wecom_callback
ExecStart=/home/ubuntu/digitalTutor/runtime/backend/bin/python /home/ubuntu/digitalTutor/ops/wecom_callback/bridge.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/ubuntu/digitalTutor/var/wecom-autoreply /home/ubuntu/digitalTutor/var/wecom-callback
Environment=PYTHONDONTWRITEBYTECODE=1

[Install]
WantedBy=multi-user.target
'''


def main():
    s=connect()
    try:
        ftp=paramiko.SFTPClient.from_transport(s)
        for name in ['wecom_transport.py','build_candidate.py','bridge.py','test_autoreply.py',
                     'test_context.py','test_context_reset.py','workbench.py','workbench.html',
                     'media.py','test_media.py','replyer.py','test_replyer.py','replyer-fixtures.json',
                     'replyer-common.md','replyer-role.md']:
            ftp.put(str(Path(__file__).with_name(name)),'/home/ubuntu/digitalTutor/ops/wecom_callback/'+name)
        with ftp.open('/home/ubuntu/digitalTutor/ops/wecom_callback/autoreply.service','w') as f:f.write(UNIT)
        ftp.close()
        print(python(s,r'''
import json,subprocess
from pathlib import Path
root=Path('/home/ubuntu/digitalTutor');folder=root/'ops/wecom_callback'
py=str(root/'runtime/backend/bin/python')
if not (root/'candidates/service-wecom-v1').exists():
    subprocess.run([py,str(folder/'build_candidate.py')],check=True)
subprocess.run([py,'-m','unittest','discover','-s',str(folder),'-p','test_autoreply.py','-v'],check=True)
state=root/'var/wecom-autoreply';state.mkdir(parents=True,exist_ok=True);state.chmod(0o700)
cfgpath=root/'config/wecom-callback.json';cfg=json.loads(cfgpath.read_text())
cfg.setdefault('auto_reply_enabled',False);cfg.setdefault('allowed_routes',[])
cfg.setdefault('api_base_url','');cfg.setdefault('auto_reply_not_before',None)
tmp=cfgpath.with_suffix('.tmp');tmp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2));tmp.chmod(0o600);tmp.replace(cfgpath)
subprocess.run(['sudo','-n','install','-m','644',str(folder/'autoreply.service'),'/etc/systemd/system/digitaltutor-wecom-autoreply.service'],check=True)
subprocess.run(['sudo','-n','systemctl','daemon-reload'],check=True)
subprocess.run(['sudo','-n','systemctl','enable','--now','digitaltutor-wecom-autoreply'],check=True)
print('INSTALLED_DISABLED_PENDING_CONFIG')
'''))
    finally:s.close()


if __name__=='__main__':main()
