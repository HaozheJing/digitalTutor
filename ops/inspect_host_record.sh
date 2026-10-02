BASE=/root/autodl-tmp/digitalTutor
grep -n -E 'class HostRecord|token|def read_record' "$BASE/vendor/hermes/gateway/host_rendezvous.py" | head -n 35
