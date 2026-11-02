#!/usr/bin/env python3
"""Verify the delivered episode and its integrated recordings locally."""
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import wave
from datetime import datetime, timezone

TOOLKIT = Path(__file__).resolve().parents[3]
OUTPUT = TOOLKIT / 'output/lesson-01-gemini'
HISTORY = OUTPUT / 'provenance'
VOICES = {'mako': 'Aoede', 'manabu': 'Charon', 'ikumi': 'Orus'}


def read(path):
    return json.loads(path.read_text())


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def run(args):
    result = subprocess.run(args, capture_output=True, text=True)
    check(result.returncode == 0, f'{args[0]} failed: {result.stderr[-3000:]}')
    return result.stdout


def local_file(directory, relative):
    check(isinstance(relative, str) and bool(relative), 'Missing relative file name')
    name = Path(relative)
    check(not name.is_absolute() and '..' not in name.parts, 'File path must stay local')
    path = directory / name
    check(path.resolve().is_relative_to(directory.resolve()) and path.is_file(),
          f'Missing or escaped local file: {relative}')
    return path


def verify_preserved_delivery():
    manifest = read(OUTPUT / 'data-integration.json')
    records = manifest.get('preservedFinalFiles')
    check(isinstance(records, list) and bool(records), 'Missing delivery preservation records')
    names = set()
    for record in records:
        name = record.get('path')
        check(name not in names, f'Duplicate preservation record: {name}')
        path = local_file(OUTPUT, name)
        check(sha(path) == record.get('sha256'), f'Delivery file changed during integration: {name}')
        names.add(name)
    required = {'egakumi-study-01-settings.mp4', 'lesson-captions.vtt',
                'lesson-transcript.md', 'egakumi-common-closing.mp4',
                'lesson-timeline.json', 'lesson-export.json', 'lesson-mix.wav',
                'visual-revision-provenance.json'}
    check(required <= names, 'Delivery preservation records omit required files')
    return len(records)


def verify_recordings(original_script, timeline):
    generation = read(OUTPUT / 'generation.json')
    check(generation.get('generationState') == 'complete'
          and generation.get('provider') == 'google-cloud'
          and generation.get('modelId') == 'gemini-3.8-flash-tts'
          and generation.get('speed') == 1
          and generation.get('credentialsPersisted') is False,
          'Expected complete normal-speed Gemini recordings')
    configuration = local_file(TOOLKIT, generation['configuration'])
    check(sha(configuration) == generation['configurationSha256'],
          'Integrated generation configuration hash differs')
    check(read(configuration).get('output') == OUTPUT.name,
          'Generation configuration points outside the delivered episode')
    omitted = set(original_script.get('firstCut', {}).get('omitTurnIds', []))
    turns = {turn['id']: turn for turn in original_script['turns'] if turn['id'] not in omitted}
    recordings = generation.get('results', [])
    check(len(recordings) == len(turns) == 87
          and {record['id'] for record in recordings} == set(turns),
          'Integrated recordings do not match the 87 edited turns')
    scenes = {scene['id']: scene for scene in timeline['scenes'] if scene.get('speaker')}
    check(set(scenes) == set(turns), 'Rendered speaking scenes differ from recordings')
    matching_fields = ('id', 'speaker', 'voice', 'speed', 'speech', 'sourceSpeech',
                       'sourceSpeechSha256', 'parameters', 'requestSha256',
                       'textSha256', 'styleSha256', 'rawAudioSha256')
    for recording in recordings:
        identifier = recording['id']
        check(re.fullmatch(r'[a-z0-9][a-z0-9-]*', identifier) is not None,
              'Unsafe recording identifier')
        turn = turns[identifier]
        saved = read(local_file(OUTPUT, identifier + '-raw.json'))
        check(all(recording.get(key) == saved.get(key) for key in matching_fields),
              f'Integrated recording metadata differs: {identifier}')
        check(saved.get('status') == 'succeeded' and saved.get('httpStatus') == 200
              and saved.get('finishReasons') == ['STOP']
              and saved.get('provider') == 'google-cloud'
              and saved.get('modelId') == 'gemini-3.8-flash-tts',
              f'Unsuccessful or unexpected recording: {identifier}')
        check(saved.get('speaker') == turn['speaker']
              and saved.get('voice') == VOICES[turn['speaker']]
              and saved.get('speed') == 1
              and saved.get('sourceAudioSpeedChanged') is False,
              f'Recording voice/speed changed: {identifier}')
        source_speech = turn['speech']
        spoken = re.sub(r'\s+', ' ', re.sub(r'\[(?:laughter|breathing)\]', ' ', source_speech)).strip()
        check(saved.get('sourceSpeech') == source_speech
              and saved.get('sourceSpeechSha256') == hashlib.sha256(source_speech.encode()).hexdigest()
              and saved.get('speech') == spoken
              and saved.get('textSha256') == hashlib.sha256(spoken.encode()).hexdigest(),
              f'Recording text differs from the authored turn: {identifier}')
        style = saved.get('parameters', {}).get('style')
        check(isinstance(style, str) and style == saved.get('style')
              and saved.get('styleSha256') == hashlib.sha256(style.encode()).hexdigest(),
              f'Recording style hash differs: {identifier}')
        request_path = local_file(OUTPUT, 'requests-sent/' + identifier + '.json')
        check(sha(request_path) == saved.get('requestSha256'),
              f'Sent request hash differs: {identifier}')
        request = read(request_path)
        check(request.get('contents') == [{'role': 'user', 'parts': [
                  {'text': spoken, 'speechMetadata': {'style': style}}]}]
              and request.get('generationConfig', {}).get('speechConfig', {}).get('voiceConfig', {}).get('voice')
                  == VOICES[turn['speaker']],
              f'Sent request text/style/voice differs: {identifier}')
        raw = local_file(OUTPUT, identifier + '-raw.wav')
        check(sha(raw) == saved.get('rawAudioSha256') == scenes[identifier].get('rawAudioSha256'),
              f'Raw recording or rendered scene hash differs: {identifier}')
        with wave.open(str(raw), 'rb') as audio:
            check(audio.getnchannels() == 1 and audio.getframerate() == 24000
                  and audio.getsampwidth() == 2 and audio.getcomptype() == 'NONE',
                  f'Unexpected raw audio format: {identifier}')
            duration = audio.getnframes() / audio.getframerate()
            check(abs(duration - saved['rawDuration']) < 1 / 24000
                  and abs(duration - scenes[identifier]['audioDuration']) < 1 / 24000,
                  f'Raw duration differs: {identifier}')
    return len(recordings)


def main():
    provenance = read(OUTPUT / 'visual-revision-provenance.json')
    exported = read(OUTPUT / 'lesson-export.json')
    timeline = read(OUTPUT / 'lesson-timeline.json')
    original_timeline = read(HISTORY / 'source-lesson-timeline.json')
    original_verification = read(HISTORY / 'source-verification.json')
    original_script = read(TOOLKIT / 'lesson-01-script.json')
    script = read(TOOLKIT / provenance['revisedScript']['file'])
    video = OUTPUT / exported['file']
    check(provenance['additionalSynthesisRequests'] == 0 and provenance['networkRequestsMade'] is False,
          'This visual-only revision must not synthesize new audio')
    preserved_delivery_files = verify_preserved_delivery()
    for key in ('sourceScript', 'revisedScript'):
        item = provenance[key]
        check(sha(TOOLKIT / item['file']) == item['sha256'], f'{key} changed after the visual revision')
    # The source video and duplicate mix were intentionally removed after
    # delivery. Their original hashes remain evidence; only the delivered
    # episode and its integrated recordings are runtime dependencies.
    historical_files = {'sourceTimeline': 'source-lesson-timeline.json',
                        'sourceGeneration': 'source-generation.json',
                        'sourceVerification': 'source-verification.json'}
    for key, filename in historical_files.items():
        check(sha(HISTORY / filename) == provenance[key]['sha256'],
              f'Historical source record changed: {key}')
    source_export = read(HISTORY / 'source-lesson-export.json')
    check(source_export['sha256'] == provenance['sourceVideo']['sha256'],
          'Historical source export/video hash differs')
    check(sha(video) == exported['sha256'] == provenance['outputVideo']['sha256'], 'Output MP4 hash changed')
    check(original_verification['status'] == 'passed', 'Source verification is not passed')
    check(original_verification['sha256'] == provenance['sourceVideo']['sha256'], 'Source verification/video mismatch')
    check(sha(OUTPUT / 'lesson-mix.wav') == provenance['sourceMix']['sha256'] == provenance['outputMixSha256'], 'Source voice/BGM mix changed')
    check(read(HISTORY / 'source-audio-cache.json')['files']['lesson-mix.wav'] == provenance['sourceMix']['sha256'], 'Original verified mix cache differs')
    integrated_recordings = verify_recordings(original_script, timeline)
    check(timeline['subtitleCues'] == original_timeline['subtitleCues'], 'Caption cues/times changed')
    check(timeline['cameraTimeline'] == original_timeline['cameraTimeline'], 'Opening camera timing changed')
    check(timeline['chapters'] == original_timeline['chapters'], 'Chapter timing changed')
    check(timeline['music'] == original_timeline['music'], 'Music specification changed')
    check(script['firstCut'] == original_script['firstCut'], 'Edited recording selection changed')
    fields = ('id', 'speaker', 'chapterId', 'speech', 'caption', 'subtitleLines', 'subtitleChunks', 'gapAfterSeconds', 'pauseAfterSeconds')
    original_by_id = {t['id']: t for t in original_script['turns']}
    check(len(script['turns']) == len(original_script['turns']), 'Source turn count changed')
    for turn in script['turns']:
        original = original_by_id[turn['id']]
        check(all(turn.get(k) == original.get(k) for k in fields), f'Speech or captions changed: {turn["id"]}')
    for visual in provenance['visualAssets']:
        check(sha(TOOLKIT / visual['file']) == visual['sha256'], f'Visual changed: {visual["turnId"]}')
    check(provenance['motion'] == {'bodyZoom': False, 'bodyPan': False, 'perTurnReset': False, 'redOutlinePulse': False, 'bodyCameraFixed': True, 'subtitlesAndPresenterRailRemainFixed': True}, 'Body camera must remain fixed')
    for turn in script['turns']:
        if turn['chapterId'] != 'opening':
            check(not any(turn.get('visual', {}).get(k) for k in ('azureMotion','camera','zoom','motion')), f'Unexpected body camera instruction: {turn["id"]}')
    check(timeline['speed'] == 1 and exported['speechSpeedChanged'] is False and provenance['speed'] == 1, 'Speed must remain 1.0')
    check(timeline['synthesisModel'] == 'gemini-3.8-flash-tts', 'Wrong synthesis model')
    check({k: v['voiceId'] for k, v in timeline['cast'].items()} == {'mako': 'Aoede', 'manabu': 'Charon', 'ikumi': 'Orus'}, 'Selected voices changed')
    check(all(p['speed'] == 1 for p in timeline['cast'].values()), 'Cast speed changed')
    fps = timeline['fps']
    cursor = 0
    for old, new in zip(original_timeline['scenes'], timeline['scenes'], strict=True):
        for key in ('id', 'phase', 'start', 'end', 'duration', 'frameCount', 'speech', 'speaker', 'audioDuration', 'voiceDelay', 'gapAfter', 'rawAudioSha256'):
            check(old.get(key) == new.get(key), f'Scene timing/audio changed: {old["id"]}/{key}')
        check(round(new['start'] * fps) == cursor, f'Gap in frame timeline: {new["id"]}')
        cursor += new['frameCount']
    check(cursor == timeline['frameCount'] == original_timeline['frameCount'], 'Frame timeline length changed')
    opening_frames = round(next(s['start'] for s in timeline['scenes'] if s['phase'] == 'body') * fps)
    closing = timeline['scenes'][-1]
    check(provenance['openingSourceFrames'] == [0, opening_frames], 'Opening source range differs')
    check(provenance['closingSourceFrames'] == [round(closing['start'] * fps), cursor], 'Closing source range differs')
    check(closing['duration'] == 8 and closing['frameCount'] == 192, 'Common closing is not eight seconds')
    info = json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)]))
    v = next(s for s in info['streams'] if s['codec_type'] == 'video')
    a = next(s for s in info['streams'] if s['codec_type'] == 'audio')
    check(v['codec_name'] == 'h264' and (v['width'], v['height']) == (1600, 900), 'Wrong video codec/dimensions')
    numerator, denominator = map(int, v['avg_frame_rate'].split('/'))
    check(numerator / denominator == 24, 'Wrong frame rate')
    check(int(v['nb_frames']) == cursor, 'Encoded video frame count differs')
    check(abs(float(v['duration']) - timeline['duration']) < 1 / fps, 'Encoded video duration differs')
    check(a['codec_name'] == 'aac' and int(a['sample_rate']) == 48000 and int(a['channels']) == 2, 'Wrong audio format')
    check(abs(float(a['duration']) - timeline['duration']) < .03, 'Audio duration differs')
    check(abs(float(info['format']['duration']) - timeline['duration']) < .03, 'Container duration differs')
    check(abs(float(v.get('start_time', 0))) < 1 / fps and abs(float(a.get('start_time', 0))) < .03, 'A/V start is offset')
    # Decode every video frame and every audio sample; fail on decode errors.
    run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(video), '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
    result = {
        'status': 'passed', 'warnings': [], 'verifiedAt': datetime.now(timezone.utc).isoformat(),
        'variant': provenance['variant'], 'durationSeconds': timeline['duration'], 'durationMinutes': round(timeline['duration'] / 60, 3),
        'videoFrames': cursor, 'resolution': [1600, 900], 'fps': 24, 'fullAudioVideoDecode': 'passed',
        'sourceTurns': len(script['turns']), 'editedTurns': timeline['editedTurnCount'], 'subtitleCues': len(timeline['subtitleCues']),
        'voices': {k: v['voiceId'] for k, v in timeline['cast'].items()}, 'speed': 1,
        'synthesisModel': timeline['synthesisModel'], 'additionalSynthesisRequests': 0,
        'integratedRawRecordingsVerified': integrated_recordings,
        'preservedDeliveryFilesVerified': preserved_delivery_files,
        'standaloneDeliveryDirectory': True, 'sourceOutputDirectoryRequired': False,
        'sourceSpeechAndCaptionsUnchanged': True, 'originalVoiceAndMusicMixReused': True,
        'originalMixIntegratedLUFS': original_verification['mixIntegratedLUFS'],
        'originalMixTruePeakDBTP': original_verification['mixTruePeakDBTP'],
        'originalMixSha256': provenance['sourceMix']['sha256'], 'openingCameraTimingPreserved': True,
        'commonClosingSeconds': closing['duration'], 'visualsAreTeachingMenus': True, 'azurePortalReplica': False,
        'motion': provenance['motion'], 'visualAssets': len(provenance['visualAssets']),
        'sha256': exported['sha256'], 'sourceBasis': provenance['sourceBasis'],
        'limitations': ['Visual readability is reviewed separately. No new listening or pronunciation assessment is implied by audio reuse.']
    }
    temp = OUTPUT / 'verification.json.tmp'
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    temp.replace(OUTPUT / 'verification.json')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, StopIteration) as error:
        print(f'Verification failed: {error}', file=sys.stderr)
        raise SystemExit(1)
