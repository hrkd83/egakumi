#!/usr/bin/env python3
"""Serve only the finished introduction on localhost and a free port."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from intro_runtime import OUTPUT, TOOLKIT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Port must be 0–65535; 0 selects a free port.')
    folder = TOOLKIT / 'output' / OUTPUT
    if folder.resolve() != folder or not (folder / 'index.html').is_file():
        parser.error('Finished introduction page not found: ' + str(folder))
    server = ThreadingHTTPServer(('127.0.0.1', args.port),
        partial(SimpleHTTPRequestHandler, directory=str(folder)))
    print(f'Introduction: http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
