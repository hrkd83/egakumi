#!/usr/bin/env python3
"""Verify a finished lesson without synthesis, rendering, network access or writes."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import wave
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
OUT = HERE / 'output' / 'lesson-01'
ENDPOINT = 'https://gateway.example.com/models/azure-speech/v1/synthesize'
VOICES = {
    'mako': 'en-US-Bree:DragonHDLatestNeural',
    'manabu': 'en-US-Vance:DragonHDLatestNeural',
    'ikumi': 'en-US-Tyler:DragonHDLatestNeural',
}
ROLES = {'mako': 'Moderator', 'manabu': 'Product Manager', 'ikumi': 'Solution Architect'}
ACTOR_X = {'mako': 302, 'manabu': 800, 'ikumi': 1298}
FPS, SAMPLE_RATE = 24, 48000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def input_path(name):
    require(not Path(name).is_absolute(), 'Input paths must be relative to video-sample')
    value = (HERE / name).resolve()
    require(value.is_relative_to(HERE), 'Input path escapes video-sample')
    return value


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def close(actual, expected, tolerance=1e-6):
    return math.isfinite(float(actual)) and abs(float(actual) - float(expected)) <= tolerance


def run(args):
    result = subprocess.run(args, capture_output=True, text=True)
    require(result.returncode == 0, f'{args[0]} failed: {result.stderr[-2000:]}')
    return result


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                           '-of', 'json', str(path)]).stdout)


def stamp(seconds):
    ms = math.floor(seconds * 1000 + .5)
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}.{ms % 1000:03}'


def verify_video(path, duration, frames):
    metadata = probe(path)
    video = next(row for row in metadata['streams'] if row['codec_type'] == 'video')
    audio = next(row for row in metadata['streams'] if row['codec_type'] == 'audio')
    require(video['codec_name'] == 'h264' and video['pix_fmt'] == 'yuv420p',
            f'{path.name}: expected H.264 / yuv420p')
    require((video['width'], video['height']) == (1600, 900), f'{path.name}: wrong dimensions')
    num, den = map(int, video['avg_frame_rate'].split('/'))
    require(close(num / den, FPS), f'{path.name}: wrong frame rate')
    require(close(video['duration'], duration, .001), f'{path.name}: wrong video duration')
    require(close(metadata['format']['duration'], duration, .075), f'{path.name}: wrong container duration')
    if 'nb_frames' in video:
        require(int(video['nb_frames']) == frames, f'{path.name}: wrong indexed frame count')
    require(audio['codec_name'] == 'aac' and int(audio['channels']) == 2,
            f'{path.name}: expected stereo AAC')
    require(int(audio['sample_rate']) == SAMPLE_RATE, f'{path.name}: wrong audio sample rate')
    require(close(audio['duration'], duration, .075), f'{path.name}: audio does not cover the video')
    # One complete A/V decode also supplies a decoded frame count. This avoids
    # decoding the entire 15-minute video once in ffprobe and again in ffmpeg.
    decoded = run(['ffmpeg', '-v', 'error', '-xerror', '-nostats', '-stats_period', '10',
                   '-progress', 'pipe:1', '-i', str(path), '-map', '0:v:0', '-map', '0:a:0',
                   '-f', 'null', '-'])
    counted = re.findall(r'^frame=\s*(\d+)\s*$', decoded.stdout, re.MULTILINE)
    require(counted and int(counted[-1]) == frames, f'{path.name}: wrong decoded frame count')
    require('progress=end' in decoded.stdout, f'{path.name}: decode did not reach the end')


def verify_wav(path, channels, frames, rate=SAMPLE_RATE):
    with wave.open(str(path)) as audio:
        require((audio.getframerate(), audio.getsampwidth(), audio.getnchannels()) == (rate, 2, channels),
                f'{path.name}: wrong PCM audio format')
        require(audio.getnframes() == frames, f'{path.name}: wrong sample count')
        expected = frames * channels * 2
        received = 0
        while block := audio.readframes(256 * 1024):
            received += len(block)
        require(received == expected, f'{path.name}: incomplete PCM payload')


def verify_camera(timeline, scenes):
    expected_scenes = [scene for scene in scenes if scene['phase'] == 'opening']
    movements = timeline['cameraTimeline']
    require([row['id'] for row in movements] == [scene['id'] for scene in expected_scenes],
            'Camera timeline must cover the establishing shot and every opening turn')
    previous = None
    seen = set()
    for movement, scene in zip(movements, expected_scenes):
        speaker = scene.get('speaker')
        require(movement.get('speaker') == speaker and movement.get('previousSpeaker') == previous,
                f'{scene["id"]}: camera speaker continuity mismatch')
        require(close(movement['start'], scene['start']) and close(movement['end'], scene['end']),
                f'{scene["id"]}: camera segment timing mismatch')
        start, end = movement['from'], movement['to']
        require(close(start['focusX'], ACTOR_X.get(previous, 800))
                and close(start['zoom'], 1.1 if previous else 1),
                f'{scene["id"]}: camera does not start from the preceding shot')
        require(close(end['focusX'], ACTOR_X.get(speaker, 800))
                and close(end['zoom'], 1.1 if speaker else 1),
                f'{scene["id"]}: camera does not settle on the active speaker')
        if speaker:
            projected_x = 800 + end['zoom'] * (ACTOR_X[speaker] - end['focusX'])
            require(close(projected_x, 800), f'{scene["id"]}: active speaker is not centered')
            seen.add(speaker)
        previous = speaker
    require(seen == set(VOICES), 'Opening camera does not include all three speakers')
    # Probe the camera math without drawing images. For every actor transition,
    # the midpoint must lie between the two shots and the settled actor at x800.
    code = """
import {getHologramCamera} from './lesson-visual.mjs';
const rows=[];
for(const previousSpeaker of [null,'mako','manabu','ikumi'])
 for(const speaker of ['mako','manabu','ikumi'])
  rows.push({previousSpeaker,speaker,samples:[0,.425,.85,2].map(turnTime=>getHologramCamera({previousSpeaker,speaker,turnTime}))});
console.log(JSON.stringify(rows));
"""
    runtime = subprocess.run(['node', '--input-type=module', '-e', code], cwd=HERE,
                             capture_output=True, text=True)
    require(runtime.returncode == 0, 'Cannot inspect camera coordinates: ' + runtime.stderr[-1200:])
    for row in json.loads(runtime.stdout):
        first, mid, settled, held = row['samples']
        before, target = ACTOR_X.get(row['previousSpeaker'], 800), ACTOR_X[row['speaker']]
        require(close(first['focusX'], before) and close(settled['focusX'], target)
                and close(held['focusX'], target), 'Runtime camera endpoint mismatch')
        require(min(before, target) - 1e-6 <= mid['focusX'] <= max(before, target) + 1e-6,
                'Camera pan overshoots the virtual stage')
        require(0 < mid['progress'] < 1 and close(first['progress'], 0)
                and close(settled['progress'], 1), 'Camera movement is not interpolated')
        require(close(800 + held['zoom'] * (target - held['focusX']), 800),
                'Runtime camera does not center its speaker')
    return len(movements)


def main():
    global OUT, VOICES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='lesson-01')
    parser.add_argument('--script', default='lesson-01-script.json')
    parser.add_argument('--generation-config')
    parser.add_argument('--cast')
    args = parser.parse_args()
    config_name = args.generation_config or ('lesson-01-generation.json' if args.output == 'lesson-01' else 'lesson-01-gemini-generation.json')
    cast_name = args.cast or ('podcast-cast.json' if args.output == 'lesson-01' else 'podcast-cast-gemini.json')
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]*', args.output), 'Use a simple unique output directory')
    for name in (args.script, config_name, cast_name):
        input_path(name)
    OUT = HERE / 'output' / args.output
    script = read(input_path(args.script))
    config = read(input_path(config_name))
    cast = read(input_path(cast_name))
    generation = read(OUT / 'generation.json')
    is_gemini = generation.get('provider') == 'google-cloud' and generation.get('modelId') == 'gemini-3.8-flash-tts'
    if is_gemini:
        VOICES = {'mako': 'Aoede', 'manabu': 'Charon', 'ikumi': 'Orus'}
    clean_speech = lambda text: re.sub(r'\s+', ' ', re.sub(r'\[(?:breathing|laughter|sighing)\]', ' ', text)).strip()
    endpoint = ('https://aiplatform.googleapis.com/v1/projects/egakumi-example/locations/global/publishers/google/models/gemini-3.8-flash-tts:generateContent' if is_gemini else ENDPOINT)
    timeline = read(OUT / 'lesson-timeline.json')
    exported = read(OUT / 'lesson-export.json')
    warnings = []
    require(generation['endpoint'] == timeline['synthesisEndpoint'] == endpoint,
            'Synthesis endpoint provenance mismatch')
    if is_gemini:
        require(generation['projectId'] == 'egakumi-example' and generation['apiFamily'] == 'agent-platform-generate-content', 'Unexpected Google project or API')
        require(timeline['synthesisProvider'] == 'google-cloud' and timeline['synthesisModel'] == 'gemini-3.8-flash-tts', 'Timeline Google provider/model mismatch')
    require(generation['generationState'] == 'complete' and generation['credentialsPersisted'] is False,
            'Generation must be complete without persisted credentials')
    if is_gemini:
        if generation.get('configurationSha256'):
            require(generation['configurationSha256'] == digest(input_path(config_name)), 'Gemini generation config hash mismatch')
        if generation.get('sourceScript'):
            require(generation['sourceScript'] == args.script, 'Generated audio source path mismatch')
        require(config['sourceScriptSha256'] == digest(input_path(args.script)), 'Gemini source script hash mismatch')
        require(script['speed'] == timeline['speed'] == 1 and timeline['enhancePronunciation'] is None
                and cast['speech']['modelId'] == 'gemini-3.8-flash-tts'
                and 'Japanese natural-language' in timeline['pronunciationPolicy'], 'Expected normal-speed Gemini with Japanese style directions')
    else:
        require(generation['scriptSha256'] == hashlib.sha256(config['script'].encode()).hexdigest(), 'Generation script hash mismatch')
        require(script['speed'] == timeline['speed'] == 1 and script['speechSettings']['enhancePronunciation'] is True
                and timeline['enhancePronunciation'] is True and cast['speech']['enhancePronunciation'] is True,
                'Expected normal speed and pronunciation enhancement')
    require((timeline['width'], timeline['height'], timeline['fps']) == (1600, 900, FPS),
            'Timeline format mismatch')
    require('repository' in timeline['sourceBasis'].lower()
            and 'not a live azure inspection' in timeline['sourceBasis'].lower(),
            'Source basis must distinguish repository definitions from live Azure inspection')
    require('公開設定例' in script['scope'] and '取得値' in script['scope'],
            'Script must retain its source/example scope')
    for reference in script['sourceReferences']:
        if 'file' in reference:
            require((HERE / reference['file']).is_file(), f'Missing source {reference["file"]}')
    for person, voice in VOICES.items():
        selected, recorded = cast['members'][person], timeline['cast'][person]
        require(selected['name'] == recorded['name'] == person.upper(), f'{person}: display name mismatch')
        require(selected['role'] == recorded['role'] == ROLES[person], f'{person}: role label mismatch')
        require(selected['voiceId'] == recorded['voiceId'] == voice, f'{person}: selected voice mismatch')
        require(selected['status'] == 'selected' and selected['speed'] == recorded['speed'] == 1,
                f'{person}: selection or speed mismatch')

    turns, scenes, cues = script['turns'], timeline['scenes'], timeline['subtitleCues']
    omitted_list = script.get('firstCut', {}).get('omitTurnIds', [])
    omitted = set(omitted_list)
    active_turns = [turn for turn in turns if turn['id'] not in omitted]
    active_ids = {turn['id'] for turn in active_turns}
    require(len(turns) >= 3 and len(active_turns) >= 3, 'Episode requires source turns and three opening speakers')
    require(len({c['id'] for c in script['chapters']}) == len(script['chapters']), 'Duplicate chapter IDs')
    require({t['chapterId'] for t in active_turns} == {c['id'] for c in script['chapters']}, 'Every chapter must be used by active turns')
    expected_recording_ids = [turn['id'] for turn in (active_turns if is_gemini else turns)]
    require(len(config['candidates']) == len(generation['results']) == len(expected_recording_ids), 'Configured/generated count differs from selected source turns')
    require([row['id'] for row in config['candidates']] == expected_recording_ids and [row['id'] for row in generation['results']] == expected_recording_ids, 'Configured/generated turn order mismatch')
    require(len(omitted) == len(omitted_list), 'Duplicate editorial omissions')
    require(omitted <= {turn['id'] for turn in turns}, 'Editorial omissions name an unknown turn')
    if omitted:
        require(script.get('firstCut', {}).get('sourceRecordingsKept') is True, 'Editorial cut must retain its original source material')
    require(timeline['sourceTurnCount'] == len(turns) and timeline['editedTurnCount'] == len(active_turns)
            and timeline['omittedTurnIds'] == omitted_list, 'Timeline editorial cut differs from the script')
    require(len({turn['id'] for turn in turns}) == len(turns), 'Duplicate turn IDs')
    require(len({row['id'] for row in generation['results']}) == len(expected_recording_ids), 'Duplicate recording IDs')
    require([scene['id'] for scene in scenes if scene.get('speaker')] == [turn['id'] for turn in active_turns],
            'Speech scene order mismatch')
    expected_scene_ids = ['logo', 'cast-establish']
    previous_chapter = None
    for turn in active_turns:
        if turn['chapterId'] != 'opening' and turn['chapterId'] != previous_chapter:
            expected_scene_ids.append('chapter-' + turn['chapterId'])
        expected_scene_ids.append(turn['id'])
        previous_chapter = turn['chapterId']
    expected_scene_ids.append('closing')
    require([scene['id'] for scene in scenes] == expected_scene_ids,
            'Edited lesson scene/chapter order mismatch')
    require(scenes[0]['phase'] == 'logo' and close(scenes[0]['duration'], 4)
            and scenes[0]['frameCount'] == 96, 'Common logo must be four seconds / 96 frames')
    require(scenes[1]['id'] == 'cast-establish' and scenes[1]['speaker'] is None,
            'Opening must begin with all three characters before the first speaking shot')
    closing = scenes[-1]
    require(closing['phase'] == 'closing' and closing['speaker'] is None
            and close(closing['duration'], 8) and closing['frameCount'] == 192,
            'Common closing must be eight seconds / 192 frames')
    frame_total = 0
    for scene in scenes:
        require(close(scene['start'], frame_total / FPS), f'{scene["id"]}: scene discontinuity')
        require(isinstance(scene['frameCount'], int) and scene['frameCount'] > 0
                and close(scene['duration'], scene['frameCount'] / FPS),
                f'{scene["id"]}: invalid frame duration')
        frame_total += scene['frameCount']
        require(close(scene['end'], frame_total / FPS), f'{scene["id"]}: invalid scene end')
    require(frame_total == timeline['frameCount'] and close(timeline['duration'], frame_total / FPS),
            'Total timeline frame count mismatch')
    require(close(closing['end'], timeline['duration']), 'Closing does not end with the episode')
    if not 14 * 60 <= timeline['duration'] <= 17 * 60:
        warnings.append(f'Duration {timeline["duration"] / 60:.2f} minutes is outside the 14–17 minute target; review editorial pacing.')

    cues_by_turn = defaultdict(list)
    previous_cue_end = 0
    for cue in cues:
        require(cue['start'] >= previous_cue_end - 1e-6 and cue['end'] > cue['start'],
                'Subtitle cues overlap or run backwards')
        require(close(cue['start'] * FPS, round(cue['start'] * FPS))
                and close(cue['end'] * FPS, round(cue['end'] * FPS)),
                'Subtitle cue is not aligned to video frames')
        require(cue['end'] <= timeline['duration'] + 1e-6, 'Subtitle cue exceeds the episode')
        require(cue['speaker'] in VOICES and 0 < len(cue['lines']) <= 2,
                'Invalid cue speaker or line count')
        require(not any('[' in line or ']' in line for line in cue['lines']), 'Subtitle exposes a speech tag')
        cues_by_turn[cue['turnId']].append(cue)
        previous_cue_end = cue['end']
    require(set(cues_by_turn) == active_ids, 'Missing active subtitle turn or omitted turn remains visible')
    records = {row['id']: row for row in generation['results']}
    candidates = {row['id']: row for row in config['candidates']}
    scene_map = {scene['id']: scene for scene in scenes}
    minimum_tail = float('inf')
    tag_count = 0
    source_tag_count = 0
    for turn in turns:
        identifier = turn['id']
        turn_tags = len(re.findall(r'\[(?:breathing|laughter|sighing)\]', turn['speech']))
        source_tag_count += turn_tags
        if is_gemini and identifier in omitted:
            require(identifier not in records and identifier not in candidates and identifier not in scene_map and identifier not in cues_by_turn,
                    f'{identifier}: omitted source was synthesized or remains in the final timeline')
            continue
        scene, recording, candidate = scene_map.get(identifier), records[identifier], candidates[identifier]
        raw = OUT / f'{identifier}-raw.wav'
        saved = read(OUT / f'{identifier}-raw.json')
        expected_speech = clean_speech(turn['speech']) if is_gemini else turn['speech']
        raw_rate_expected = 24000 if is_gemini else 16000
        require(recording['status'] == 'succeeded' and recording['httpStatus'] == saved['httpStatus'] == 200,
                f'{identifier}: synthesis did not succeed')
        require(candidate['role'] == ROLES[turn['speaker']], f'{identifier}: configured role label mismatch')
        for row in (recording, candidate, saved):
            require(row['voice'] == VOICES[turn['speaker']] and row['speed'] == 1,
                    f'{identifier}: voice or speed mismatch')
            require(row['speech'] == expected_speech and row['caption'] == turn['caption'],
                    f'{identifier}: recorded transcript/caption mismatch')
            if is_gemini:
                require(row['parameters']['language'] == 'ja-JP' and row['parameters']['style'], f'{identifier}: missing Japanese style instructions')
                require('[' not in row['speech'] and ']' not in row['speech'], f'{identifier}: legacy Azure tag sent to Gemini')
            else:
                require(row['parameters']['enhancePronunciation'] is True, f'{identifier}: speech settings mismatch')
        require(recording['rawAudioSha256'] == saved['rawAudioSha256'] == digest(raw),
                f'{identifier}: raw WAV hash mismatch')
        if is_gemini:
            for row in (recording, candidate, saved):
                require(row['sourceSpeech'] == turn['speech'] and row['sourceSpeechSha256'] == hashlib.sha256(turn['speech'].encode()).hexdigest(), f'{identifier}: original source text mismatch')
            require(not Path(recording['requestFile']).is_absolute() and '..' not in Path(recording['requestFile']).parts, f'{identifier}: unsafe request path')
            request_file = OUT / recording['requestFile']
            require(recording['requestSha256'] == saved['requestSha256'] == digest(request_file), f'{identifier}: Gemini request hash mismatch')
            payload = read(request_file)
            part = payload['contents'][0]['parts'][0]
            require(part['text'] == expected_speech and part['speechMetadata']['style'] == recording['parameters']['style'] == candidate['parameters']['style'], f'{identifier}: Gemini transcript/style payload mismatch')
            require(payload['generationConfig']['responseModalities'] == ['AUDIO'] and payload['generationConfig']['speechConfig']['voiceConfig']['voice'] == VOICES[turn['speaker']], f'{identifier}: Gemini audio/voice payload mismatch')
            require(recording['modelId'] == 'gemini-3.8-flash-tts' and recording['endpoint'] == endpoint, f'{identifier}: Gemini model/endpoint mismatch')
        else:
            ssml = OUT / f'{identifier}.ssml'
            require(recording['ssmlSha256'] == saved['ssmlSha256'] == digest(ssml), f'{identifier}: SSML hash mismatch')
            xml = ET.fromstring(ssml.read_text())
            voice = xml.find('{http://www.w3.org/2001/10/synthesis}voice')
            require(xml.attrib.get('{http://www.w3.org/XML/1998/namespace}lang') == 'ja-JP', f'{identifier}: wrong synthesis language')
            require(voice is not None and voice.attrib['name'] == VOICES[turn['speaker']] and voice.attrib['parameters'] == 'enhancePronunciation=true' and ''.join(voice.itertext()) == turn['speech'], f'{identifier}: SSML payload mismatch')
        with wave.open(str(raw)) as audio:
            raw_frames, raw_rate = audio.getnframes(), audio.getframerate()
        verify_wav(raw, 1, raw_frames, raw_rate_expected)
        raw_duration = raw_frames / raw_rate
        require(close(recording['rawDuration'], raw_duration, 1 / raw_rate_expected), f'{identifier}: source audio duration mismatch')
        chunks = turn.get('subtitleChunks') or [{'caption': turn['caption'], 'lines': turn.get('subtitleLines') or [turn['caption']], 'weight': 1}]
        require(''.join(chunk['caption'] for chunk in chunks) == turn['caption'],
                f'{identifier}: source caption chunks omit or duplicate text')
        for chunk in chunks:
            require(''.join(chunk['lines']) == chunk['caption'] and 0 < len(chunk['lines']) <= 2,
                    f'{identifier}: incomplete source subtitle lines')
        if identifier in omitted:
            require(scene is None and identifier not in cues_by_turn,
                    f'{identifier}: omitted source remains in the final scene/cue timeline')
            continue
        require(scene is not None, f'{identifier}: active source has no final scene')
        if turn.get('visual', {}).get('layout') == 'svg':
            episode_dir = input_path(args.script).parent
            source_svg = (episode_dir / turn['visual']['svg']).resolve()
            require(source_svg.is_relative_to(episode_dir) and source_svg.is_file(), f'{identifier}: unsafe or missing episode SVG')
            require(scene['visual'].get('svgSha256') == digest(source_svg), f'{identifier}: diagram SVG hash mismatch')
        require(scene['rawAudioSha256'] == recording['rawAudioSha256'],
                f'{identifier}: selected scene raw WAV hash mismatch')
        require(close(scene['audioDuration'], raw_duration, 1 / raw_rate_expected),
                f'{identifier}: selected scene audio duration mismatch')
        require(scene['voice'] == VOICES[turn['speaker']] and scene['speed'] == 1
                and scene['enhancePronunciation'] is (None if is_gemini else True) and scene['speech'] == expected_speech,
                f'{identifier}: timeline speech settings mismatch')
        speech_end = scene['start'] + scene['voiceDelay'] + raw_duration
        require(speech_end <= scene['end'] + 1e-6, f'{identifier}: narration is cut short')
        require(scene['duration'] + 1e-6 >= scene['voiceDelay'] + raw_duration + scene['gapAfter'],
                f'{identifier}: requested pause is cut short')
        minimum_tail = min(minimum_tail, scene['end'] - speech_end)
        turn_cues = cues_by_turn[identifier]
        require(len(turn_cues) == len(chunks), f'{identifier}: cue/chunk count mismatch')
        require(close(turn_cues[0]['start'], scene['start']) and close(turn_cues[-1]['end'], scene['end']),
                f'{identifier}: subtitle cues do not cover the complete turn')
        for index, (chunk, cue) in enumerate(zip(chunks, turn_cues)):
            require(''.join(chunk['lines']) == chunk['caption'] and 0 < len(chunk['lines']) <= 2,
                    f'{identifier}: incomplete subtitle lines')
            require(cue['lines'] == chunk['lines'] and cue['speaker'] == turn['speaker'],
                    f'{identifier}: cue content differs from the complete caption')
            if index:
                require(close(turn_cues[index - 1]['end'], cue['start']),
                        f'{identifier}: gap within a spoken turn subtitle')
        if is_gemini:
            require(scene['sourceSamples'] == raw_frames and scene['sourceSampleRate'] == raw_rate and scene['normalizedSpeechSamples'] == raw_frames * SAMPLE_RATE / raw_rate, f'{identifier}: source sample preservation metadata mismatch')
        tag_count += len(re.findall(r'\[(?:breathing|laughter|sighing)\]', expected_speech))
    expected_vtt = 'WEBVTT\n\n' + '\n'.join(
        f'{index}\n{stamp(cue["start"])} --> {stamp(cue["end"])}\n'
        f'{cast["members"][cue["speaker"]]["name"]}: ' + '\n'.join(cue['lines']) + '\n'
        for index, cue in enumerate(cues, 1))
    require((OUT / 'lesson-captions.vtt').read_text() == expected_vtt,
            'WebVTT timing or text differs from timeline cues')
    episode_number = script.get('episodeNumber', 1)
    episode_label = script.get('episodeLabel', f'第{episode_number}回')
    require(timeline['episodeNumber'] == episode_number and timeline.get('episodeLabel', episode_label) == episode_label, 'Timeline episode identity mismatch')
    expected_transcript = '# ' + episode_label + ' ' + timeline['title'] + '\n\n' + '\n\n'.join(
        '## ' + chapter['title'] + '\n\n' + '\n\n'.join(
            f'**{cast["members"][turn["speaker"]]["name"]}**：{turn["caption"]}'
            for turn in active_turns if turn['chapterId'] == chapter['id'])
        for chapter in script['chapters']) + '\n'
    require((OUT / 'lesson-transcript.md').read_text() == expected_transcript,
            'Published transcript differs from the active editorial cut')
    camera_segments = verify_camera(timeline, scenes)

    music = timeline['music']
    require(music['body'] == 'main-bgm.mp3' and close(music['bodyCrossfadeSeconds'], 2)
            and music['ducking'] is True, 'Programme BGM loop or ducking settings mismatch')
    require(music['brand'] == 'brand-theme.mp3'
            and music['closingBrandSourceSeconds'] == [63.8, 71.8]
            and close(music['closingDuration'], 8), 'Brand tail must use exactly 63.8–71.8 seconds')
    manifest = music['sourceManifest']
    require(manifest['mixPolicy']['closing']['brandSourceSeconds'] == [63.8, 71.8]
            and close(manifest['mixPolicy']['closing']['durationSeconds'], 8),
            'Closing source manifest differs from the episode')
    for asset in manifest['assets']:
        require(digest(HERE / 'assets' / 'music' / asset['path']) == asset['derivative']['sha256'],
                f'{asset["path"]}: programme music hash mismatch')
    video = OUT / exported['file']
    require(exported['sha256'] == digest(video), 'Final video hash mismatch')
    require(close(exported['duration'], timeline['duration']) and exported['frameCount'] == frame_total,
            'Export duration or frame count mismatch')
    require(exported['speechSpeedChanged'] is False and exported['enhancePronunciation'] is (None if is_gemini else True),
            'Export speech settings mismatch')
    require(close(exported['commonClosingDuration'], 8), 'Export common closing duration mismatch')
    sample_count = round(timeline['duration'] * SAMPLE_RATE)
    verify_wav(OUT / 'lesson-narration.wav', 1, sample_count)
    verify_wav(OUT / 'lesson-mix.wav', 2, sample_count)
    verify_video(video, timeline['duration'], frame_total)
    verify_video(OUT / exported['commonClosing'], 8, 192)
    measured = run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(OUT / 'lesson-mix.wav'),
                    '-af', 'loudnorm=I=-16:TP=-1.5:LRA=8:print_format=json', '-f', 'null', '-']).stderr
    metrics = json.loads(measured[measured.rfind('{'):measured.rfind('}') + 1])
    loudness, peak = float(metrics['input_i']), float(metrics['input_tp'])
    require(math.isfinite(loudness) and abs(loudness + 16) <= 1.5,
            'Final mix is outside -16 +/-1.5 LUFS')
    require(math.isfinite(peak) and peak <= -1, 'Final mix true peak exceeds -1 dBTP')
    print(json.dumps({
        'status': 'passed', 'warnings': warnings,
        'durationSeconds': timeline['duration'], 'durationMinutes': round(timeline['duration'] / 60, 3),
        'videoFrames': frame_total, 'sourceTurns': len(turns), 'editedTurns': len(active_turns),
        'omittedTurnIds': omitted_list, 'subtitleCues': len(cues),
        'roles': ROLES, 'voices': VOICES, 'speed': 1, 'enhancePronunciation': None if is_gemini else True,
        'synthesisProvider': timeline.get('synthesisProvider', 'egakumi-gateway'), 'synthesisModel': timeline.get('synthesisModel', 'DragonHDLatestNeural'), 'recordedTurns': len(generation['results']),
        'openingCameraSegments': camera_segments, 'speechTags': tag_count, 'sourceSpeechTags': source_tag_count,
        'minimumSpeechTailSeconds': round(minimum_tail, 6),
        'bodyLoopCrossfadeSeconds': 2, 'closingBrandSourceSeconds': [63.8, 71.8],
        'closingDurationSeconds': 8, 'mixIntegratedLUFS': loudness, 'mixTruePeakDBTP': peak,
        'sourceBasis': timeline['sourceBasis'], 'sha256': exported['sha256'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, IndexError, StopIteration, FileNotFoundError,
            ZeroDivisionError, ET.ParseError, wave.Error, json.JSONDecodeError) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        sys.exit(1)
