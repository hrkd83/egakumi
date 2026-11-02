#!/usr/bin/env python3
"""Extract episode 07 review frames from its finished MP4, without synthesis.

Run only after build-lesson.mjs has completed. Uses the saved timeline rather
than fixed timestamps, verifies the exported MP4 hash, and writes solely to
output/lesson-07-gemini/final-qa/.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parents[2]
OUT = HERE / 'output' / 'lesson-07-gemini'


def digest(file):
    result = hashlib.sha256()
    with file.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def main():
    if any((OUT / name).exists() for name in (
            '.lesson-build.lock', '.gemini-generator.lock')):
        raise ValueError('Episode 07 is still generating or rendering.')
    timeline = json.loads((OUT / 'lesson-timeline.json').read_text())
    exported = json.loads((OUT / 'lesson-export.json').read_text())
    if timeline.get('episodeKey') != '07' or timeline.get('episodeNumber') != 7:
        raise ValueError('Expected the finished episode 07 timeline.')
    video = (OUT / exported['file']).resolve()
    if video.parent != OUT.resolve() or not video.is_file():
        raise ValueError('Finished MP4 is missing or outside the episode output.')
    video_hash = digest(video)
    if video_hash != exported['sha256']:
        raise ValueError('Finished MP4 differs from lesson-export.json.')
    scenes = {scene['id']: scene for scene in timeline['scenes']}
    frames = []

    def select(label, scene, reason, *, time=None, settled=False, contains=None):
        cue = None
        if time is None:
            cues = [item for item in timeline['subtitleCues']
                    if item['turnId'] == scene['id']]
            if settled:
                cues = [item for item in cues
                        if (item['start'] + item['end']) / 2 >= scene['start'] + .9]
            if contains:
                cues = [item for item in cues
                        if contains in ''.join(item['lines'])]
            if not cues:
                raise ValueError('No suitable subtitle cue: ' + scene['id'])
            cue = cues[0]
            time = (cue['start'] + cue['end']) / 2
        if not scene['start'] <= time < scene['end']:
            raise ValueError('QA timestamp is outside its scene: ' + scene['id'])
        frames.append({
            'file': f'{len(frames) + 1:02}-{label}.png',
            'timeSeconds': time,
            'sceneId': scene['id'],
            'reason': reason,
            'chapterId': scene.get('chapterId'),
            'speaker': scene.get('speaker'),
            'subtitleLines': cue['lines'] if cue else [],
            'subtitleCue': {'start': cue['start'], 'end': cue['end']} if cue else None,
        })

    logo = scenes['logo']
    select('logo-intro', logo, '共通導入ロゴを確認する。',
           time=(logo['start'] + logo['end']) / 2)
    establish = scenes['cast-establish']
    select('cast-establish', establish, '3人の顔・名前・英語の役割を同時に確認する。',
           time=(establish['start'] + establish['end']) / 2)
    for speaker in ('mako', 'manabu', 'ikumi'):
        scene = next(item for item in timeline['scenes']
                     if item['phase'] == 'opening' and item.get('speaker') == speaker)
        select('opening-' + speaker, scene,
               'カメラ移動後の話者中央寄せ、顔、名前、字幕を確認する。',
               settled=True)

    # The IDs identify authored concepts; all sample times come from their cues.
    examples = [
        (8, 'ikumi', 'u1-backend-path', 'U1のAppGW→FW→APIMとTCP 80。', 'TCP'),
        (11, 'ikumi', 'connection-identity', '同じ通信を特定する時刻・送信元・宛先・ポート。', None),
        (21, 'ikumi', 'dns-answer', '送信元の名前解決とAPIM内部IPを照合する。', '内部IP'),
        (24, 'ikumi', 'return-route', 'AppGW側とAPIM側の往復経路を確認する。', None),
        (29, 'ikumi', 'nsg-rules', 'U1の送信NSG・受信NSG・FW規則を照合する。', 'allow-apim-http'),
        (33, 'ikumi', 'network-log', '主例に対応するAZFWNetworkRuleと通信条件。', 'AZFWNetworkRule'),
        (40, 'ikumi', 'default-action', 'Default Actionと適用規則を確認する。', 'Default Action'),
        (46, 'ikumi', 'pe-direct-path', 'APIM→Foundry PEの直接経路とFW非経由を確認する。', '443'),
        (51, 'ikumi', 'triage-example', '一つの接続でDenyとDefault Actionを追う演習。', 'Deny'),
        (61, 'ikumi', 'log-destinations', 'LAWと日本側監査Storageへの保存先。', None),
        (67, 'ikumi', 'notification-conditions', 'ログ保存と通知条件、Action Groupの配線を区別する。', None),
        (68, 'ikumi', 'metric-alerts', 'AppGWとFirewallのメトリック通知の条件。', None),
        (75, 'ikumi', 'log-alert-flag', 'enableLogAlertsと他の収集・通知設定を区別する。', None),
        (77, 'mako', 'quiz-1', '条件・問い・判断のカードでPE通信を確認する。', 'Foundry'),
        ('080r2', 'mako', 'quiz-2', '条件・問い・判断のカードでFW Allowの意味を確認する。', '認証'),
        ('083r2', 'mako', 'quiz-3', '条件・問い・判断のカードでDenyと通知を確認する。', 'enableLogAlerts'),
        (87, 'ikumi', 'summary', '同じ通信について照合する順序のまとめ。', None),
        (90, 'ikumi', 'next-episode', '第8回の基盤管理者接続・作業実行への案内。', None),
    ]
    for number, speaker, label, reason, contains in examples:
        select(label, scenes[f'lesson07-turn-{str(number).zfill(3)}-{speaker}'], reason,
               contains=contains)
    closing = scenes['closing']
    select('closing-middle', closing, '共通クロージング中盤のロゴと余白。',
           time=(closing['start'] + closing['end']) / 2)
    select('closing-end', closing, '最終フレーム付近の暗転と画面終了。',
           time=closing['end'] - 1 / timeline['fps'])

    qa = OUT / 'final-qa'
    qa.mkdir(exist_ok=True)
    for frame in frames:
        target = qa / frame['file']
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(frame['timeSeconds']),
                        '-i', str(video), '-frames:v', '1', '-update', '1', str(target)],
                       check=True)
        if not target.is_file() or target.stat().st_size == 0:
            raise ValueError('QA frame was not extracted: ' + frame['file'])
    manifest = {
        'episodeKey': '07',
        'video': exported['file'],
        'videoSha256': video_hash,
        'durationSeconds': timeline['duration'],
        'selectionPolicy': '字幕cue中央。openingはカメラ移動完了後。ロゴは中盤と終了直前。全8章を含む25枚。',
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
