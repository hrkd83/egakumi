#!/usr/bin/env python3
"""Serve only episode 18 artifacts on an automatically allocated local port."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

folder = Path(__file__).resolve().parent.parent.parent / 'output' / 'lesson-18-gemini'
server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(folder)))
print(f'Episode 18: http://127.0.0.1:{server.server_port}/', flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
