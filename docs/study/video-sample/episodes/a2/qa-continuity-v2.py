#!/usr/bin/env python3
"""Inspect every adjacent encoded body frame in A2's uninterrupted excerpt."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / 'output/lesson-a2-gemini'


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def main():
    clip = json.loads((OUT / 'conversation-preview.json').read_text())
    timeline = json.loads((OUT / 'lesson-timeline.json').read_text())
    export = json.loads((OUT / 'lesson-export.json').read_text())
    video = OUT / clip['file']
    assert clip['sourceSha256'] == export['sha256']
    assert clip['sha256'] == digest(video)
    assert clip['continuousConversation'] and not clip['cutsWithinExcerpt']
    rows = [s for s in timeline['scenes'] if s['id'] in clip['turnIds']]
    start = clip['sourceRangeSeconds'][0]
    boundaries = [round(s['start'] - start, 6) for s in rows[1:]]
    # Convert chroma sampling before the exact odd-sized crop. Luma is retained;
    # only the 1207x511 teaching body is compared, excluding speaker/subtitle UI.
    filters = ('format=yuv444p,crop=1207:511:65:204:exact=1,'
               'tblend=all_mode=difference,signalstats,metadata=mode=print:file=-')
    result = subprocess.run(['ffmpeg', '-v', 'error', '-nostats', '-i', str(video),
                             '-vf', filters, '-an', '-f', 'null', '-'],
                            text=True, capture_output=True, check=True)
    differences = []
    current = None
    for line in result.stdout.splitlines():
        match = re.match(r'frame:(\d+)\s+pts:.*?pts_time:([0-9.e+-]+)', line)
        if match:
            current = dict(pairIndex=int(match[1]), seconds=float(match[2]))
        elif line.startswith('lavfi.signalstats.YAVG=') and current is not None:
            differences.append(dict(current, meanYDifference=float(line.split('=', 1)[1])))
    assert len(differences) == clip['frameCount'] - 1, 'Frame-pair count mismatch'
    large = []
    other = []
    threshold = .3
    for row in differences:
        hit = any(abs(row['seconds'] - t) < 1 / 24 + 1e-6 for t in boundaries)
        if row['meanYDifference'] > threshold:
            large.append(dict(row, matchesTurnBoundary=hit))
        if not hit:
            other.append(row['meanYDifference'])
    unexpected = [r for r in large if not r['matchesTurnBoundary']]
    automated_status = 'needs_review' if unexpected else 'passed'
    # A reviewed codec refresh remains visible in the report. Reviews are bound
    # to the exact excerpt hash, and are never applied to a replacement video.
    review_path = HERE / 'continuity-reviewed-v2.json'
    reviewed = []
    if unexpected and review_path.exists():
        reviews = json.loads(review_path.read_text())
        if reviews['previewSha256'] == clip['sha256']:
            frame_metadata = json.loads(subprocess.check_output([
                'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                '-show_frames', '-show_entries',
                'frame=best_effort_timestamp_time,key_frame,pict_type',
                '-of', 'json', str(video)], text=True))['frames']
            remaining = []
            for row in unexpected:
                review = next((r for r in reviews['reviewedAnomalies']
                               if r['pairIndex'] == row['pairIndex']), None)
                frame = frame_metadata[row['pairIndex'] + 1]
                if (review and frame['key_frame'] == 1 and frame['pict_type'] == 'I'
                        and abs(review['seconds'] - row['seconds']) < 1e-6
                        and abs(review['meanYDifference'] - row['meanYDifference']) < 1e-6):
                    reviewed.append(dict(row, frameMetadata=frame, review=review))
                else:
                    remaining.append(row)
            unexpected = remaining
    report = dict(status='passed' if not unexpected else 'needs_review',
                  automatedStatus=automated_status,
                  review=dict(status='passed' if not unexpected else 'needs_review',
                              disposition='Reviewed hash-bound I-frame refresh retained separately' if reviewed else 'No reviewed codec exception'),
                  scope='Encoded uninterrupted body-region frame difference; playback and listening are separate',
                  sourceSha256=export['sha256'], previewSha256=clip['sha256'],
                  crop=[65, 204, 1207, 511], videoFrames=clip['frameCount'],
                  adjacentFramePairs=len(differences), durationSeconds=clip['durationSeconds'],
                  turnIds=clip['turnIds'], expectedTurnTransitionsSeconds=boundaries,
                  largeDifferenceThresholdY=threshold, largeDifferences=large,
                  reviewedCodecRefreshDifferences=reviewed,
                  unexplainedLargeDifferences=unexpected,
                  maximumOtherFrameDifferenceY=max(other, default=0),
                  limitations=['Encoded GOP refresh can produce small residual differences.',
                               'Composition and red-outline timing also require source audit and decoded-frame inspection.',
                               'This is not audio recognition or pronunciation/naturalness listening.'])
    (HERE / 'continuity-qa-v2.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
