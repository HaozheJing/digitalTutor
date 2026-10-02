set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
if [ ! -d "$BASE/vendor/hermes/.git" ]; then
  git clone --depth 1 --branch v2026.9.24 https://github.com/NousResearch/hermes-agent.git "$BASE/vendor/hermes"
fi
git -C "$BASE/vendor/hermes" describe --tags --always
