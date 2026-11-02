#!/usr/bin/env python3
"""Serve episode a2 locally with byte ranges for browser video seeking."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import re


OUT = Path(__file__).resolve().parents[2] / 'output' / 'lesson-a2-gemini'


class EpisodeHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        self.byte_range = None
        requested_range = self.headers.get('Range')
        file_path = Path(self.translate_path(self.path))
        if not requested_range or not file_path.is_file():
            return super().send_head()
        source = file_path.open('rb')
        stat = os.fstat(source.fileno())
        size = stat.st_size
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested_range.strip())
        if match and any(match.groups()):
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            else:
                start, end = max(0, size - int(last)), size - 1
            if 0 <= start <= end < size:
                self.send_response(206)
                self.send_header('Content-Type', self.guess_type(str(file_path)))
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                self.send_header('Content-Length', str(end - start + 1))
                self.send_header('Last-Modified', self.date_time_string(stat.st_mtime))
                self.end_headers()
                self.byte_range = (start, end)
                source.seek(start)
                return source
        source.close()
        self.send_response(416)
        self.send_header('Content-Range', f'bytes */{size}')
        self.send_header('Content-Length', '0')
        self.end_headers()
        return None

    def end_headers(self):
        if self.byte_range is None:
            self.send_header('Accept-Ranges', 'bytes')
        super().end_headers()

    def copyfile(self, source, outputfile):
        if self.byte_range is None:
            return super().copyfile(source, outputfile)
        remaining = self.byte_range[1] - self.byte_range[0] + 1
        while remaining:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)


def main():
    if not (OUT / 'index.html').is_file():
        raise SystemExit('Finished episode a2 is missing.')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(EpisodeHandler, directory=str(OUT)))
    print(f'Episode A2: http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
