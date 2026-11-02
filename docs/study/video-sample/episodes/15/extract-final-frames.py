#!/usr/bin/env python3
"""Extract episode 15 QA frames only after its MP4 export has completed.

The authored script supplies the view and question/answer selections. All PNGs
and the manifest stay in episodes/15/final-frames/. No synthesis or rendering.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

EPISODE = Path(__file__).resolve().parent
OUTPUT = EPISODE.parent.parent / 'output' / 'lesson-15-gemini'
DESTINATION = EPISODE / 'final-frames'
SCRIPT = EPISODE / 'script.json'
FPS = 24


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
    timeline_file, export_file = OUTPUT / 'lesson-timeline.json', OUTPUT / 'lesson-export.json'
    require(timeline_file.is_file() and export_file.is_file(), 'A completed timeline and export are required.')
    script, timeline, exported = load(SCRIPT), load(timeline_file), load(export_file)
    require(script.get('episodeKey') == timeline.get('episodeKey') == '15', 'Source is not episode 15.')
    require(timeline.get('script') == 'episodes/15/script.json', 'Timeline names a different source script.')
    require(timeline.get('fps') == exported.get('fps') == FPS, 'Expected a 24fps finished lesson.')
    require(exported.get('speechSpeedChanged') is False and timeline.get('speed') == script.get('speed') == 1,
            'Expected normal speech speed.')
    require(exported.get('file') == 'egakumi-study-15.mp4', 'Unexpected episode 15 export filename.')
    video = OUTPUT / exported['file']
    require(video.is_file() and not video.is_symlink() and video.stat().st_size > 0, 'Completed MP4 is missing.')
    require(video.resolve().parent == OUTPUT.resolve(), 'Source MP4 escapes the episode output.')
    require(digest(video) == exported.get('sha256'), 'MP4 hash differs from the completed export.')
    info = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_streams',
                           '-of', 'json', str(video)]))['streams'][0]
    require((int(info['width']), int(info['height'])) == (1600, 900), 'Unexpected dimensions.')
    require(info['avg_frame_rate'] == '24/1', 'Unexpected frame rate.')
    require(int(info['nb_frames']) == timeline.get('frameCount') == exported.get('frameCount'),
            'Frame count differs from timeline/export.')
    require(abs(float(info['duration']) - timeline['duration']) < 1 / FPS, 'Video duration differs from timeline.')
    require(abs(timeline['duration'] - exported['duration']) < 1 / FPS, 'Timeline and export durations differ.')
    scenes = {scene['id']: scene for scene in timeline['scenes']}
    omitted = set(script.get('firstCut', {}).get('omitTurnIds', []))
    turns = [turn for turn in script['turns'] if turn['id'] not in omitted]
    require(len({turn['id'] for turn in turns}) == len(turns), 'Duplicate script turn IDs.')
    for turn in turns:
        require(turn['id'] in scenes, f'Timeline omits {turn["id"]}.')
        scene = scenes[turn['id']]
        require(scene.get('caption') == turn['caption'] and scene.get('speaker') == turn['speaker']
                and scene.get('chapterId') == turn['chapterId'], f'Timeline differs from {turn["id"]}.')
        for key, value in turn['visual'].items():
            require(scene.get('visual', {}).get(key) == value, f'Timeline visual differs: {turn["id"]} / {key}.')
    return video, script, timeline, exported, digest(timeline_file), digest(export_file)


def plan(script, timeline):
    scenes = {scene['id']: scene for scene in timeline['scenes']}
    omitted = set(script.get('firstCut', {}).get('omitTurnIds', []))
    turns = [turn for turn in script['turns'] if turn['id'] not in omitted]
    selections, already = [], set()

    def add(scene_id, label, reason, offset=2.0):
        if scene_id in already and scene_id != 'closing':
            return
        already.add(scene_id)
        scene = scenes[scene_id]
        local = min(offset, max(0, scene['duration'] - 1 / FPS))
        frame = min(round(scene['start'] * FPS) + round(local * FPS), round(scene['end'] * FPS) - 1)
        seconds = frame / FPS
        cue = next((cue for cue in timeline['subtitleCues'] if cue['start'] <= seconds < cue['end']), None)
        visual = scene.get('visual', {})
        selections.append({
            'label': label, 'reason': reason, 'sceneId': scene_id, 'frame': frame, 'seconds': seconds,
            'phase': scene['phase'], 'speaker': scene.get('speaker'),
            'viewId': visual.get('viewId'), 'layout': visual.get('layout'),
            'highlightFields': visual.get('highlightFields', []),
            'highlightRows': visual.get('highlightRows', []),
            'subtitleLinesAtExtraction': cue['lines'] if cue else [],
            'file': f'{len(selections) + 1:02}-{label}.png',
        })

    add('logo', 'intro-logo', 'Common four-second intro', 2.4)
    add('cast-establish', 'cast-wide', 'All three faces, names and roles', .5)
    opening = [turn for turn in turns if turn['chapterId'] == 'opening']
    require({turn['speaker'] for turn in opening} == {'mako', 'manabu', 'ikumi'}, 'Opening cast differs.')
    for speaker in ('mako', 'manabu', 'ikumi'):
        turn = next(turn for turn in opening if turn['speaker'] == speaker)
        add(turn['id'], f'opening-{speaker}-centered', 'Camera after the adopted speaker transition')

    by_view = {}
    for turn in turns:
        if turn['chapterId'] != 'opening':
            view_id = turn['visual'].get('viewId')
            require(view_id, f'Body turn has no viewId: {turn["id"]}.')
            by_view.setdefault(view_id, []).append(turn)
    require(len(by_view) == 18, f'Expected all 18 authored body views; found {len(by_view)}. Review the selection plan.')
    # Prefer an answer with a red outline so each settings view is checked at its
    # busiest point. SVG figures are represented by their first spoken scene.
    for view_id, group in by_view.items():
        turn = next((turn for turn in group if turn['visual'].get('highlightFields')
                     or turn['visual'].get('highlightRows')), group[0])
        add(turn['id'], view_id.removeprefix('lesson15-'), 'Representative of every authored body view')

    quizzes = {view_id: group for view_id, group in by_view.items() if all(t['chapterId'] == 'check' for t in group)}
    require(len(quizzes) == 3, 'Expected three quiz views.')
    for index, (view_id, group) in enumerate(quizzes.items(), 1):
        question = next(turn for turn in group if turn['speaker'] == 'mako')
        require(not question['visual'].get('highlightFields') and not question['visual'].get('highlightRows'),
                f'Quiz question already highlights an answer: {view_id}.')
        answer = next(turn for turn in group if turn['visual'].get('highlightFields') or turn['visual'].get('highlightRows'))
        add(question['id'], f'quiz{index}-question', 'Question must not reveal the answer with a red outline')
        add(answer['id'], f'quiz{index}-answer', 'Answer red outline; may already be the view representative')

    # Three ordinary question frames pair with highlighted representative answers.
    for suffix in ('reserve', 'apim', 'finish'):
        group = by_view[f'lesson15-{suffix}']
        question = next(turn for turn in group if turn['speaker'] == 'mako')
        add(question['id'], f'{suffix}-question', 'Compare stable geometry against the highlighted answer')
    add('closing', 'closing-logo', 'Common eight-second ending', 3.0)
    add('closing', 'closing-end', 'Last output frame', scenes['closing']['duration'] - 1 / FPS)
    require(30 <= len(selections) <= 35, f'Expected 30–35 QA frames; planned {len(selections)}.')
    require({selection['viewId'] for selection in selections if selection['phase'] == 'body'} == set(by_view),
            'At least one body view is missing from QA selections.')
    return selections


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replace', action='store_true', help='Intentionally replace this episode QA extraction after a new export.')
    args = parser.parse_args()
    video, script, timeline, exported, timeline_hash, export_hash = source()
    script_hash = digest(SCRIPT)
    selections = plan(script, timeline)
    require(not DESTINATION.is_symlink(), 'QA destination must not be a symlink.')
    DESTINATION.mkdir(exist_ok=True)
    manifest_file = DESTINATION / 'manifest.json'
    targets = [DESTINATION / selection['file'] for selection in selections] + [manifest_file]
    for target in targets:
        require(not target.is_symlink(), 'QA output must not be a symlink.')
        require(not target.exists() or args.replace, f'{target.name} already exists; use --replace intentionally.')
    for selection in selections:
        target = DESTINATION / selection['file']
        run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{selection["seconds"]:.9f}', '-i', str(video),
             '-map', '0:v:0', '-frames:v', '1', '-an', '-update', '1', str(target)])
        require(target.is_file() and target.stat().st_size > 0, 'Frame extraction produced an empty file.')
        selection['sha256'] = digest(target)
    require(digest(video) == exported['sha256'] and digest(SCRIPT) == script_hash,
            'MP4 or script changed during QA extraction.')
    require(digest(OUTPUT / 'lesson-timeline.json') == timeline_hash
            and digest(OUTPUT / 'lesson-export.json') == export_hash, 'Export metadata changed during extraction.')
    manifest = {
        'status': 'extracted-for-visual-review', 'createdAt': datetime.now(timezone.utc).isoformat(),
        'episodeKey': '15', 'sourceType': 'completed-mp4', 'sourceFile': str(video),
        'sourceSha256': exported['sha256'], 'sourceScriptSha256': script_hash,
        'sourceTimelineSha256': timeline_hash, 'sourceExportSha256': export_hash,
        'width': 1600, 'height': 900, 'fps': FPS, 'frames': selections,
        'bodyViewCount': 18, 'quizQuestionCount': 3,
        'visualReviewPerformedByThisScript': False, 'networkRequestsMade': False,
    }
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'manifest': str(manifest_file), 'frames': len(selections),
                      'sourceSha256': exported['sha256'], 'visualReview': 'pending'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
