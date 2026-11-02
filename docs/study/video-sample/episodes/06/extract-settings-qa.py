#!/usr/bin/env python3
"""Extract final episode 06 settings-view QA frames without synthesis."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


EP = Path(__file__).resolve().parent
OUT = EP.parents[1] / 'output' / 'lesson-06-gemini'


def read(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def extract(video, target, seconds):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(seconds),
                    '-i', str(video), '-frames:v', '1', '-update', '1', str(target)],
                   check=True)
    require(target.is_file() and target.stat().st_size > 0,
            'Frame extraction returned no image: ' + target.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-preview', action='store_true',
                        help='Require the current uninterrupted conversation preview and sample it too.')
    args = parser.parse_args()
    for name in ('.lesson-build.lock', '.gemini-generator.lock', '.conversation-preview.lock'):
        require(not (OUT / name).exists(), 'Episode work is still active: ' + name)
    script, timeline, exported = read(EP / 'script.json'), read(OUT / 'lesson-timeline.json'), read(OUT / 'lesson-export.json')
    require(script.get('visualStyle') == 'teaching-settings-v2', 'Expected teaching-settings-v2 script')
    require(timeline.get('episodeKey') == '06' and timeline.get('speed') == 1,
            'Expected completed episode 06 at normal speed')
    video = (OUT / exported['file']).resolve()
    require(video.parent == OUT.resolve() and video.is_file(), 'MP4 must stay in the episode output')
    video_hash = digest(video)
    require(video_hash == exported['sha256'], 'Final MP4 differs from the export manifest')
    scenes = {row['id']: row for row in timeline['scenes']}
    frames, preview_frames = [], []
    qa = OUT / 'qa-settings-v2'
    qa.mkdir(exist_ok=True)

    targets = [
        (63, 'mako', 'expected-question', '期待値と実測の問い。赤枠がないことを確認。'),
        (64, 'ikumi', 'expected-answer', '期待値の出力・環境変数を赤枠で示す。'),
        (65, 'ikumi', 'measurement-answer', '確認先の許可と自動実測なしを示す。'),
        (66, 'manabu', 'expected-reflection', '同じ設定欄のまま期待値と実測の違いを確かめる。'),
        (75, 'manabu', 'quiz-1-answer', '確認1の回答で設定生成・配備状態を強調。'),
        (78, 'ikumi', 'quiz-2-answer', '確認2の回答で非SNATとAllowの違いを強調。'),
        (81, 'ikumi', 'quiz-3-answer', '確認3の回答で未実装と測定の根拠を強調。'),
    ]
    for number, speaker, label, reason in targets:
        turn_id = f'lesson06-turn-{number:03}-{speaker}'
        scene = scenes[turn_id]
        cues = [row for row in timeline['subtitleCues'] if row['turnId'] == turn_id]
        require(cues, 'Missing subtitle cue: ' + turn_id)
        cue = cues[0]
        seconds = (cue['start'] + cue['end']) / 2
        require(scene['start'] <= seconds < scene['end'], 'Frame time is outside its scene')
        file = f'{len(frames) + 1:02}-{label}.png'
        extract(video, qa / file, seconds)
        visual = scene['visual']
        frames.append({'file': file, 'timeSeconds': seconds, 'turnId': turn_id,
                       'viewId': visual.get('viewId'), 'layout': visual['layout'],
                       'highlightRows': visual.get('highlightRows', []),
                       'highlightFields': visual.get('highlightFields', []),
                       'subtitleLines': cue['lines'], 'reason': reason})

    preview_record = None
    if args.with_preview:
        preview_record = read(OUT / 'conversation-preview.json')
        require(preview_record.get('status') == 'passed' and preview_record.get('sourceSha256') == video_hash,
                'Conversation preview belongs to a different final MP4')
        require(preview_record.get('continuousConversation') is True and
                preview_record.get('cutsWithinExcerpt') is False and preview_record.get('speed') == 1,
                'Expected an uninterrupted normal-speed question and answer')
        preview_video = OUT / 'conversation-preview.mp4'
        require(digest(preview_video) == preview_record['sha256'], 'Conversation preview hash differs')
        duration = preview_record['durationSeconds']
        start = preview_record['sourceRangeSeconds'][0]
        for seconds in (0, 10, 20, 30, 40, 48):
            require(seconds < duration, 'Preview is too short for the requested QA samples')
            file = f'preview-{seconds:02}.png'
            extract(preview_video, qa / file, seconds)
            source_seconds = start + seconds
            scene = next(row for row in timeline['scenes']
                         if row['start'] <= source_seconds < row['end'])
            visual = scene['visual']
            preview_frames.append({'file': file, 'previewTimeSeconds': seconds,
                                   'sourceTimeSeconds': source_seconds, 'turnId': scene['id'],
                                   'viewId': visual.get('viewId'),
                                   'highlightRows': visual.get('highlightRows', []),
                                   'highlightFields': visual.get('highlightFields', []),
                                   'reason': '同じ連続動画内で設定欄・縮尺・赤枠の変化を確認する。'})
        require(len({row['viewId'] for row in preview_frames}) == 1,
                'Expected one fixed view throughout this conversation preview')

    manifest = {'episodeKey': '06', 'visualStyle': 'teaching-settings-v2',
                'video': video.name, 'videoSha256': video_hash,
                'durationSeconds': timeline['duration'], 'frames': frames,
                'conversationPreview': None if preview_record is None else {
                    'file': 'conversation-preview.mp4', 'sha256': preview_record['sha256'],
                    'sourceRangeSeconds': preview_record['sourceRangeSeconds'],
                    'durationSeconds': preview_record['durationSeconds'],
                    'cutsWithinExcerpt': False, 'speed': 1, 'frames': preview_frames},
                'scope': 'Extraction metadata only. Visual and listening judgements are recorded separately.'}
    (qa / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'directory': str(qa), 'mainFrames': len(frames),
                      'previewFrames': len(preview_frames), 'videoSha256': video_hash}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, StopIteration, FileNotFoundError,
            json.JSONDecodeError, subprocess.CalledProcessError) as error:
        raise SystemExit('Settings QA extraction stopped: ' + str(error))
