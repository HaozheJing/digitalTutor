set -eu
printf 'OS\n'
cat /etc/os-release
printf '\nRuntime\n'
id
pwd
uname -m
python3 --version || true
printf '\nExisting runtimes\n'
for path in /root/miniconda3/bin/python /opt/conda/bin/python /usr/bin/python3 /root/miniconda3/bin/conda; do if [ -x "$path" ]; then "$path" --version; fi; done
ls -ld /root/miniconda3 /opt/conda /usr/bin/python* /root/.hermes /root/.openviking 2>/dev/null || true
for item in git curl docker uv supervisorctl; do command -v "$item" || true; done
printf '\nStorage\n'
ls -ld /root/autodl-tmp /autodl-tmp 2>/dev/null || true
df -h /root /root/autodl-tmp 2>/dev/null || true
printf '\nExisting target\n'
if [ -e /root/autodl-tmp/digitalTutor ]; then ls -la /root/autodl-tmp/digitalTutor; fi
printf '\nServices and ports\n'
ss -ltnp | head -25
printf '\nResources\n'
free -h
printf '\nGitHub connectivity\n'
curl -I --connect-timeout 10 --max-time 20 https://github.com 2>/dev/null | head -3
