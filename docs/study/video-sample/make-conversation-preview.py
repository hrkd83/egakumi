#!/usr/bin/env python3
"""Extract an uninterrupted question-and-answer preview from a finished lesson.

This local tool does not read authentication or make network/API requests.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent
FPS=24
BUSY_LOCKS=('.lesson-build.lock','.gemini-generator.lock')
OUTPUT_NAMES=('conversation-preview.mp4','conversation-preview.json','conversation-preview.html')


def require(condition,message):
    if not condition:raise ValueError(message)


def episode_key(value):
    value=str(value).strip().lower()
    if value.isdigit() and 1<=int(value)<=23:return f'{int(value):02}'
    if re.fullmatch(r'(?:a[1-4]|b[1-3])',value):return value
    raise ValueError('Use a content ID from 1–23, A1–A4, or B1–B3.')


def read(path):
    return json.loads(path.read_text())


def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as source:
        for part in iter(lambda:source.read(1024*1024),b''):result.update(part)
    return result.hexdigest()


def close(a,b,tolerance=1e-6):
    return math.isfinite(float(a)) and math.isfinite(float(b)) and abs(float(a)-float(b))<=tolerance


def run(args):
    result=subprocess.run(args,capture_output=True,text=True)
    require(result.returncode==0,f'{args[0]} failed: {result.stderr[-3000:]}')
    return result.stdout


def probe(path):
    return json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]))


def idle(folder):
    present=[name for name in BUSY_LOCKS if (folder/name).exists()]
    require(not present,'Episode audio generation or video building is active: '+', '.join(present))


@contextmanager
def preview_lock(folder):
    idle(folder)
    lock=folder/'.conversation-preview.lock'
    descriptor=None
    acquired=False
    try:
        try:descriptor=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError:raise ValueError('A conversation preview is already running; do not delete its lock.') from None
        acquired=True
        os.write(descriptor,str(os.getpid()).encode());os.close(descriptor);descriptor=None
        idle(folder)
        yield
    finally:
        if descriptor is not None:os.close(descriptor)
        # Never remove a lock created by a competing process.
        if acquired and lock.exists() and lock.read_text()==str(os.getpid()):lock.unlink()


def destinations(folder,replace):
    for name in OUTPUT_NAMES:
        target=folder/name
        require(not target.is_symlink(),'Preview destinations must not be symlinks: '+name)
        require(not target.exists() or replace,'A preview already exists; use --replace to replace the verified preview: '+name)
        require(not target.exists() or target.is_file(),'Preview destination is not a file: '+name)


def source_plan(args):
    key=episode_key(args.episode)
    output_base=(HERE/'output').resolve()
    folder=HERE/'output'/f'lesson-{key}-gemini'
    require(folder.is_dir(),'Finished episode output directory is missing: '+str(folder))
    require(folder.resolve().parent==output_base,'Episode output must stay in its canonical output directory.')
    idle(folder);destinations(folder,args.replace)
    timeline_file=folder/'lesson-timeline.json';export_file=folder/'lesson-export.json'
    require(timeline_file.is_file() and export_file.is_file(),'Finished timeline and export manifest are required.')
    require(timeline_file.resolve().parent==folder.resolve() and export_file.resolve().parent==folder.resolve(),'Source manifests must stay inside the episode output.')
    timeline=read(timeline_file);exported=read(export_file)
    require(timeline.get('speed')==1 and exported.get('speechSpeedChanged') is False,'Expected a completed lesson at normal speed 1.0.')
    require(timeline.get('fps')==FPS and exported.get('fps')==FPS,'Expected a 24fps source lesson.')
    require(episode_key(timeline.get('episodeNumber'))==key,'Source episode identity differs from --episode.')
    cast=timeline.get('cast',{})
    require(all(person.get('speed')==1 for person in cast.values()),'Every source cast member must be at normal speed.')
    filename=exported.get('file','')
    require(isinstance(filename,str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*\.mp4',filename),'Export filename must be a local MP4 basename.')
    video=folder/filename
    require(video.is_file() and video.resolve().parent==folder.resolve(),'Completed MP4 is missing or escapes the episode output.')
    require(isinstance(exported.get('sha256'),str) and digest(video)==exported['sha256'],'Completed MP4 hash differs from the export manifest.')
    scenes=timeline.get('scenes',[])
    require(isinstance(scenes,list) and scenes,'Source timeline has no scenes.')
    ids=set();cursor=0
    for scene in scenes:
        require(isinstance(scene.get('id'),str) and scene['id'] not in ids,'Scene IDs must be unique.')
        ids.add(scene['id']);frames=scene.get('frameCount')
        require(type(frames) is int and frames>0,'Every scene must have a positive integer frame count.')
        require(close(scene['start'],cursor/FPS) and close(scene['end'],(cursor+frames)/FPS),'Source scene timeline is not contiguous on the 24fps grid.')
        require(close(scene['duration'],frames/FPS),'Source scene duration differs from its frame count.')
        if scene.get('speaker'):require(scene.get('speed',1)==1,'A source speaking turn is not at normal speed.')
        cursor+=frames
    require(cursor==timeline.get('frameCount')==exported.get('frameCount'),'Source frame counts differ.')
    require(close(timeline['duration'],cursor/FPS) and close(exported['duration'],cursor/FPS),'Source duration differs from its frame count.')
    for name in (args.from_turn,args.through_turn):
        require(re.fullmatch(r'[a-z0-9][a-z0-9-]*',name) is not None,'Turn IDs must be simple lowercase local IDs.')
    begin=next((i for i,s in enumerate(scenes) if s['id']==args.from_turn),None)
    finish=next((i for i,s in enumerate(scenes) if s['id']==args.through_turn),None)
    require(begin is not None and finish is not None,'Both requested turn IDs must exist in the completed timeline.')
    require(begin<=finish,'--through-turn must follow --from-turn.')
    require(bool(scenes[begin].get('speaker')) and bool(scenes[finish].get('speaker')),'Preview endpoints must be complete speaking turns.')
    selected=scenes[begin:finish+1]
    require(all(s.get('phase') in ('body','opening') for s in selected),'Preview must contain conversation and its chapter cards, not a logo or closing.')
    start_frame=round(scenes[begin]['start']*FPS)
    frames=sum(s['frameCount'] for s in selected);duration=frames/FPS
    end_frame=start_frame+frames
    require(end_frame==round(scenes[finish]['end']*FPS),'Selected frame range is not continuous.')
    source_info=probe(video)
    v=next(s for s in source_info['streams'] if s['codec_type']=='video')
    a=next(s for s in source_info['streams'] if s['codec_type']=='audio')
    numerator,denominator=map(int,v['avg_frame_rate'].split('/'))
    require(numerator/denominator==FPS and int(v['nb_frames'])==cursor,'Completed source video does not match its 24fps timeline.')
    require(int(a['sample_rate'])==48000 and int(a['channels'])==2,'Source audio must be 48kHz stereo.')
    require(abs(float(v['duration'])-timeline['duration'])<1/FPS,'Source video duration differs from its timeline.')
    require(abs(float(a['duration'])-timeline['duration'])<.03,'Source audio duration differs from its timeline.')
    warnings=[] if 40<=duration<=70 else ['The continuous excerpt is outside the suggested 40–70 seconds; keeping the complete question and answer takes priority.']
    return {
        'episodeKey':key,'episodeNumber':timeline['episodeNumber'],'folder':folder,'video':video,
        'timelineFile':timeline_file,'exportFile':export_file,'timeline':timeline,
        'sourceSha256':exported['sha256'],'timelineSha256':digest(timeline_file),'exportSha256':digest(export_file),
        'sourceStartFrame':start_frame,'sourceEndFrame':end_frame,'frameCount':frames,
        'durationSeconds':duration,'sourceRangeSeconds':[start_frame/FPS,end_frame/FPS],
        'selectedScenes':selected,'turnIds':[s['id'] for s in selected if s.get('speaker')],
        'chapterCardIds':[s['id'] for s in selected if not s.get('speaker')],
        'width':int(v['width']),'height':int(v['height']),'warnings':warnings,
    }


def unchanged(plan):
    idle(plan['folder'])
    require(digest(plan['video'])==plan['sourceSha256'],'Source MP4 changed while the preview was being made.')
    require(digest(plan['timelineFile'])==plan['timelineSha256'],'Source timeline changed while the preview was being made.')
    require(digest(plan['exportFile'])==plan['exportSha256'],'Source export manifest changed while the preview was being made.')


def validate_clip(path,plan):
    info=probe(path)
    v=next(s for s in info['streams'] if s['codec_type']=='video')
    a=next(s for s in info['streams'] if s['codec_type']=='audio')
    require(v['codec_name']=='h264' and int(v['nb_frames'])==plan['frameCount'],'Preview video codec/frame count differs.')
    require(v['avg_frame_rate']=='24/1' and v['time_base']=='1/12288','Preview must use the exact 24fps frame grid.')
    require(int(v['duration_ts'])==plan['frameCount']*512 and int(v.get('start_pts',0))==0,'Preview video timestamps differ.')
    require((int(v['width']),int(v['height']))==(plan['width'],plan['height']),'Preview dimensions changed.')
    require(a['codec_name']=='aac' and int(a['sample_rate'])==48000 and int(a['channels'])==2,'Preview audio must be AAC, 48kHz stereo.')
    require(abs(float(a.get('start_time',0)))<.03 and abs(float(a['duration'])-plan['durationSeconds'])<.03,'Preview audio timing differs.')
    require(abs(float(info['format']['duration'])-plan['durationSeconds'])<.03,'Preview container duration differs.')
    run(['ffmpeg','-v','error','-xerror','-i',str(path),'-map','0:v:0','-map','0:a:0','-f','null','-'])
    return {'status':'passed','fullAudioVideoDecode':'passed','videoFrameCount':plan['frameCount'],
            'fps':FPS,'timeBase':'1/12288','videoStartPTS':0,'audioSampleRate':48000,
            'audioChannels':2,'normalSpeed':1,'cutsWithinExcerpt':False}


def player(plan):
    esc=html.escape
    title=f'第{plan["episodeNumber"]}回：連続した会話で確認'
    topic=plan['timeline'].get('title','egakumi勉強会')
    length=round(plan['durationSeconds'],2)
    return f'''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>body{{background:#030710;color:#e5f7fc;font:17px/1.8 -apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif;max-width:1300px;margin:32px auto;padding:0 24px}}h1{{font-size:28px}}p{{color:#a6bfcd}}video{{width:100%;background:#000;border:1px solid #285168;border-radius:10px}}a{{color:#91e6e0}}</style><h1>{esc(title)}</h1><p>{esc(str(topic))} / {length}秒。質問から回答まで途中を飛ばさず、全員通常速度で再生します。</p><video controls playsinline preload="metadata" src="conversation-preview.mp4" aria-label="{esc(title,quote=True)}"></video><p><a href="index.html">全編の再生ページへ</a>　<a href="conversation-preview.mp4" download>プレビューを保存</a>　<a href="conversation-preview.json">制作記録</a></p></html>'''


def make(plan,args):
    folder=plan['folder']
    with preview_lock(folder):
        destinations(folder,args.replace);unchanged(plan)
        with tempfile.TemporaryDirectory(prefix='.conversation-preview-',dir=folder) as temporary:
            temp=Path(temporary);video=temp/OUTPUT_NAMES[0]
            command=['ffmpeg','-v','error','-y','-ss',str(plan['sourceStartFrame']/FPS),'-i',str(plan['video']),
                '-t',str(plan['durationSeconds']),'-frames:v',str(plan['frameCount']),'-map','0:v:0','-map','0:a:0',
                '-vf','setpts=PTS-STARTPTS','-af',f'atrim=duration={plan["durationSeconds"]},asetpts=PTS-STARTPTS',
                '-c:v','libx264','-threads','2','-preset','fast','-crf','19','-pix_fmt','yuv420p',
                '-c:a','aac','-b:a','192k','-ar','48000','-ac','2','-video_track_timescale','12288',
                '-movie_timescale','12288','-movflags','+faststart',str(video)]
            run(command)
            verification=validate_clip(video,plan)
            record={
                'status':'passed','createdAt':datetime.now(timezone.utc).isoformat(),
                'episodeKey':plan['episodeKey'],'episodeNumber':plan['episodeNumber'],
                'file':OUTPUT_NAMES[0],'sha256':digest(video),'sourceFile':plan['video'].name,
                'sourceSha256':plan['sourceSha256'],'sourceTimelineSha256':plan['timelineSha256'],
                'sourceExportSha256':plan['exportSha256'],'sourceRangeSeconds':plan['sourceRangeSeconds'],
                'sourceStartFrame':plan['sourceStartFrame'],'sourceEndFrame':plan['sourceEndFrame'],
                'frameCount':plan['frameCount'],'durationSeconds':plan['durationSeconds'],
                'fromTurn':args.from_turn,'throughTurn':args.through_turn,'turnIds':plan['turnIds'],
                'chapterCardIds':plan['chapterCardIds'],'continuousConversation':True,'cutsWithinExcerpt':False,
                'speed':1,'speechSpeedChanged':False,'pitchChanged':False,
                'originalVoiceAndBGMContinuous':True,'networkRequestsMade':False,
                'authenticationRead':False,'additionalSynthesisRequests':0,'warnings':plan['warnings'],
                'verification':verification,
            }
            (temp/OUTPUT_NAMES[1]).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
            (temp/OUTPUT_NAMES[2]).write_text(player(plan))
            unchanged(plan);destinations(folder,args.replace)
            # Existing files remain usable until the completed temporary clip passes
            # every check. Each replacement is atomic; publish the manifest last.
            for name in (OUTPUT_NAMES[0],OUTPUT_NAMES[2],OUTPUT_NAMES[1]):os.replace(temp/name,folder/name)
            return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode',required=True)
    parser.add_argument('--from-turn',required=True)
    parser.add_argument('--through-turn',required=True)
    parser.add_argument('--replace',action='store_true',help='Replace an existing preview only after the new temporary preview passes verification.')
    parser.add_argument('--dry-run',action='store_true',help='Read and verify the source and selected range without writing files or rendering.')
    args=parser.parse_args();plan=source_plan(args)
    if args.dry_run:
        print(json.dumps({k:plan[k] for k in ['episodeKey','sourceSha256','sourceRangeSeconds','sourceStartFrame','sourceEndFrame','frameCount','durationSeconds','turnIds','chapterCardIds','warnings']}|{'dryRun':True,'networkRequestsMade':False,'authenticationRead':False,'filesWritten':False},ensure_ascii=False,indent=2))
    else:print(json.dumps(make(plan,args),ensure_ascii=False,indent=2))


def interrupted(signum,frame):
    raise KeyboardInterrupt


if __name__=='__main__':
    signal.signal(signal.SIGTERM,interrupted)
    try:main()
    except (ValueError,KeyError,OSError,StopIteration,json.JSONDecodeError) as error:
        raise SystemExit('Stopped: '+str(error)) from None
    except KeyboardInterrupt:
        raise SystemExit('Stopped: preview interrupted; existing completed files were preserved.') from None
