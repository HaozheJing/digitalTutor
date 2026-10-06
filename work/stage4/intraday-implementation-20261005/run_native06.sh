set -eu
cd /root/autodl-tmp/digitalTutor
runtime/backend/bin/python work/stage4/intraday-implementation-20261005/run_frozen.py --suite regression-final --workers 6 --manifest intraday-l-test15 --run intraday-l-native06
