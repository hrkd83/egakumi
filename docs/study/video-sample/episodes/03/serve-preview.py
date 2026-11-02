#!/usr/bin/env python3
"""Serve episode 03 locally, with byte ranges for browser video seeking.

Range semantics: https://www.rfc-editor.org/rfc/rfc9110.html#section-14
Directory handling and URL path translation remain with SimpleHTTPRequestHandler.
"""
from __future__ import annotations

import argparse
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re


OUTPUT = Path(__file__).resolve().parents[2] / "output" / "lesson-03-gemini"


def byte_range(value: str, size: int) -> tuple[int, int]:
    """Parse one bytes range, bounding large numerals before integer conversion."""
    match = re.fullmatch(r"bytes=\s*([0-9]*)-([0-9]*)\s*", value, re.IGNORECASE)
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError("Invalid or unsatisfiable byte range")

    def number(digits: str) -> int:
        digits = digits.lstrip("0") or "0"
        return size + 1 if len(digits) > len(str(size)) else int(digits)

    first, last = match.groups()
    if not first:
        length = number(last)
        if length == 0:
            raise ValueError("Zero-length suffix range")
        return max(0, size - length), size - 1

    start = number(first)
    end = number(last) if last else size - 1
    if start >= size or end < start:
        raise ValueError("Unsatisfiable byte range")
    return start, min(end, size - 1)


class PreviewHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        if getattr(self, "_accept_ranges", False):
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self):
        self._selected_range = None
        path = self.translate_path(self.path)
        self._accept_ranges = os.path.isfile(path)
        requested = self.headers.get("Range")

        # RFC 9110 defines Range for GET only. Retain the parent's HEAD,
        # redirects, directory index/listing, missing-file and cache behavior.
        if (self.command != "GET" or not requested or not self._accept_ranges
                or path.endswith("/")):
            return super().send_head()
        unit, separator, _ = requested.partition("=")
        if unit.strip().lower() != "bytes" or not separator or "," in requested:
            # Unknown units and multipart requests use a complete response.
            return super().send_head()
        if any(name in self.headers for name in
               ("If-Match", "If-None-Match", "If-Modified-Since", "If-Unmodified-Since")):
            # Range is optional. Let the parent evaluate its supported cache
            # conditions before considering any partial representation.
            return super().send_head()

        try:
            source = open(path, "rb")
        except OSError:
            return super().send_head()
        try:
            metadata = os.fstat(source.fileno())
            size = metadata.st_size
            modified = self.date_time_string(metadata.st_mtime)
            if ("If-Range" in self.headers
                    and self.headers["If-Range"] != modified):
                # No ETag is issued. A stale/unrecognized validator requires
                # the full representation rather than an unrelated fragment.
                source.close()
                return super().send_head()
            if size == 0:
                source.close()
                return super().send_head()
            try:
                start, end = byte_range(requested, size)
            except ValueError:
                source.close()
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None

            self._selected_range = (start, end)
            source.seek(start)
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Last-Modified", modified)
            self.end_headers()
            return source
        except BaseException:
            source.close()
            raise

    def copyfile(self, source, outputfile):
        try:
            if self._selected_range is None:
                return super().copyfile(source, outputfile)
            start, end = self._selected_range
            remaining = end - start + 1
            while remaining:
                chunk = source.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                outputfile.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # Browsers cancel an in-flight read when seeking elsewhere.
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("Port must be 0–65535; 0 selects a free port.")
    if not (OUTPUT / "index.html").is_file():
        parser.error("Finished episode page not found: " + str(OUTPUT))
    handler = partial(PreviewHandler, directory=str(OUTPUT))
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"Episode 03: http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
