#!/usr/bin/env python3
"""Extract episode 05 QA frames from the finished MP4; no synthesis or rendering."""
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parents[2]
OUT = HERE / 'output' / 'lesson-05-gemini'


def main():
    timeline = json.loads((OUT / 'lesson-timeline.json').read_text())
    exported = json.loads((OUT / 'lesson-export.json').read_text())
    if timeline.get('episodeKey') != '05' or timeline.get('episodeNumber') != 5:
        raise ValueError('Expected the finished episode 05 timeline.')
    video = (OUT / exported['file']).resolve()
    if video.parent != OUT.resolve() or not video.is_file():
        raise ValueError('Finished MP4 is missing or outside the episode output.')
    if (OUT / '.lesson-build.lock').exists():
        raise ValueError('Episode 05 is still rendering; wait for the build to finish.')
    scenes = {scene['id']: scene for scene in timeline['scenes']}
    frames = []

    def select(label, scene, reason, *, time=None, settled=False):
        cue = None
        if time is None:
            cues = [cue for cue in timeline['subtitleCues']
                    if cue['turnId'] == scene['id']]
            if settled:
                cues = [cue for cue in cues
                        if (cue['start'] + cue['end']) / 2 >= scene['start'] + .9]
            if not cues:
                raise ValueError('No suitable subtitle cue: ' + scene['id'])
            cue = cues[0]
            time = (cue['start'] + cue['end']) / 2
        if not scene['start'] <= time < scene['end']:
            raise ValueError('QA timestamp is outside its scene: ' + scene['id'])
        frames.append({'file': label + '.png', 'timeSeconds': time,
                       'sceneId': scene['id'], 'reason': reason,
                       'subtitleLines': cue['lines'] if cue else [],
                       'subtitleCue': {'start': cue['start'], 'end': cue['end']}
                       if cue else None})

    establish = scenes['cast-establish']
    select('01-cast-establish', establish, '3人の顔・名前・英語の役割を同時に確認する。',
           time=(establish['start'] + establish['end']) / 2)
    for index, speaker in enumerate(('mako', 'manabu', 'ikumi'), 2):
        scene = next(scene for scene in timeline['scenes']
                     if scene['phase'] == 'opening' and scene.get('speaker') == speaker)
        select(f'{index:02}-opening-{speaker}', scene,
               '話者へのカメラ移動が終わり、顔・名前・字幕が読めることを確認する。',
               settled=True)

    examples = [
        (21, 'ikumi', 'network-first', 'Networkの一致で判定が終了する図と字幕。'),
        (28, 'ikumi', 'priority-example', '仮の優先度の比較と種類の評価順。'),
        (33, 'ikumi', 'internal-relay', 'AppGWからAPIMへの内部中継と条件。'),
        (44, 'ikumi', 'storage', 'APIMからStorageへの基盤通信。'),
        (60, 'ikumi', 'dns', '独自DNSの条件と説明字幕。'),
        (66, 'mako', 'quiz-1', 'StorageのNetwork許可についての確認問題。'),
        (68, 'mako', 'quiz-2', 'FQDNを指定したNTPについての確認問題。'),
        (70, 'mako', 'quiz-3', 'SQL PEの直接経路についての確認問題。'),
        (79, 'manabu', 'summary', '3つの到達点とまとめの字幕。'),
        (80, 'mako', 'next-episode', '次回案内の字幕と終了前の画面。'),
    ]
    for index, (number, speaker, label, reason) in enumerate(examples, 5):
        select(f'{index:02}-{label}', scenes[f'lesson05-turn-{number:03}-{speaker}'], reason)
    closing = scenes['closing']
    select('15-closing-middle', closing, '共通クロージング中盤のロゴと余白。',
           time=(closing['start'] + closing['end']) / 2)
    select('16-closing-end', closing, '最終フレーム付近の暗転と画面終了。',
           time=closing['end'] - 1 / timeline['fps'])

    qa = OUT / 'qa-final'
    qa.mkdir(exist_ok=True)
    for frame in frames:
        target = qa / frame['file']
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(frame['timeSeconds']),
                        '-i', str(video), '-frames:v', '1', '-update', '1', str(target)],
                       check=True)
        if not target.is_file() or target.stat().st_size == 0:
            raise ValueError('QA frame was not extracted: ' + frame['file'])
    manifest = {'episodeKey': '05', 'video': exported['file'],
                'videoSha256': exported['sha256'], 'durationSeconds': timeline['duration'],
                'selectionPolicy': '字幕のある場面はcue中央。openingは移動完了後。',
                'frames': frames}
    (qa / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'directory': str(qa), 'frames': len(frames),
                      'manifest': str(qa / 'manifest.json')}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, StopIteration, FileNotFoundError,
            json.JSONDecodeError, subprocess.CalledProcessError) as error:
        raise SystemExit('QA extraction stopped: ' + str(error))
