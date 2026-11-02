#!/usr/bin/env python3
"""Serve episode 02 on loopback, including byte ranges for browser seeking."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re


class VideoHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Accept-Ranges', 'bytes')
        super().end_headers()

    def send_head(self):
        self.remaining = None
        value = self.headers.get('Range')
        filename = self.translate_path(self.path)
        if not value or not os.path.isfile(filename):
            return super().send_head()
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', value.strip())
        stream = open(filename, 'rb')
        stat = os.fstat(stream.fileno())
        size = stat.st_size
        if match and any(match.groups()):
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            else:
                start, end = max(0, size - int(last)), size - 1
            if 0 <= start <= end < size:
                stream.seek(start)
                self.remaining = end - start + 1
                self.send_response(206)
                self.send_header('Content-Type', self.guess_type(filename))
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                self.send_header('Content-Length', str(self.remaining))
                self.send_header('Last-Modified', self.date_time_string(stat.st_mtime))
                self.end_headers()
                return stream
        stream.close()
        self.send_response(416)
        self.send_header('Content-Range', f'bytes */{size}')
        self.send_header('Content-Length', '0')
        self.end_headers()
        return None

    def copyfile(self, source, outputfile):
        try:
            if self.remaining is None:
                return super().copyfile(source, outputfile)
            while self.remaining:
                data = source.read(min(64 * 1024, self.remaining))
                if not data:
                    break
                outputfile.write(data)
                self.remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError):
            # Browser seeking can cancel the previous media request.
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Use port 0 for automatic allocation, or 1–65535.')
    folder = Path(__file__).resolve().parents[2] / 'output' / 'lesson-02-gemini'
    if not (folder / 'index.html').is_file():
        parser.error('Build the complete episode first.')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(VideoHandler, directory=str(folder)))
    print(f'Episode 02: http://127.0.0.1:{server.server_port}/ (byte ranges enabled)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
