"""Extract representative frames from the completed MP4, without changing it."""
from pathlib import Path
import json
import subprocess

HERE=Path(__file__).resolve().parent
OUT=HERE.parents[1]/'output'/'lesson-16-gemini'
t=json.loads((OUT/'lesson-timeline.json').read_text())
video=OUT/'egakumi-study-16.mp4'
frames=HERE/'final-frames';frames.mkdir(exist_ok=True)
samples=[('logo',2),('cast',4.5)]
for n in [1,2,3,22,23,26,33,36,43,44,45,70,71,74,75,78,79,83]:
    scene=next(s for s in t['scenes'] if s.get('id','').startswith(f'lesson16-turn-{n:03}-'))
    samples.append((f'turn-{n:03}',scene['start']+min(3,scene['duration']/2)))
samples.extend([('closing',t['scenes'][-1]['start']+3),('closing-end',t['duration']-.2)])
manifest=[]
for name,seconds in samples:
    file=frames/(name+'.png')
    subprocess.run(['ffmpeg','-v','error','-y','-ss',str(seconds),'-i',str(video),'-frames:v','1',str(file)],check=True)
    manifest.append({'name':name,'seconds':seconds,'file':file.name})
(frames/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'frames':len(manifest),'directory':str(frames)}))
