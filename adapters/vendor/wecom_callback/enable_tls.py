"""Run on the server after public ports 80/443 are reachable."""
import json
from pathlib import Path
import subprocess

ROOT = Path('/home/ubuntu/digitalTutor')
IP = '82.156.162.52'
CERTBOT = '/opt/digitaltutor-certbot/bin/certbot'


def run(args):
    subprocess.run(args, check=True)


def main():
    run(['sudo', '-n', CERTBOT, 'certonly', '--non-interactive', '--agree-tos',
         '--register-unsafely-without-email', '--preferred-profile', 'shortlived',
         '--webroot', '--webroot-path', '/var/www/digitaltutor-acme',
         '--ip-address', IP, '--cert-name', 'digitaltutor-callback-ip'])
    nginx = '''limit_req_zone $binary_remote_addr zone=wecom_callback:10m rate=20r/s;
server {
    listen 80 default_server;
    server_name 82.156.162.52;
    access_log off;
    server_tokens off;
    location ^~ /.well-known/acme-challenge/ { root /var/www/digitaltutor-acme; }
    location / { return 308 https://82.156.162.52$request_uri; }
}
server {
    listen 443 ssl default_server;
    server_name 82.156.162.52;
    ssl_certificate /etc/letsencrypt/live/digitaltutor-callback-ip/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/digitaltutor-callback-ip/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    server_tokens off;
    access_log off;
    client_max_body_size 1m;
    client_body_timeout 10s;
    client_header_timeout 10s;
    location = /healthz { proxy_pass http://127.0.0.1:9010; }
    location ^~ /wecom/ {
        limit_req zone=wecom_callback burst=100 nodelay;
        proxy_connect_timeout 3s;
        proxy_read_timeout 15s;
        proxy_pass http://127.0.0.1:9010;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
    }
    location / { return 404; }
}
'''
    staged = ROOT/'ops/wecom_callback/nginx-https.conf'
    staged.write_text(nginx)
    target = Path('/etc/nginx/conf.d/digitaltutor-wecom.conf')
    backup = ROOT/'ops/wecom_callback/nginx-before-tls.conf'
    backup.write_bytes(target.read_bytes())
    run(['sudo','-n','install','-m','644',str(staged),str(target)])
    try:
        run(['sudo','-n','nginx','-t'])
    except subprocess.CalledProcessError:
        run(['sudo','-n','install','-m','644',str(backup),str(target)])
        raise
    run(['sudo','-n','systemctl','reload','nginx'])
    service = '''[Unit]
Description=Renew digitalTutor callback IP TLS certificate
After=network-online.target nginx.service

[Service]
Type=oneshot
ExecStart=/opt/digitaltutor-certbot/bin/certbot renew --quiet --cert-name digitaltutor-callback-ip --deploy-hook "/usr/bin/systemctl reload nginx"
'''
    timer = '''[Unit]
Description=Check digitalTutor callback TLS renewal twice daily

[Timer]
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=1800
Persistent=true

[Install]
WantedBy=timers.target
'''
    for name, body in [('digitaltutor-callback-cert.service',service),
                       ('digitaltutor-callback-cert.timer',timer)]:
        path=ROOT/'ops/wecom_callback'/name
        path.write_text(body)
        run(['sudo','-n','install','-m','644',str(path),'/etc/systemd/system/'+name])
    run(['sudo','-n','systemctl','daemon-reload'])
    run(['sudo','-n','systemctl','enable','--now','digitaltutor-callback-cert.timer'])
    print('HTTPS_ENABLED')


if __name__ == '__main__':
    main()
