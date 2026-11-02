"""Serve this episode on loopback, with byte ranges for video chapter seeking."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2] / 'output' / 'lesson-10-gemini'


class RangeHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        self.remaining = None
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            return super().send_head()
        stream = path.open('rb')
        info = path.stat()
        size = info.st_size
        start, end = 0, size - 1
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested.strip())
            if not match or not any(match.groups()):
                stream.close()
                self.send_error(416, 'Unsupported range')
                return None
            a, b = match.groups()
            if a:
                start = int(a)
                end = min(int(b), size - 1) if b else size - 1
            else:
                start = max(0, size - int(b))
            if start >= size or start > end:
                stream.close()
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return None
        self.send_response(206 if requested else 200)
        self.send_header('Content-Type', self.guess_type(str(path)))
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Last-Modified', self.date_time_string(info.st_mtime))
        if requested:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        stream.seek(start)
        self.remaining = end - start + 1
        return stream

    def copyfile(self, source, destination):
        if self.remaining is None:
            return super().copyfile(source, destination)
        try:
            while self.remaining:
                data = source.read(min(256 * 1024, self.remaining))
                if not data:
                    break
                destination.write(data)
                self.remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Browser intentionally abandoned a previous video range.


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    if not (ROOT / 'index.html').is_file():
        parser.error('Finished episode is missing')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(RangeHandler, directory=str(ROOT)))
    print(f'Episode 10: http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
