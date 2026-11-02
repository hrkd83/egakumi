"""Serve this episode on loopback with byte ranges for chapter seeking."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shutil

OUT=Path(__file__).resolve().parents[2]/'output'/'lesson-16-gemini'
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(OUT),**kwargs)
    def send_head(self):
        self.byte_range=None
        header=self.headers.get('Range')
        file=Path(self.translate_path(self.path))
        if not header or not file.is_file():return super().send_head()
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',header)
        if not match:return super().send_head()
        size=file.stat().st_size
        if not size:return super().send_head()
        first,last=match.groups()
        if first:start=int(first);end=min(int(last) if last else size-1,size-1)
        else:start=max(0,size-int(last or 0));end=size-1
        if start>=size or start>end:
            self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.end_headers();return None
        stream=file.open('rb');stream.seek(start);self.byte_range=(start,end)
        self.send_response(206)
        self.send_header('Content-Type',self.guess_type(str(file)))
        self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.send_header('Accept-Ranges','bytes')
        self.send_header('Content-Length',str(end-start+1))
        self.end_headers();return stream
    def copyfile(self,source,outputfile):
        if self.byte_range is None:return shutil.copyfileobj(source,outputfile)
        remaining=self.byte_range[1]-self.byte_range[0]+1
        while remaining:
            block=source.read(min(256*1024,remaining))
            if not block:break
            outputfile.write(block);remaining-=len(block)

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    print(f'Episode 16: http://127.0.0.1:{server.server_port}/',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
