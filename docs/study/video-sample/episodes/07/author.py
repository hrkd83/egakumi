#!/usr/bin/env python3
"""Current episode 07 authoring entry point: teaching-settings-v2."""
import runpy,subprocess
from pathlib import Path
here=Path(__file__).resolve().parent
runpy.run_path(str(here/'author-settings-v2.py'),run_name='__main__')
subprocess.run(['node',str(here/'render-settings-v2.mjs')],check=True)
