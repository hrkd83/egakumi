#!/usr/bin/env python3
"""Serve one finished episode locally with a dynamically allocated port."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--episode', required=True)
parser.add_argument('--port', type=int, default=0)
args = parser.parse_args()
value = args.episode.lower()
if value.isdigit() and 1 <= int(value) <= 23:
    key = f'{int(value):02}'
elif re.fullmatch(r'(?:a[1-4]|b[1-3])', value):
    key = value
else:
    parser.error('Use 1–23, A1–A4, or B1–B3.')
folder = HERE / 'output' / f'lesson-{key}-gemini'
if not (folder / 'index.html').is_file():
    parser.error('Finished episode page not found: ' + str(folder))
if not 0 <= args.port <= 65535:
    parser.error('Port must be 0–65535; 0 selects a free port.')
server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(SimpleHTTPRequestHandler, directory=str(folder)))
print(f'Episode {key}: http://127.0.0.1:{server.server_port}/', flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
