#!/usr/bin/env python3
"""Local-only review server with media byte-range support for chapter seeking."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[3]/'output/lesson-01-gemini'

class MediaHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        path=Path(self.translate_path(self.path))
        if not path.is_file() or 'Range' not in self.headers:
            self.byte_range=None
            return super().send_head()
        value=re.fullmatch(r'bytes=(\d*)-(\d*)',self.headers['Range'].strip())
        if not value:return self.send_error(416,'Unsupported range')
        size=path.stat().st_size
        begin,end=value.groups()
        if begin:
            begin=int(begin);end=min(int(end),size-1) if end else size-1
        else:
            count=int(end or '0');begin=max(0,size-count);end=size-1
        if begin>end or begin>=size:
            self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.send_header('Content-Length','0');self.end_headers();return None
        stream=path.open('rb');stream.seek(begin);self.byte_range=(begin,end)
        self.send_response(206)
        self.send_header('Content-Type',self.guess_type(str(path)))
        self.send_header('Content-Length',str(end-begin+1))
        self.send_header('Content-Range',f'bytes {begin}-{end}/{size}')
        self.send_header('Last-Modified',self.date_time_string(path.stat().st_mtime))
        self.end_headers()
        return stream

    def end_headers(self):
        self.send_header('Accept-Ranges','bytes')
        self.send_header('Cache-Control','no-cache')
        super().end_headers()

    def copyfile(self,source,output):
        remaining=self.byte_range[1]-self.byte_range[0]+1 if self.byte_range else None
        try:
            while remaining is None or remaining>0:
                data=source.read(min(256*1024,remaining) if remaining is not None else 256*1024)
                if not data:break
                output.write(data)
                if remaining is not None:remaining-=len(data)
        except (BrokenPipeError,ConnectionResetError):pass

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=0);args=parser.parse_args()
    if not 0<=args.port<=65535:parser.error('port must be 0–65535')
    if not (ROOT/'index.html').is_file():parser.error('Build the episode first')
    server=ThreadingHTTPServer(('127.0.0.1',args.port),partial(MediaHandler,directory=str(ROOT)))
    print(f'Settings v2: http://127.0.0.1:{server.server_port}/',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
