#!/usr/bin/env python3
"""Guard only episode 07's current render parent against temporary disk exhaustion."""
import os,shutil,signal,time
from pathlib import Path
out=Path(__file__).resolve().parents[2]/'output/lesson-07-gemini'
lock=out/'.lesson-build.lock'
pid=int(lock.read_text())
# The parent was suspended while a sibling episode used the temporary space.
suspended=True
try:
 while lock.exists() and lock.read_text().strip()==str(pid):
  free=shutil.disk_usage(out).free/1024**3
  if suspended and free>=1.4:
   os.kill(pid,signal.SIGCONT);suspended=False
   print(f'Resumed episode 07 pid={pid}, free={free:.2f} GiB',flush=True)
  elif not suspended and free<.30:
   os.kill(pid,signal.SIGSTOP);suspended=True
   print(f'Suspended episode 07 pid={pid}, free={free:.2f} GiB',flush=True)
  time.sleep(2)
finally:
 if suspended and lock.exists() and lock.read_text().strip()==str(pid):
  os.kill(pid,signal.SIGCONT)
print('Episode 07 render lock released; capacity guard finished.',flush=True)
