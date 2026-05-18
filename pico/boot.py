import os
import time

# Safety hatch for deploys: if a `skip-main` file exists in the filesystem
# root, drop to REPL instead of running the app. This is the rescue path when
# `main.run()` would otherwise grab the event loop and prevent code pushes.
#
# Toggle from a host:
#   mpremote touch :skip-main   # next boot drops to REPL
#   mpremote rm :skip-main      # next boot runs main again
try:
    os.stat("skip-main")
    print("boot: skip-main present, dropping to REPL")
    raise SystemExit
except OSError:
    pass  # no skip-main, proceed normally

# Brief delay so a determined operator with Ctrl-C can still interrupt before
# main.run() takes the event loop. 1 second is short enough to feel snappy.
print("boot: starting main in 1s")
time.sleep(1)

import main
main.run()
