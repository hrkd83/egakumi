#!/usr/bin/env python3
"""Generate one configured study episode using the selected Gemini 3.8 Flash TTS.

Uses the existing gcloud user identity. Tokens stay in memory. The first
request warms up serially, then at most --workers requests run concurrently.
Paid attempts are journaled before dispatch; failed/unknown requests are never
silently resent. A known HTTP failure can be explicitly selected for retry.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
import wave

HERE = Path(__file__).resolve().parent
HELPER_FILE = HERE / 'generate-gemini-audition.py'
module_spec = importlib.util.spec_from_file_location('egakumi_gemini_audio', HELPER_FILE)
audio = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(audio)
MODEL_ID = 'gemini-3.8-flash-tts'
MODEL_KEY = 'gemini-3-8-flash'
PROVIDER = 'google-cloud'
FAMILY = 'agent-platform-generate-content'
PROJECT = 'egakumi-example'
ACCOUNT = 'developer@example.com'
EXPECTED_VOICES = {'mako': 'Aoede', 'manabu': 'Charon', 'ikumi': 'Orus'}
KNOWN_HTTP_RETRY_CODES = {400, 401, 403, 404, 408, 409, 429, 500, 502, 503, 504}


def clean_transcript(text):
    return re.sub(r'\s+', ' ', re.sub(r'\[(?:laughter|breathing)\]', ' ', text)).strip()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def clock():
    return datetime.now(timezone.utc).isoformat()


def local_path(value, *, must_exist=True):
    """Resolve an explicit path inside video-sample, including episode folders."""
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('Use a relative path within docs/study/video-sample.')
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Paths must stay within docs/study/video-sample.')
    resolved = (HERE / relative).resolve()
    if not resolved.is_relative_to(HERE.resolve()) or resolved == HERE.resolve():
        raise ValueError('Paths must stay within docs/study/video-sample.')
    if must_exist and not resolved.is_file():
        raise ValueError('Required local file is missing: ' + value)
    return resolved


def validate_episode_source(source):
    if source.get('status') not in {'ready-for-generation', 'first-cut-produced'}:
        raise ValueError('Complete the episode script and set status to ready-for-generation before generating speech.')
    chapters = source.get('chapters', [])
    chapter_ids = [chapter['id'] for chapter in chapters]
    if not chapter_ids or len(set(chapter_ids)) != len(chapter_ids) or 'opening' not in chapter_ids:
        raise ValueError('Episode chapters must be unique and include the common opening.')
    opening_speakers = set()
    omitted = set(source.get('firstCut', {}).get('omitTurnIds', []))
    fields = list(source.get('learningObjectives', []))
    for turn in source['turns']:
        if turn.get('chapterId') not in chapter_ids:
            raise ValueError('A speech turn names an unknown chapter.')
        if turn['chapterId'] == 'opening' and turn['id'] not in omitted:
            opening_speakers.add(turn.get('speaker'))
        fields.extend([turn.get('speech', ''), turn.get('caption', '')])
    if opening_speakers != set(EXPECTED_VOICES):
        raise ValueError('The opening must introduce all three selected speakers.')
    if any(not isinstance(value, str) for value in fields):
        raise ValueError('Speech, captions, and learning objectives must be text.')
    def contains_todo(value):
        if isinstance(value, str):
            return bool(re.search(r'\bTODO\b', value, re.I))
        if isinstance(value, dict):
            return any(contains_todo(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_todo(item) for item in value)
        return False
    if contains_todo(source):
        raise ValueError('Replace every TODO placeholder in the authored episode before preparing.')


def validate_episode_identity(source, source_file, output_name):
    """Validate source/output identity before any configuration file is written."""
    audio.local_name(output_name)
    relative_source = source_file.relative_to(HERE)
    if relative_source.parts[0] == 'episodes':
        if len(relative_source.parts) < 3 or not re.fullmatch(r'(?:0[1-9]|1[0-9]|2[0-3]|a[1-4]|b[1-3])', relative_source.parts[1]):
            raise ValueError('Use the canonical episodes/01..23, episodes/a1..a4, or episodes/b1..b3 folder.')
        episode_key = relative_source.parts[1]
        if output_name != f'lesson-{episode_key}-gemini':
            raise ValueError('Episode output must match its canonical episode folder.')
        number = source.get('episodeNumber')
        number_key = str(number).lower() if isinstance(number, str) else f'{number:02}' if isinstance(number, int) else None
        if number_key != episode_key:
            raise ValueError('Episode number must match its canonical source folder.')


def prepare(config_file, project, account):
    config_bytes = config_file.read_bytes()
    config = json.loads(config_bytes)
    if (config.get('projectId') != project or config.get('account') != account
            or config.get('provider') != PROVIDER or config.get('modelId') != MODEL_ID
            or config.get('apiFamily') != FAMILY or config.get('speed') != 1
            or config.get('language') != 'ja-JP'):
        raise ValueError('Configuration differs from the selected Japanese Gemini production.')
    source_file = local_path(config['sourceScript'])
    source_bytes = source_file.read_bytes()
    if sha(source_bytes) != config['sourceScriptSha256']:
        raise ValueError('Source script changed; review and update the production configuration first.')
    source = json.loads(source_bytes)
    validate_episode_source(source)
    if config.get('episodeId') is not None and config['episodeId'] != source.get('episodeId'):
        raise ValueError('Configuration belongs to a different episode.')
    source_turns = source['turns']
    omitted_ids = source.get('firstCut', {}).get('omitTurnIds', [])
    omitted = set(omitted_ids)
    ids = [t['id'] for t in source_turns]
    if (not source_turns or len(set(ids)) != len(ids)
            or len(omitted) != len(omitted_ids) or not omitted <= set(ids)):
        raise ValueError('Episode turns and editorial omission IDs must be unique and valid.')
    turns = [t for t in source_turns if t['id'] not in omitted]
    candidates = config['candidates']
    if (not turns or len(candidates) != len(turns)
            or config.get('sourceTurnCount') != len(source_turns)
            or config.get('activeTurnCount') != len(turns)
            or [t['id'] for t in turns] != [c['id'] for c in candidates]
            or config['omittedTurnIds'] != omitted_ids):
        raise ValueError('Generation must exactly match this episode and its editorial cut.')
    validate_episode_identity(source, source_file, config['output'])
    if config.get('castSha256'):
        if sha(local_path(config['cast']).read_bytes()) != config['castSha256']:
            raise ValueError('Shared cast changed; review and prepare the configuration again.')
    endpoint = f'https://aiplatform.googleapis.com/v1/projects/{project}/locations/global/publishers/google/models/{MODEL_ID}:generateContent'
    plans = []
    for turn, candidate in zip(turns, candidates):
        audio.identifier(turn['id'])
        speaker = turn['speaker']
        speech = clean_transcript(turn['speech'])
        style = candidate['parameters']['style']
        if (candidate['person'] != speaker or candidate['speaker'] != speaker
                or candidate['voice'] != EXPECTED_VOICES[speaker]
                or candidate['speed'] != 1 or candidate['speech'] != speech
                or candidate['sourceSpeech'] != turn['speech']
                or candidate['sourceSpeechSha256'] != sha(turn['speech'].encode())
                or candidate['caption'] != turn['caption']
                or candidate['parameters']['language'] != 'ja-JP'
                or not isinstance(style, str) or not style.strip()
                or re.search(r'\[[^\]]+\]', speech)):
            raise ValueError('Candidate text, voice, or delivery differs from the selected source.')
        body = audio.encoded({
            'contents': [{'role': 'user', 'parts': [
                {'text': speech, 'speechMetadata': {'style': style}},
            ]}],
            'generationConfig': {
                'responseModalities': ['AUDIO'],
                'speechConfig': {'voiceConfig': {'voice': candidate['voice']}},
            },
        })
        plans.append((candidate, body))
    return config, config_bytes, endpoint, plans


def send_one(record, body, token, project):
    """A worker performs one request only; journal writes stay on the main thread."""
    record = dict(record)
    request = Request(record['endpoint'], data=body, method='POST', headers={
        'Authorization': 'Bearer ' + token,
        'x-goog-user-project': project,
        'Content-Type': 'application/json; charset=utf-8',
        'Accept': 'application/json',
        'User-Agent': 'egakumi-study-gemini-lesson/1.0',
    })
    start = time.monotonic()
    wav = None
    try:
        with build_opener(audio.NoRedirect).open(request, timeout=180) as response:
            raw_response = response.read(audio.MAX_RESPONSE_BYTES + 1)
            record.update(httpStatus=response.status,
                          requestId=response.headers.get('x-request-id') or response.headers.get('x-google-request-id'))
        if len(raw_response) > audio.MAX_RESPONSE_BYTES:
            raise ValueError('Response exceeded the production limit.')
        record['responsePayloadSha256'] = sha(raw_response)
        decoded = json.loads(raw_response)
        wav, info = audio.audio_from_response(decoded)
        record.update(info)
        usage = decoded.get('usageMetadata') or decoded.get('usage_metadata')
        record.update(status='succeeded', completedAt=clock(), rawAudioSha256=sha(wav),
                      usageMetadata=usage if isinstance(usage, dict) else None,
                      usageReported=bool(usage), sourceAudioSpeedChanged=False,
                      estimatedAudioTokensAt25PerSecond=round(info['rawDuration'] * 25))
    except HTTPError as error:
        record.update(status='failed_http', httpStatus=error.code, **audio.safe_error(error))
    except (URLError, TimeoutError, OSError) as error:
        record.update(status='usage_unknown', errorType=type(error).__name__)
    except (ValueError, KeyError, TypeError, wave.Error) as error:
        record.update(status='failed_response' if record.get('httpStatus') == 200 else 'usage_unknown',
                      errorType=type(error).__name__)
    record['elapsedSeconds'] = round(time.monotonic() - start, 3)
    return record, wav


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default=PROJECT)
    parser.add_argument('--account', default=ACCOUNT)
    parser.add_argument('--config', default='lesson-01-gemini-generation.json')
    parser.add_argument('--output', help='Optional check: must equal the configured episode output name.')
    parser.add_argument('--workers', type=int, choices=(1, 2, 3), default=1,
                        help='Requests within this episode; default 1 when multiple episode tasks run in parallel.')
    parser.add_argument('--retry-failed-http', action='append', default=[], metavar='TURN_ID',
                        help='Explicitly retry a named non-200 HTTP failure after reviewing its code; never retries an unknown outcome.')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.project != PROJECT or args.account != ACCOUNT:
        parser.error('This approved production uses egakumi-example and developer@example.com.')
    config_file = local_path(args.config)
    config, config_bytes, endpoint, plans = prepare(config_file, args.project, args.account)
    output_name = audio.local_name(config['output'])
    if args.output is not None and args.output != output_name:
        parser.error('--output must equal config.output; use a separate episode configuration for a different output.')
    retry_ids = set(args.retry_failed_http)
    if not retry_ids <= {c['id'] for c, _ in plans}:
        parser.error('Retry ID is outside the active first cut.')
    if args.dry_run:
        print(json.dumps({'networkRequestsMade': False, 'authenticationRead': False,
                          'provider': PROVIDER, 'modelId': MODEL_ID, 'project': args.project,
                          'endpoint': endpoint, 'requests': len(plans), 'workers': args.workers,
                          'configuration': str(config_file.relative_to(HERE)), 'output': output_name,
                          'voices': EXPECTED_VOICES, 'speed': 1,
                          'sourceScriptSha256': config['sourceScriptSha256'],
                          'configurationSha256': sha(config_bytes)}, ensure_ascii=False))
        return 0

    out = HERE / 'output' / output_name
    out.mkdir(parents=True, exist_ok=True)
    requests_dir = out / 'requests-sent'
    requests_dir.mkdir(exist_ok=True)
    lock = out / '.gemini-generator.lock'
    build_lock = out / '.lesson-build.lock'
    if build_lock.exists():
        raise ValueError('This episode is currently rendering; finish that task before generating its audio.')
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(descriptor, str(os.getpid()).encode())
    os.close(descriptor)
    if build_lock.exists():
        lock.unlink()
        raise ValueError('Rendering started concurrently; no speech request was sent.')
    journal_file = out / 'gemini-attempts.json'
    token = None
    journal = {}
    records = {}
    state = 'initializing'
    created = clock()
    attempted_this_run = 0

    def checkpoint():
        audio.write_json(journal_file, journal)
        totals = {'promptTokenCount': 0, 'candidatesTokenCount': 0, 'totalTokenCount': 0}
        for r in records.values():
            if r.get('status') == 'succeeded' and isinstance(r.get('usageMetadata'), dict):
                for key in totals:
                    totals[key] += r['usageMetadata'].get(key, 0)
        audio.write_json(out / 'generation.json', {
            'schemaVersion': 2, 'provider': PROVIDER, 'modelId': MODEL_ID, 'modelKey': MODEL_KEY,
            'apiFamily': FAMILY, 'endpoint': endpoint, 'projectId': args.project, 'account': args.account,
            'configuration': str(config_file.relative_to(HERE)), 'configurationSha256': sha(config_bytes),
            'sourceScript': config['sourceScript'], 'sourceScriptSha256': config['sourceScriptSha256'],
            'episodeId': config.get('episodeId'), 'episodeNumber': config.get('episodeNumber'),
            'audioHelper': HELPER_FILE.name, 'audioHelperSha256': sha(HELPER_FILE.read_bytes()),
            'createdAt': created, 'updatedAt': clock(), 'generationState': state,
            'workers': args.workers, 'activeTurnCount': len(plans),
            'omittedTurnIds': config['omittedTurnIds'], 'speed': 1,
            'results': [records[c['id']] for c, _ in plans if c['id'] in records],
            'usageMetadataTotals': totals, 'attemptedThisRun': attempted_this_run,
            'requestsRetried': sum(len(r.get('attemptHistory', [])) for r in journal.values()),
            'automaticRetries': 0, 'sourceAudioSpeedChanged': False, 'credentialsPersisted': False,
            'authentication': 'Existing gcloud user access token; memory only',
        })

    try:
        journal = json.loads(journal_file.read_text()) if journal_file.exists() else {}
        if not isinstance(journal, dict):
            raise ValueError('Attempt journal must be an object.')
        pending = []
        for candidate, body in plans:
            turn_id = candidate['id']
            wav_file = out / (turn_id + '-raw.wav')
            info_file = out / (turn_id + '-raw.json')
            request_file = requests_dir / (turn_id + '.json')
            record = dict(candidate, turnId=turn_id, provider=PROVIDER, modelId=MODEL_ID,
                          modelKey=MODEL_KEY, apiFamily=FAMILY, endpoint=endpoint,
                          text=candidate['speech'], style=candidate['parameters']['style'],
                          requestSha256=sha(body), textSha256=sha(candidate['speech'].encode()),
                          styleSha256=sha(candidate['parameters']['style'].encode()),
                          file=wav_file.name, requestFile=str(request_file.relative_to(out)))
            if wav_file.exists():
                if not info_file.exists() or not request_file.exists():
                    raise ValueError('Cached audio is missing bound provenance or the sent request.')
                saved = json.loads(info_file.read_text())
                if (saved.get('status') != 'succeeded' or saved.get('requestSha256') != record['requestSha256']
                        or saved.get('endpoint') != endpoint or saved.get('voice') != candidate['voice']
                        or saved.get('speech') != candidate['speech']
                        or saved.get('sourceSpeech') != candidate['sourceSpeech']
                        or saved.get('sourceSpeechSha256') != candidate['sourceSpeechSha256']
                        or saved.get('style') != candidate['parameters']['style']
                        or saved.get('parameters', {}).get('style') != candidate['parameters']['style']
                        or saved.get('rawAudioSha256') != sha(wav_file.read_bytes())
                        or sha(request_file.read_bytes()) != record['requestSha256']):
                    raise ValueError('Cached audio or sent request differs from this production; refusing regeneration.')
                audio.validate_wav(wav_file.read_bytes())
                if saved.get('caption') != candidate['caption']:
                    # Captions are derived display metadata, never part of the
                    # paid request. Rebind only after proving the spoken text,
                    # delivery, request, voice and waveform are still identical.
                    previous_caption = saved.get('caption')
                    saved.setdefault('originalCaption', previous_caption)
                    saved.setdefault('captionRevisions', []).append({
                        'previousCaption': previous_caption, 'caption': candidate['caption'],
                        'reboundAt': clock(), 'reason': 'Display caption edit; synthesis request and audio unchanged',
                        'sourceScriptSha256': config['sourceScriptSha256'],
                        'configurationSha256': sha(config_bytes),
                        'requestSha256': record['requestSha256'],
                        'rawAudioSha256': saved['rawAudioSha256'],
                    })
                    saved['caption'] = candidate['caption']
                    audio.write_json(info_file, saved)
                    journal[turn_id] = dict(saved)
                records[turn_id] = dict(saved, reusedRaw=True)
                continue
            if turn_id in journal:
                previous = journal[turn_id]
                if turn_id not in retry_ids:
                    raise ValueError('A prior attempted turn has no successful WAV: ' + turn_id
                                     + '. Review its outcome; no automatic resend.')
                if (previous.get('status') != 'failed_http'
                        or previous.get('httpStatus') not in KNOWN_HTTP_RETRY_CODES
                        or previous.get('requestSha256') != record['requestSha256']):
                    raise ValueError('Explicit retry is allowed only for an unchanged, known non-200 HTTP failure.')
                record['attemptHistory'] = previous.get('attemptHistory', []) + [
                    {k: v for k, v in previous.items() if k != 'attemptHistory'}]
                record['explicitHttpRetry'] = True
            elif turn_id in retry_ids:
                raise ValueError('Explicit retry ID does not have a previous known HTTP failure.')
            records[turn_id] = dict(record, status='not_attempted')
            pending.append((record, body, wav_file, info_file, request_file))
        state = 'running'
        checkpoint()
        if not pending:
            state = 'complete'
            checkpoint()
            print(json.dumps({'state': state, 'succeeded': len(records), 'attemptedThisRun': 0,
                              'credentialsPersisted': False}))
            return 0
        token = audio.token_for(args.account)
        stopped = False
        warmed = any(r.get('status') == 'succeeded' for r in records.values())
        cursor = 0
        active = {}
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            while cursor < len(pending) or active:
                capacity = args.workers if warmed else 1
                while not stopped and cursor < len(pending) and len(active) < capacity:
                    record, body, wav_file, info_file, request_file = pending[cursor]
                    cursor += 1
                    audio.write_bytes(request_file, body)
                    record.update(status='started', requestedAt=clock())
                    records[record['id']] = record
                    journal[record['id']] = dict(record)
                    attempted_this_run += 1
                    checkpoint()  # Must reach disk before a paid attempt can leave.
                    future = executor.submit(send_one, record, body, token, args.project)
                    active[future] = (wav_file, info_file)
                if not active:
                    break
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    wav_file, info_file = active.pop(future)
                    record, wav = future.result()
                    if record['status'] == 'succeeded':
                        # Save metadata first; a crash between files cannot cause a paid resend.
                        audio.write_json(info_file, record)
                        audio.write_bytes(wav_file, wav)
                        warmed = True
                    else:
                        stopped = True
                    records[record['id']] = record
                    journal[record['id']] = dict(record)
                    checkpoint()
                    success = sum(r.get('status') == 'succeeded' for r in records.values())
                    codes = list(dict.fromkeys(filter(None, [record.get('googleStatus')]
                                               + record.get('googleReasons', []))))
                    print(f'{success}/{len(plans)} {record["id"]}: {record["status"]}'
                          + (f' HTTP {record["httpStatus"]}' if record.get('httpStatus') else '')
                          + (' [' + ', '.join(codes) + ']' if codes else ''), flush=True)
        state = 'complete' if all(r.get('status') == 'succeeded' for r in records.values()) else 'failed'
        checkpoint()
        print(json.dumps({'state': state,
                          'succeeded': sum(r.get('status') == 'succeeded' for r in records.values()),
                          'selectedRequests': len(plans), 'attemptedThisRun': attempted_this_run,
                          'credentialsPersisted': False}))
        return 0 if state == 'complete' else 1
    finally:
        token = None
        for turn_id, record in records.items():
            if record.get('status') == 'started':
                record['status'] = 'usage_unknown'
                journal[turn_id] = dict(record)
        if state in ('running', 'initializing') and records:
            state = 'interrupted'
            checkpoint()
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, FileExistsError, FileNotFoundError) as error:
        print('Stopped: ' + str(error) if isinstance(error, ValueError)
              else f'Stopped: {type(error).__name__}. No automatic request retry.', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Interrupted. In-flight usage is unknown; no automatic resend.', file=sys.stderr)
        sys.exit(130)
