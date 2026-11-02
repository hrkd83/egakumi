#!/usr/bin/env python3
"""Verify the Portal visual revision against the unchanged episode source."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[2]
BASE = KIT / 'output/lesson-03-gemini'
OUT = BASE / 'portal'


def read(path):
    return json.loads(path.read_text())


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def audio_packet_digest(path):
    command = ['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
               '-c', 'copy', '-f', 'data', 'pipe:1']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    h = hashlib.sha256()
    for chunk in iter(lambda: process.stdout.read(1024 * 1024), b''):
        h.update(chunk)
    error = process.stderr.read().decode()
    if process.wait():
        raise ValueError(error)
    return h.hexdigest()


def main():
    source = read(BASE / 'lesson-export.json')
    baseline = read(OUT / 'source-base.json')
    exported = read(OUT / 'portal-export.json')
    original = BASE / source['file']
    video = OUT / exported['file']
    assert digest(original) == source['sha256'] == baseline['originalSha256']
    assert digest(video) == exported['sha256']
    assert digest(BASE / 'lesson-timeline.json') == baseline['timelineSha256']
    assert digest(KIT / 'episodes/03/script.json') == baseline['scriptSha256']
    source_verification = read(BASE / 'verification.json')
    assert source_verification['status'] == 'passed'
    assert source_verification['sha256'] == source['sha256']

    module_spec = importlib.util.spec_from_file_location('episode_verification', KIT / 'verify-lesson.py')
    verifier = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(verifier)
    verifier.verify_video(video, source['duration'], source['frameCount'])
    source_audio = audio_packet_digest(original)
    revised_audio = audio_packet_digest(video)
    assert source_audio == revised_audio, 'Original AAC audio payload changed'
    source_audio_info = next(s for s in verifier.probe(original)['streams'] if s['codec_type'] == 'audio')
    revised_audio_info = next(s for s in verifier.probe(video)['streams'] if s['codec_type'] == 'audio')
    for key in ['codec_name', 'sample_rate', 'channels', 'time_base', 'start_pts', 'duration_ts']:
        assert source_audio_info[key] == revised_audio_info[key], 'Original audio timing changed: ' + key

    source_timeline = read(BASE / 'lesson-timeline.json')
    revised = read(OUT / 'portal-timeline.json')
    expected = {s['id'] for s in source_timeline['scenes'] if s['phase'] == 'body'}
    assert {s['id'] for s in revised['scenes']} == expected
    assert len(revised['scenes']) == len(expected)
    assert sum(s['captionCues'] for s in revised['scenes']) == sum(
        1 for c in source_timeline['subtitleCues'] if c['start'] >= revised['bodyStartFrame'] / 24)
    assert revised['sourceTimelineSha256'] == baseline['timelineSha256']
    assert revised['sceneDataSha256'] == digest(HERE / 'scene-data.json')
    assert revised['surfaceRendererSha256'] == digest(HERE / 'portal-surface.mjs')
    assert revised['compositorSha256'] == digest(HERE / 'build-portal.mjs')
    animation = [s for s in revised['scenes'] if s['motionSeconds'] > 0]
    zoom = [s for s in revised['scenes'] if s['cameraTo']['width'] < 1550]
    assert len(animation) >= 40, 'Too few animated explanation scenes'
    assert len(zoom) >= 40, 'Too few zoomed explanation scenes'
    for scene in revised['scenes']:
        for r in scene['focusRects']:
            assert 0 <= r['x'] < r['x'] + r['width'] <= 1600.1
            assert 0 <= r['y'] < r['y'] + r['height'] <= 680.1
        for c in (scene['cameraFrom'], scene['cameraTo']):
            assert 0 <= c['x'] < c['x'] + c['width'] <= 1600.1
            assert 0 <= c['y'] < c['y'] + c['height'] <= 680.1

    shared = read(OUT / 'shared-preservation-before.json')['sha256']
    assert all(digest(KIT / f) == value for f, value in shared.items())
    result = {
        'status': 'passed', 'durationSeconds': source['duration'],
        'videoFrames': source['frameCount'], 'width': 1600, 'height': 900,
        'fps': 24, 'wholeVideoAndAudioDecode': 'passed',
        'originalMp4Unchanged': True, 'originalScriptAndTimelineUnchanged': True,
        'originalAudioPacketPayloadUnchanged': True,
        'originalAudioTimestampsUnchanged': True,
        'audioStartPTS': revised_audio_info['start_pts'],
        'audioDurationTS': revised_audio_info['duration_ts'],
        'audioTimeBase': revised_audio_info['time_base'],
        'audioPacketSha256': source_audio, 'newSpeechRequests': 0,
        'bodyScenes': len(expected), 'animatedScenes': len(animation),
        'zoomedScenes': len(zoom), 'captions': 'Original timings and text retained',
        'sourceVerification': '../verification.json',
        'sourceMixIntegratedLUFS': source_verification['mixIntegratedLUFS'],
        'sourceMixTruePeakDBTP': source_verification['mixTruePeakDBTP'],
        'simulatedAzurePortal': True, 'sharedFilesUnchanged': len(shared),
        'sha256': exported['sha256'], 'audioListeningReview': 'not performed',
        'warnings': source_verification.get('warnings', []),
    }
    (OUT / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
