#!/usr/bin/env python3
"""Serve the finished episode 04 locally, including single HTTP byte ranges.

Read-only, standard library only. Binds to 127.0.0.1 and chooses a free port by
default so that other episode servers are left running.
"""
from __future__ import annotations

import argparse
from contextlib import suppress
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

HERE = Path(__file__).resolve().parent
OUTPUT = HERE.parents[1] / 'output' / 'lesson-04-gemini'
RANGE = re.compile(r'bytes=([0-9]*)-([0-9]*)', re.IGNORECASE)


class InvalidRange(ValueError):
    """The supplied single byte range cannot be served."""


def parse_byte_range(value: str | None, size: int) -> tuple[int, int] | None:
    """Return inclusive byte offsets, None for no Range, or raise InvalidRange."""
    if value is None:
        return None
    match = RANGE.fullmatch(value.strip())
    if match is None or size <= 0:
        raise InvalidRange('Expected one satisfiable bytes range')
    first, last = match.groups()
    if not first and not last:
        raise InvalidRange('Empty byte range')
    try:
        if not first:
            suffix = int(last)
            if suffix <= 0:
                raise InvalidRange('Suffix length must be positive')
            return max(0, size - suffix), size - 1
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError as error:
        raise InvalidRange('Invalid byte offset') from error
    if start >= size or start > end:
        raise InvalidRange('Range starts outside the representation')
    return start, min(end, size - 1)


def http_date(value: str) -> datetime | None:
    try:
        result = parsedate_to_datetime(value)
    except (TypeError, IndexError, OverflowError, ValueError):
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


class EpisodeHandler(SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    index_pages = ('index.html', 'index.htm')
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        '.mp4': 'video/mp4', '.wav': 'audio/wav', '.vtt': 'text/vtt; charset=utf-8',
    }

    def __init__(self, *args, **kwargs):
        self.byte_range = None
        super().__init__(*args, directory=str(OUTPUT), **kwargs)

    @staticmethod
    def safe_path(path: str | Path) -> Path:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(OUTPUT):
            raise PermissionError('Resource resolves outside episode 04 output')
        return resolved

    def translate_path(self, path):
        return str(self.safe_path(super().translate_path(path)))

    def send_head(self):
        self.byte_range = None
        try:
            path = Path(self.translate_path(self.path))
            parts = urlsplit(self.path)
            trailing_slash = parts.path.endswith(('/', '%2f', '%2F'))
            if path.is_dir():
                if not trailing_slash:
                    self.send_response(HTTPStatus.MOVED_PERMANENTLY)
                    # Keep redirects relative to this local origin.
                    self.send_header('Location', urlunsplit(('', '', parts.path + '/', parts.query, parts.fragment)))
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return None
                for index in self.index_pages:
                    candidate = self.safe_path(path / index)
                    if candidate.is_file():
                        path = candidate
                        break
                else:
                    return self.list_directory(str(path))
            elif trailing_slash:
                self.send_error(HTTPStatus.NOT_FOUND, 'File not found')
                return None
            if not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, 'File not found')
                return None
            source = path.open('rb')
        except PermissionError:
            self.send_error(HTTPStatus.FORBIDDEN, 'Resource is outside episode 04 output')
            return None
        except (OSError, RuntimeError):
            self.send_error(HTTPStatus.NOT_FOUND, 'File not found')
            return None
        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST, 'Invalid resource path')
            return None

        try:
            info = os.fstat(source.fileno())
            size = info.st_size
            modified = datetime.fromtimestamp(info.st_mtime, timezone.utc).replace(microsecond=0)
            # Match the normal SimpleHTTPRequestHandler date-cache behavior.
            if 'If-Modified-Since' in self.headers and 'If-None-Match' not in self.headers:
                since = http_date(self.headers['If-Modified-Since'])
                if since is not None and modified <= since:
                    self.send_response(HTTPStatus.NOT_MODIFIED)
                    self.send_header('Accept-Ranges', 'bytes')
                    self.end_headers()
                    source.close()
                    return None

            range_headers = self.headers.get_all('Range', [])
            request_range = ','.join(range_headers) if range_headers else None
            if request_range is not None and 'If-Range' in self.headers:
                validator = http_date(self.headers['If-Range'])
                # No ETag is advertised. An unknown/stale validator gets 200.
                if validator is None or validator != modified:
                    request_range = None
            try:
                selected = parse_byte_range(request_range, size)
            except InvalidRange:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                source.close()
                return None

            self.byte_range = selected
            self.send_response(HTTPStatus.PARTIAL_CONTENT if selected else HTTPStatus.OK)
            self.send_header('Content-Type', self.guess_type(str(path)))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Last-Modified', self.date_time_string(info.st_mtime))
            if selected:
                start, end = selected
                source.seek(start)
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                self.send_header('Content-Length', str(end - start + 1))
            else:
                self.send_header('Content-Length', str(size))
            self.end_headers()
            return source
        except BaseException:
            source.close()
            raise

    def copyfile(self, source, outputfile):
        if self.byte_range is None:
            return super().copyfile(source, outputfile)
        remaining = self.byte_range[1] - self.byte_range[0] + 1
        while remaining:
            block = source.read(min(64 * 1024, remaining))
            if not block:
                self.close_connection = True
                return
            outputfile.write(block)
            remaining -= len(block)

    def handle(self):
        with suppress(BrokenPipeError, ConnectionResetError):
            super().handle()

    def finish(self):
        with suppress(BrokenPipeError, ConnectionResetError):
            super().finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0, help='Local port; 0 chooses a free port (default).')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Port must be 0–65535')
    if OUTPUT.resolve() != OUTPUT or not OUTPUT.is_dir():
        parser.error('The canonical episode 04 output folder is missing or redirected')
    try:
        page = EpisodeHandler.safe_path(OUTPUT / 'index.html')
    except (OSError, RuntimeError, ValueError):
        parser.error('The episode 04 page must stay inside its output folder')
    if not page.is_file():
        parser.error('The completed episode 04 page is missing')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), EpisodeHandler)
    server.daemon_threads = True
    print(f'Episode 04 (HTTP Range): http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
