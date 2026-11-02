#!/usr/bin/env python3
"""Extract 19 episode 17 QA stills from its completed, hash-verified MP4.

Requires a matching lesson-timeline.json, lesson-export.json and finished master.
Writes only episodes/17/final-frames; never renders or changes source/audio files.
Extraction is not visual review. Run only after the final export is complete.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import struct
import subprocess
from zoneinfo import ZoneInfo


EPISODE = Path(__file__).resolve().parent
OUTPUT = EPISODE.parent.parent / 'output' / 'lesson-17-gemini'
DESTINATION = EPISODE / 'final-frames'
SCRIPT = EPISODE / 'script.json'
FPS = 24


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def load(path):
    return json.loads(path.read_text())


def run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    require(result.returncode == 0, f'{command[0]} failed: {result.stderr[-2000:]}')
    return result.stdout


def validate_visual(scene, turn):
    expected = turn.get('visual')
    actual = scene.get('visual')
    require(isinstance(expected, dict) and isinstance(actual, dict),
            f'Visual is absent from script or timeline: {turn["id"]}')
    actual = dict(actual)
    if expected.get('layout') == 'svg':
        # build-lesson prepares SVG bytes into these two timeline-only fields.
        reference = expected.get('svg')
        require(isinstance(reference, str) and not Path(reference).is_absolute(),
                f'Expected an episode-local source SVG: {turn["id"]}')
        svg_file = (EPISODE / reference).resolve()
        require(svg_file.is_relative_to(EPISODE.resolve()) and svg_file.is_file(),
                f'Source SVG is missing or escapes the episode: {turn["id"]}')
        svg_bytes = svg_file.read_bytes()
        require(actual.get('svgSha256') == hashlib.sha256(svg_bytes).hexdigest(),
                f'Timeline SVG hash differs from source bytes: {turn["id"]}')
        expected_uri = 'data:image/svg+xml;base64,' + base64.b64encode(svg_bytes).decode('ascii')
        require(actual.get('svgDataUri') == expected_uri,
                f'Timeline SVG data URI differs from source bytes: {turn["id"]}')
        del actual['svgSha256']
        del actual['svgDataUri']
    require(actual == expected, f'Timeline visual differs from current script: {turn["id"]}')


def source():
    for lock in ('.lesson-build.lock', '.gemini-generator.lock'):
        require(not (OUTPUT / lock).exists(), f'Episode is still busy: {lock}')
    timeline_file = OUTPUT / 'lesson-timeline.json'
    export_file = OUTPUT / 'lesson-export.json'
    require(timeline_file.is_file() and export_file.is_file(),
            'Completed lesson-timeline.json and lesson-export.json are required.')
    timeline, exported, script = load(timeline_file), load(export_file), load(SCRIPT)
    require(timeline.get('episodeKey') == script.get('episodeKey') == '17'
            and timeline.get('episodeNumber') == script.get('episodeNumber') == 17,
            'Expected episode 17 source and timeline.')
    require(timeline.get('fps') == exported.get('fps') == FPS,
            'Expected a completed 24fps export.')
    require(timeline.get('width') == exported.get('width') == 1600
            and timeline.get('height') == exported.get('height') == 900,
            'Timeline/export dimensions differ from 1600x900.')
    require(timeline.get('speed') == 1 and exported.get('speechSpeedChanged') is False,
            'Expected normal-speed speech without a speed conversion.')
    require(timeline.get('synthesisModel') == exported.get('synthesisModel') == 'gemini-3.8-flash-tts',
            'Unexpected synthesis model in the completed export.')
    require(timeline.get('frameCount') == exported.get('frameCount')
            and isinstance(timeline.get('frameCount'), int) and timeline['frameCount'] > 0,
            'Timeline/export frame counts differ or are incomplete.')
    expected_duration = timeline['frameCount'] / FPS
    require(abs(timeline.get('duration', 0) - expected_duration) < .001
            and abs(exported.get('duration', 0) - expected_duration) < .001,
            'Timeline/export duration does not match its frame count.')
    require(exported.get('file') == 'egakumi-study-17.mp4', 'Unexpected episode export filename.')
    video = OUTPUT / exported['file']
    require(video.resolve().parent == OUTPUT.resolve() and video.is_file()
            and video.stat().st_size > 0, 'Completed episode master is missing or outside its output.')
    require(digest(video) == exported.get('sha256'), 'MP4 SHA-256 differs from lesson-export.json.')
    scenes = timeline.get('scenes', [])
    require(len({scene['id'] for scene in scenes}) == len(scenes), 'Duplicate scene IDs in timeline.')
    turns = script.get('turns', [])
    require(len(turns) == 75, 'Expected the reviewed 75-turn episode 17 script.')
    by_id = {scene['id']: scene for scene in scenes}
    for turn in turns:
        scene = by_id.get(turn['id'])
        require(scene is not None, f'Current script turn is absent from the timeline: {turn["id"]}')
        require(scene.get('caption') == turn['caption']
                and scene.get('sourceSpeech', scene.get('speech')) == turn['speech'],
                f'Timeline does not match the current script: {turn["id"]}')
        validate_visual(scene, turn)
    info = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                           '-show_streams', '-of', 'json', str(video)]))['streams'][0]
    require((int(info['width']), int(info['height'])) == (1600, 900), 'MP4 dimensions differ from the timeline.')
    require(info['avg_frame_rate'] == '24/1', 'MP4 frame rate differs from the timeline.')
    require(int(info['nb_frames']) == timeline['frameCount'], 'MP4 frame count differs from the timeline.')
    return video, timeline, exported, {
        'script': digest(SCRIPT), 'timeline': digest(timeline_file), 'export': digest(export_file),
    }


def plan(timeline):
    by_id = {scene['id']: scene for scene in timeline['scenes']}
    selections = []

    def add(scene_id, label, reason, *, offset=None):
        scene = by_id[scene_id]
        start_frame = round(scene['start'] * FPS)
        end_frame = round(scene['end'] * FPS)
        require(end_frame > start_frame, f'Empty scene: {scene_id}')
        if offset is None:
            # Use the first subtitle cue midpoint, with the opening camera settled.
            cues = [cue for cue in timeline['subtitleCues']
                    if cue.get('turnId') == scene_id
                    and (cue['start'] + cue['end']) / 2 >= scene['start'] + .9]
            require(cues, f'No suitable subtitle cue for QA extraction: {scene_id}')
            seconds = (cues[0]['start'] + cues[0]['end']) / 2
        else:
            seconds = scene['start'] + min(max(offset, 0), scene['duration'] - 1 / FPS)
        frame = min(max(round(seconds * FPS), start_frame), end_frame - 1)
        seconds = frame / FPS
        cue = next((cue for cue in timeline['subtitleCues']
                    if cue['start'] <= seconds < cue['end']), None)
        visual = scene.get('visual', {})
        selections.append({
            'file': f'{len(selections) + 1:02}-{label}.png', 'label': label, 'reason': reason,
            'sceneId': scene_id, 'frame': frame, 'seconds': seconds,
            'sceneStartSeconds': scene['start'], 'sceneEndSeconds': scene['end'],
            'phase': scene['phase'], 'chapterId': scene.get('chapterId'),
            'speaker': scene.get('speaker'), 'layout': visual.get('layout'),
            'viewId': visual.get('viewId'), 'svg': visual.get('svg'),
            'highlightFields': visual.get('highlightFields', []),
            'highlightRows': visual.get('highlightRows', []),
            'subtitleLinesAtExtraction': cue['lines'] if cue else [],
            'subtitleCue': {'start': cue['start'], 'end': cue['end']} if cue else None,
        })

    add('logo', 'intro-logo', '冒頭4秒の共通ロゴを確認する。', offset=2.4)
    add('cast-establish', 'cast-wide', '3人の顔・名前・英語の役割を一緒に確認する。', offset=.5)
    for number, speaker in [(1, 'mako'), (2, 'manabu'), (3, 'ikumi')]:
        add(f'lesson17-turn-{number:03}-{speaker}', f'opening-{speaker}-centered',
            '滑らかな移動の後に話者が中央へ収まり、名前・役割・字幕を読めるか確認する。')
    for number, speaker, label, reason in [
        (8, 'ikumi', 'management-flow', 'PortalからSQLとAPIMへの管理処理の分担図と、その図に対応する字幕。'),
        (12, 'ikumi', 'key-reveal', '対象確認・listSecrets・SQL表示監査の設定項目と赤枠。'),
        (15, 'ikumi', 'suspend', '停止のsuspended行と、保持するID・キーの行の赤枠。'),
        (16, 'ikumi', 'resume', '再開のactive行と、停止から変わる赤枠。'),
        (24, 'ikumi', 'ownership-transfer', '所有グループと移管監査を更新し、同じAPIM実体を使う説明。'),
        (29, 'ikumi', 'inventory-review', '棚卸しの日時・本人・監査と、状態を勝手に変えない説明。'),
        (31, 'ikumi', 'delete-202-pending', '削除202受付中とdeletingに対応する赤枠。'),
        (33, 'ikumi', 'delete-confirmed', '完了確認後のdeleted・削除監査に対応する赤枠。'),
        (41, 'ikumi', 'limit-save-and-audit', '現在所属・版比較・制限設定と監査を保存する説明。'),
        (48, 'ikumi', 'limits-flow', '設定保存とAPI利用時の内部照会を分けた補助図。'),
        (69, 'mako', 'quiz-question', '制限反映の確認問題。質問中は正答の赤枠がないこと。'),
        (70, 'ikumi', 'quiz-answer', '同じviewId・構図を保った回答。Bだけを赤枠で示すこと。'),
    ]:
        add(f'lesson17-turn-{number:03}-{speaker}', label, reason)
    add('closing', 'closing-logo', '共通8秒クロージング中盤のロゴを確認する。', offset=3.0)
    add('closing', 'closing-end', '最終フレームの暗転・画面終了を確認する。',
        offset=by_id['closing']['duration'] - 1 / FPS)
    require(len(selections) == 19, 'Unexpected QA selection count.')
    return selections


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replace', action='store_true', help='Intentionally replace this episode QA extraction.')
    parser.add_argument('--plan-only', action='store_true', help='Validate the finished master and print selections without writing PNGs.')
    args = parser.parse_args()
    video, timeline, exported, source_hashes = source()
    selections = plan(timeline)
    if args.plan_only:
        print(json.dumps({'episodeKey': '17', 'sourceSha256': exported['sha256'],
                          'frames': selections, 'filesWritten': False}, ensure_ascii=False, indent=2))
        return
    require(not DESTINATION.is_symlink(), 'QA destination must not be a symlink.')
    DESTINATION.mkdir(exist_ok=True)
    targets = [DESTINATION / selection['file'] for selection in selections]
    manifest_file = DESTINATION / 'manifest.json'
    for target in [*targets, manifest_file]:
        require(not target.is_symlink(), f'QA destination must not be a symlink: {target.name}')
        require(not target.exists() or args.replace,
                f'QA destination already exists: {target.name}; use --replace intentionally.')
    for selection, target in zip(selections, targets):
        run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{selection["seconds"]:.9f}',
             '-i', str(video), '-map', '0:v:0', '-frames:v', '1', '-an',
             '-update', '1', str(target)])
        require(target.is_file() and target.stat().st_size > 24, 'Frame extraction produced an empty file.')
        with target.open('rb') as stream:
            header = stream.read(24)
        require(header[:8] == b'\x89PNG\r\n\x1a\n'
                and struct.unpack('>II', header[16:24]) == (1600, 900),
                f'Unexpected QA image format or dimensions: {target.name}')
        selection['sha256'] = digest(target)
    require(digest(video) == exported['sha256'], 'Master changed during QA extraction.')
    require(digest(OUTPUT / 'lesson-timeline.json') == source_hashes['timeline']
            and digest(OUTPUT / 'lesson-export.json') == source_hashes['export']
            and digest(SCRIPT) == source_hashes['script'],
            'Source metadata or script changed during QA extraction.')
    manifest = {
        'status': 'extracted-for-visual-review',
        'createdAt': datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(),
        'episodeKey': '17', 'sourceType': 'completed-mp4',
        'sourceFile': str(video), 'sourceSha256': exported['sha256'],
        'sourceScriptSha256': source_hashes['script'],
        'sourceTimelineSha256': source_hashes['timeline'],
        'sourceExportSha256': source_hashes['export'],
        'durationSeconds': timeline['duration'], 'frameCount': timeline['frameCount'],
        'width': 1600, 'height': 900, 'fps': FPS, 'frames': selections,
        'selectionPolicy': '字幕cueの中央。冒頭はカメラ移動完了後。ロゴは明示位置。19枚。',
        'visualReviewPerformedByThisScript': False, 'networkRequestsMade': False,
    }
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'manifest': str(manifest_file), 'frames': len(selections),
                      'sourceSha256': exported['sha256'], 'visualReview': 'pending'}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, IndexError, FileNotFoundError,
            json.JSONDecodeError, subprocess.CalledProcessError) as error:
        raise SystemExit('QA extraction stopped: ' + str(error))
