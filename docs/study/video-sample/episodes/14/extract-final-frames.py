#!/usr/bin/env python3
"""Extract episode 14 QA stills from its completed, hash-verified MP4.

Does not render a preview, synthesize audio, or alter the source video.
Every PNG and the extraction manifest stay inside episodes/14/final-frames/.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

EPISODE = Path(__file__).resolve().parent
OUTPUT = EPISODE.parent.parent / 'output' / 'lesson-14-gemini'
DESTINATION = EPISODE / 'final-frames'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    require(result.returncode == 0, f'{command[0]} failed: {result.stderr[-2000:]}')
    return result.stdout


def load(path):
    return json.loads(path.read_text())


def source():
    for lock in ('.lesson-build.lock', '.gemini-generator.lock'):
        require(not (OUTPUT / lock).exists(), f'Episode is still busy: {lock}')
    timeline_file = OUTPUT / 'lesson-timeline.json'
    export_file = OUTPUT / 'lesson-export.json'
    require(timeline_file.is_file() and export_file.is_file(), 'A completed timeline and export manifest are required.')
    timeline, exported = load(timeline_file), load(export_file)
    require(timeline.get('episodeKey') == '14', 'Source is not episode 14.')
    require(timeline.get('fps') == exported.get('fps') == 24, 'Expected the 24fps finished lesson.')
    require(exported.get('speechSpeedChanged') is False and timeline.get('speed') == 1, 'Expected normal speed.')
    filename = exported.get('file')
    require(filename == 'egakumi-study-14.mp4', 'Unexpected episode 14 export filename.')
    video = OUTPUT / filename
    require(video.is_file() and video.stat().st_size > 0, 'Completed MP4 is missing.')
    require(video.resolve().parent == OUTPUT.resolve(), 'Source MP4 escapes the episode output.')
    require(digest(video) == exported.get('sha256'), 'MP4 hash differs from the completed export manifest.')
    info = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_streams', '-of', 'json', str(video)]))['streams'][0]
    require((int(info['width']), int(info['height'])) == (1600, 900), 'Unexpected video dimensions.')
    require(info['avg_frame_rate'] == '24/1', 'Unexpected video frame rate.')
    require(int(info['nb_frames']) == timeline.get('frameCount') == exported.get('frameCount'), 'Frame count differs from the timeline.')
    return video, timeline, exported, digest(timeline_file), digest(export_file)


def plan(timeline):
    scenes = {scene['id']: scene for scene in timeline['scenes']}
    selections = []

    def add(scene_id, label, offset=2.0):
        scene = scenes[scene_id]
        # Clamp to the scene and snap to the 24fps frame grid.
        local = min(offset, max(0, scene['duration'] - 1 / 24))
        frame = round(scene['start'] * 24) + round(local * 24)
        end_frame = round(scene['end'] * 24)
        frame = min(frame, end_frame - 1)
        seconds = frame / 24
        cue = next((cue for cue in timeline['subtitleCues'] if cue['start'] <= seconds < cue['end']), None)
        visual = scene.get('visual', {})
        selections.append({
            'label': label, 'sceneId': scene_id, 'frame': frame, 'seconds': seconds,
            'phase': scene['phase'], 'speaker': scene.get('speaker'),
            'viewId': visual.get('viewId'), 'layout': visual.get('layout'),
            'highlightFields': visual.get('highlightFields', []),
            'highlightRows': visual.get('highlightRows', []),
            'subtitleLinesAtExtraction': cue['lines'] if cue else [],
            'file': f'{len(selections) + 1:02}-{label}.png',
        })

    add('logo', 'intro-logo', 2.4)
    add('cast-establish', 'cast-wide', .5)
    for number, speaker in [(1, 'mako'), (2, 'manabu'), (3, 'ikumi')]:
        add(f'lesson14-turn-{number:03}-{speaker}', f'opening-{speaker}-centered')
    for number, speaker, label in [
        (5, 'manabu', 'three-axes'),
        (9, 'ikumi', 'roles-sql'), (11, 'ikumi', 'roles-apim'),
        (14, 'ikumi', 'roles-diagram'), (16, 'ikumi', 'runtime'),
        (19, 'ikumi', 'login'), (24, 'ikumi', 'sql-session'),
        (28, 'ikumi', 'authentication-data'), (32, 'ikumi', 'group-ownership'),
        (35, 'ikumi', 'identity-diagram'), (41, 'ikumi', 'company-attribution'),
        (44, 'ikumi', 'subscription-term-no-outline'),
        (49, 'ikumi', 'company-data'), (54, 'ikumi', 'company-create'),
        (56, 'ikumi', 'company-update'), (58, 'ikumi', 'company-disabled'),
        (61, 'ikumi', 'company-rename'), (63, 'ikumi', 'example'),
        (69, 'mako', 'quiz1-question'), (70, 'manabu', 'quiz1-answer'),
        (72, 'mako', 'quiz2-question'), (73, 'manabu', 'quiz2-answer'),
        (75, 'mako', 'quiz3-question'), (76, 'ikumi', 'quiz3-answer'),
        (79, 'ikumi', 'summary'),
    ]:
        add(f'lesson14-turn-{number:03}-{speaker}', label)
    add('closing', 'closing-logo', 3.0)
    add('closing', 'closing-end', scenes['closing']['duration'] - 1 / 24)
    # Keep the original 32 QA filenames stable while adding the subtitle revision.
    add('lesson14-turn-026-ikumi', 'account-data-microsoft-wrap')
    return selections


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replace', action='store_true', help='Replace this episode QA extraction after a new export.')
    args = parser.parse_args()
    video, timeline, exported, timeline_hash, export_hash = source()
    selections = plan(timeline)
    require(not DESTINATION.is_symlink(), 'QA destination must not be a symlink.')
    DESTINATION.mkdir(exist_ok=True)
    for selection in selections:
        target = DESTINATION / selection['file']
        require(not target.is_symlink(), 'QA PNG must not be a symlink.')
        require(not target.exists() or args.replace, f'QA PNG already exists: {target.name}; use --replace intentionally.')
    manifest_file = DESTINATION / 'manifest.json'
    require(not manifest_file.is_symlink(), 'QA manifest must not be a symlink.')
    require(not manifest_file.exists() or args.replace, 'QA manifest already exists; use --replace intentionally.')
    for selection in selections:
        target = DESTINATION / selection['file']
        run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{selection["seconds"]:.9f}', '-i', str(video),
             '-map', '0:v:0', '-frames:v', '1', '-an', '-update', '1', str(target)])
        require(target.is_file() and target.stat().st_size > 0, 'Frame extraction produced an empty file.')
        selection['sha256'] = digest(target)
    require(digest(video) == exported['sha256'], 'Completed MP4 changed during QA extraction.')
    manifest = {
        'status': 'extracted-for-visual-review',
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'episodeKey': '14', 'sourceType': 'completed-mp4',
        'sourceFile': str(video), 'sourceSha256': exported['sha256'],
        'sourceTimelineSha256': timeline_hash, 'sourceExportSha256': export_hash,
        'width': 1600, 'height': 900, 'fps': 24,
        'frames': selections,
        'visualReviewPerformedByThisScript': False,
        'networkRequestsMade': False,
    }
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'manifest': str(manifest_file), 'frames': len(selections),
                      'sourceSha256': exported['sha256'], 'visualReview': 'pending'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
