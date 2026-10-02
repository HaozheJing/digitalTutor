BASE=/root/autodl-tmp/digitalTutor
ps -eo pid,ppid,etimes,args | grep -E '(digitalTutor|git clone|uv sync|uv pip|uv python)' | grep -v grep || true
du -sh "$BASE"/* 2>/dev/null || true
