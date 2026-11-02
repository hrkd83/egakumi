#!/usr/bin/env python3
"""Extract representative frames from the completed episode 18 MP4."""
import hashlib
import json
from pathlib import Path
import subprocess

EPISODE = Path(__file__).resolve().parent
OUT = EPISODE.parent.parent / 'output' / 'lesson-18-gemini'
DEST = EPISODE / 'final-frames'

def read(path):
    return json.loads(path.read_text())

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    for name in ('.lesson-build.lock', '.gemini-generator.lock'):
        if (OUT / name).exists():
            raise ValueError('Episode is still busy: ' + name)
    timeline, export = read(OUT / 'lesson-timeline.json'), read(OUT / 'lesson-export.json')
    assert timeline['episodeKey'] == '18'
    assert export['file'] == 'egakumi-study-18.mp4'
    video = OUT / export['file']
    assert digest(video) == export['sha256']
    assert timeline['fps'] == 24 and timeline['speed'] == 1
    assert export['speechSpeedChanged'] is False
    selections, seen = [], set()
    for scene in timeline['scenes']:
        visual = scene.get('visual', {})
        view = visual.get('viewId')
        chapter = scene.get('chapterId', '')
        quiz = 'check' in chapter or 'quiz' in chapter
        highlighted = bool(visual.get('highlightFields') or visual.get('highlightRows'))
        key = (view, highlighted)
        special = scene['phase'] != 'body' or quiz
        if not special and (not scene.get('speaker') or key in seen):
            continue
        seen.add(key)
        offset = min(2, max(0, scene['duration'] - 1 / 24))
        frame = min(round((scene['start'] + offset) * 24), round(scene['end'] * 24) - 1)
        selections.append({'sceneId': scene['id'], 'viewId': view,
                           'speaker': scene.get('speaker'), 'frame': frame,
                           'seconds': frame / 24, 'file': scene['id'] + '.png'})
        if scene['phase'] == 'closing':
            frame = round(scene['end'] * 24) - 1
            selections.append({'sceneId': scene['id'], 'frame': frame,
                               'seconds': frame / 24, 'file': 'closing-end.png'})
    DEST.mkdir(exist_ok=True)
    for row in selections:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(row['seconds']),
                        '-i', str(video), '-frames:v', '1', str(DEST / row['file'])], check=True)
    report = {'sourceFile': str(video), 'sourceSha256': export['sha256'],
              'fps': 24, 'frames': selections}
    (DEST / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'extractedFrames': len(selections), 'destination': str(DEST)}))

if __name__ == '__main__':
    main()
