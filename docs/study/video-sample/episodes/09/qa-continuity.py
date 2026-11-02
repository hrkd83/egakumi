#!/usr/bin/env python3
"""Episode 09: inspect every adjacent body-region frame in the continuous excerpt.

Highlights may change at source turn boundaries. Differences outside those
boundaries remain warnings requiring a separate hash-bound visual review.
No codec exception is silently accepted; source media is never modified.
"""
from pathlib import Path
import hashlib
import json
import re
import subprocess

HERE = Path(__file__).resolve().parent
OUT = HERE.parent.parent / 'output/lesson-09-gemini'


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    clip = read(OUT / 'conversation-preview.json')
    timeline = read(OUT / 'lesson-timeline.json')
    export = read(OUT / 'lesson-export.json')
    script = read(HERE / 'script.json')
    video = (OUT / clip['file']).resolve()
    assert video.is_relative_to(OUT.resolve())
    assert clip['sourceSha256'] == export['sha256'], 'Preview belongs to another final video'
    assert clip['sha256'] == digest(video), 'Preview hash mismatch'
    assert clip['continuousConversation'] and not clip['cutsWithinExcerpt'], 'Excerpt has internal edits'
    fps = timeline['fps']
    assert fps == 24
    scenes = [s for s in timeline['scenes'] if s['id'] in clip['turnIds']]
    assert [s['id'] for s in scenes] == clip['turnIds'], 'Selected turn order mismatch'
    source = {t['id']: t for t in script['turns']}
    view_ids = list(dict.fromkeys(source[s['id']]['visual'].get('viewId') for s in scenes))
    source_start = clip['sourceRangeSeconds'][0]
    changes = []
    all_boundaries = []
    for previous, current in zip(scenes, scenes[1:]):
        seconds = current['start'] - source_start
        all_boundaries.append({'seconds': seconds, 'turnId': current['id']})
        left, right = source[previous['id']]['visual'], source[current['id']]['visual']
        if left != right:
            changes.append({'seconds': seconds, 'fromTurnId': previous['id'],
                            'toTurnId': current['id'], 'viewId': right.get('viewId')})

    # Convert chroma format before the odd-sized exact crop, so coordinates
    # retain the specified 65/204/1207/511 teaching region without rounding.
    filters = ('format=yuv444p,crop=1207:511:65:204:exact=1,'
               'tblend=all_mode=difference,signalstats,metadata=mode=print:file=-')
    result = subprocess.run(['ffmpeg', '-v', 'error', '-nostats', '-i', str(video),
                             '-vf', filters, '-an', '-f', 'null', '-'],
                            text=True, capture_output=True, check=True)
    differences, current = [], None
    for line in result.stdout.splitlines():
        match = re.match(r'frame:(\d+)\s+pts:.*?pts_time:([0-9.e+-]+)', line)
        if match:
            current = {'pairIndex': int(match[1]), 'seconds': float(match[2])}
        elif line.startswith('lavfi.signalstats.YAVG=') and current is not None:
            differences.append({**current, 'meanYDifference': float(line.split('=', 1)[1])})
    assert len(differences) == clip['frameCount'] - 1, 'Adjacent-frame pair count mismatch'
    threshold = .3
    large, unexpected, other = [], [], []
    for row in differences:
        matches = [b for b in changes if abs(row['seconds'] - b['seconds']) < 1 / fps + 1e-6]
        turn_matches = [b for b in all_boundaries if abs(row['seconds'] - b['seconds']) < 1 / fps + 1e-6]
        if row['meanYDifference'] > threshold:
            entry = {**row, 'expectedVisualChange': bool(matches),
                     'matchesVisualTransitions': matches, 'matchesAnyTurnTransition': turn_matches}
            large.append(entry)
            if not matches:
                unexpected.append(entry)
        if not matches:
            other.append(row['meanYDifference'])
    warnings = []
    if view_ids != [view_ids[0]] or view_ids[0] is None:
        warnings.append('Excerpt contains multiple/missing viewId values; review conversational framing')
    if unexpected:
        warnings.append('Differences outside expected source visual changes require decoded-frame review; GOP residuals are not automatically exempted')
    cue_boundaries = [round(c['start'] - source_start, 6) for c in timeline['subtitleCues']
                      if source_start < c['start'] < clip['sourceRangeSeconds'][1]]
    report = {
        'status': 'needs_review' if warnings else 'passed',
        'scope': 'All adjacent encoded body-frame differences; excludes presenter/captions; not audio listening',
        'sourceSha256': export['sha256'], 'previewSha256': clip['sha256'],
        'scriptSha256': digest(HERE / 'script.json'),
        'crop': [65, 204, 1207, 511], 'fps': fps, 'videoFrames': clip['frameCount'],
        'adjacentFramePairs': len(differences), 'durationSeconds': clip['durationSeconds'],
        'turnIds': clip['turnIds'], 'viewIds': view_ids,
        'turnBoundariesSeconds': all_boundaries,
        'expectedVisualTransitionsSeconds': changes,
        'subtitleCueBoundariesSeconds': cue_boundaries,
        'largeDifferenceThresholdY': threshold, 'largeDifferences': large,
        'unexplainedLargeDifferences': unexpected,
        'maximumNonTransitionMeanYDifference': max(other, default=0),
        'warnings': warnings,
        'limitations': [
            'Signal threshold highlights candidates; source audit and visual playback remain necessary.',
            'Encoded GOP refresh can produce small differences. None is silently accepted or removed.',
            'This compares the teaching body only; changing presenters and captions are intentionally excluded.',
            'Speech recognition, pronunciation, omissions and naturalness are not assessed.',
        ],
        'frameDifferences': differences,
    }
    (HERE / 'qa-continuity.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('status', 'videoFrames', 'adjacentFramePairs', 'viewIds',
                                           'largeDifferences', 'maximumNonTransitionMeanYDifference', 'warnings')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
