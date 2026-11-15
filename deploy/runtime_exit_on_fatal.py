"""Let the container platform recover a process that exhausts its startup retries."""

import os
import signal
import sys

from supervisor import childutils


childutils.listener.wait(sys.stdin, sys.stdout)
childutils.listener.ok(sys.stdout)
os.kill(os.getppid(), signal.SIGTERM)
