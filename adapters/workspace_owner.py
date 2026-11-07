"""Linux process-lifetime exclusive workspace owner, released on pipe EOF/crash.

Only the native simulation host invokes this before recovery. A second host
must not reset another owner's dispatched events or active task metadata.
"""
import fcntl,sys
from pathlib import Path

def hold(root):
    path=Path(root).resolve()/'runtime/family-owner.lock'
    with path.open('a+') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('busy',flush=True);return 2
        print('owned',flush=True)
        sys.stdin.buffer.read()
    return 0
if __name__=='__main__':sys.exit(hold(sys.argv[1]))
