"""Offline subprocess fixture used by the desktop process-runner integration test."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--handshake", action="store_true")
parser.add_argument("--job")
args = parser.parse_args()
if args.handshake and sys.stdin.readline().strip() != "START":
    raise SystemExit(2)
job = json.loads(open(args.job, encoding="utf-8").read())
print("Unicode fixture: Gr\u00fc\u00dfe \u97f3\u697d", flush=True)
print("stderr fixture", file=sys.stderr, flush=True)
if job["mode"] == "cancel":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"], creationflags=subprocess.CREATE_NO_WINDOW)
    print("CHILD_PID=" + str(child.pid), flush=True)
    time.sleep(90)
elif job["mode"] == "logger":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "savify-new"))
    from savify.logger import Logger
    logger = Logger(str(Path(args.job).parent / "legacy"))
    logger.debug("Savify DEBUG transport fixture")
    logger.info("Savify INFO transport fixture")
elif job["mode"] == "flood":
    for number in range(10000):
        print("Output line " + str(number), flush=True)
    print("@@CONVENIFY@@" + json.dumps(dict(event="artifact", path="fixture", label="fixture")), flush=True)
else:
    raise SystemExit(7)
