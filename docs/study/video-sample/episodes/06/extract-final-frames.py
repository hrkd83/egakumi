#!/usr/bin/env python3
"""Extract episode 06 QA frames from its finished MP4; stdlib + ffmpeg only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parents[2]
OUT = HERE / 'output' / 'lesson-06-gemini'


def digest(file):
    value = hashlib.sha256()
    with file.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    if (OUT / '.lesson-build.lock').exists():
        raise ValueError('Episode 06 is still rendering; wait for the build to finish.')
    timeline = json.loads((OUT / 'lesson-timeline.json').read_text())
    exported = json.loads((OUT / 'lesson-export.json').read_text())
    if timeline.get('episodeKey') != '06' or timeline.get('episodeNumber') != 6:
        raise ValueError('Expected the finished episode 06 timeline.')
    video = (OUT / exported['file']).resolve()
    if video.parent != OUT.resolve() or not video.is_file():
        raise ValueError('Finished MP4 is missing or outside the episode output.')
    video_hash = digest(video)
    if video_hash != exported['sha256']:
        raise ValueError('Finished MP4 differs from lesson-export.json.')
    scenes = {scene['id']: scene for scene in timeline['scenes']}
    frames = []

    def select(label, scene, reason, *, time=None, settled=False):
        cue = None
        if time is None:
            cues = [item for item in timeline['subtitleCues']
                    if item['turnId'] == scene['id']]
            if settled:
                cues = [item for item in cues
                        if (item['start'] + item['end']) / 2 >= scene['start'] + .9]
            if not cues:
                raise ValueError('No suitable subtitle cue: ' + scene['id'])
            cue = cues[0]
            time = (cue['start'] + cue['end']) / 2
        if not scene['start'] <= time < scene['end']:
            raise ValueError('QA timestamp is outside its scene: ' + scene['id'])
        frames.append({
            'file': f'{len(frames) + 1:02}-{label}.png',
            'timeSeconds': time, 'sceneId': scene['id'], 'reason': reason,
            'chapterId': scene.get('chapterId'),
            'subtitleLines': cue['lines'] if cue else [],
            'subtitleCue': {'start': cue['start'], 'end': cue['end']} if cue else None,
        })

    logo = scenes['logo']
    select('logo-intro', logo, '共通導入ロゴの中盤を確認する。',
           time=(logo['start'] + logo['end']) / 2)
    establish = scenes['cast-establish']
    select('cast-establish', establish, '3人の顔・名前・英語の役割を同時に確認する。',
           time=(establish['start'] + establish['end']) / 2)
    for speaker in ('mako', 'manabu', 'ikumi'):
        scene = next(item for item in timeline['scenes']
                     if item['phase'] == 'opening' and item.get('speaker') == speaker)
        select('opening-' + speaker, scene,
               'カメラ移動後に話者が中央に収まり、顔・名前・字幕が読めることを確認する。',
               settled=True)

    examples = [
        (5, 'manabu', 'purpose', '外向き通信の三つの視点と説明字幕。'),
        (10, 'ikumi', 'web-dependencies', 'Web依存先へ至る送信元・Firewall・接続先の図。'),
        (24, 'ikumi', 'generated-settings', '入力JSON・設定生成・配備後の許可先を区別する図。'),
        (31, 'ikumi', 'api-fqdn-addition', 'API公開時のホスト抽出と既存許可先への追加。'),
        (44, 'ikumi', 'ingress-egress-ip', 'AppGW入口IPとFirewall出口IPの区別。'),
        (49, 'ikumi', 'public-snat', '公開宛てで相手から見える送信元IPの変換。'),
        (52, 'ikumi', 'private-network-snat', 'Networkのprivate宛て非SNATと送信元保持。'),
        (58, 'ikumi', 'application-snat', 'ApplicationとNetworkのSNATの違い。'),
        (64, 'ikumi', 'expected-ip-settings', '期待値の受け渡しと確認先の許可を別カードで表示する。'),
        (68, 'ikumi', 'measure-origin', '対象の実行場所から測定する条件と字幕。'),
        (74, 'mako', 'quiz-1', '入力の空配列を最終許可と判断できるかの確認問題。'),
        (77, 'mako', 'quiz-2', '非SNAT宛先範囲と通信許可を区別する確認問題。'),
        (80, 'mako', 'quiz-3', '期待値・確認先の用意と自動実測を区別する確認問題。'),
        (85, 'ikumi', 'summary', '4項目のまとめと説明字幕。'),
        (86, 'mako', 'next-episode', '第7回への接続と終了前の字幕。'),
    ]
    for number, speaker, label, reason in examples:
        select(label, scenes[f'lesson06-turn-{number:03}-{speaker}'], reason)
    closing = scenes['closing']
    select('closing-middle', closing, '共通クロージング中盤のロゴと余白。',
           time=(closing['start'] + closing['end']) / 2)
    select('closing-end', closing, '最終フレーム付近の暗転と画面終了。',
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
    manifest = {
        'episodeKey': '06', 'video': exported['file'], 'videoSha256': video_hash,
        'durationSeconds': timeline['duration'],
        'selectionPolicy': '字幕のある場面はcue中央。openingはカメラ移動完了後。全9章を含む22枚。',
        'frames': frames,
    }
    (qa / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'directory': str(qa), 'frames': len(frames),
                      'manifest': str(qa / 'manifest.json')}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, StopIteration, FileNotFoundError,
            json.JSONDecodeError, subprocess.CalledProcessError) as error:
        raise SystemExit('QA extraction stopped: ' + str(error))
