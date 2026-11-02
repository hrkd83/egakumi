#!/usr/bin/env python3
"""Generate the approved short Gemini audition with existing gcloud user auth.

No key or service account is created. OAuth tokens stay in memory, Google hosts
are fixed, redirects and automatic retries are disabled, and every paid attempt
is journaled before sending. This command does not change cloud configuration.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
import wave

HERE = Path(__file__).resolve().parent
MODEL_IDS = {
    'gemini-3-8-flash': 'gemini-3.8-flash-tts',
    'gemini-2-5-pro': 'gemini-2.5-pro-tts',
}
MAX_RESPONSE_BYTES = 48 * 1024 * 1024
RATE = 24000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise URLError('Redirect refused')


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def write_json(file, value):
    temporary = file.with_name(file.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(file)


def write_bytes(file, value):
    temporary = file.with_name(file.name + '.tmp')
    with temporary.open('wb') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(file)


def local_name(value):
    if Path(value).name != value or value in {'.', '..'}:
        raise ValueError('Use simple local configuration and output names.')
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', value):
        raise ValueError('Unsafe model or turn identifier.')
    return value


def token_for(account):
    executable = shutil.which('gcloud')
    if not executable:
        raise ValueError('gcloud CLI is not installed.')
    result = subprocess.run(
        [executable, 'auth', 'print-access-token', '--account', account, '--quiet'],
        capture_output=True, text=True, timeout=45,
    )
    if result.returncode:
        # Never forward gcloud output, which may contain auth information.
        raise ValueError('Existing gcloud authentication could not refresh; sign in with the specified account first.')
    token = result.stdout.strip()
    if not token or len(token) > 8192 or any(ord(c) < 33 for c in token):
        raise ValueError('gcloud returned an invalid access token.')
    return token


def request_for(spec, model, turn, project, pro_api):
    member = spec['members'][turn['speaker']]
    style = '\n'.join(filter(None, [spec['globalStyle'], member['style'], turn.get('delivery', '')]))
    voice = member['voice']
    if model['id'] != MODEL_IDS[model['key']]:
        raise ValueError('Model ID does not match the approved audition model.')
    if model['key'] == 'gemini-3-8-flash':
        endpoint = f'https://aiplatform.googleapis.com/v1/projects/{project}/locations/global/publishers/google/models/gemini-3.8-flash-tts:generateContent'
        family = 'agent-platform-generate-content'
        payload = {
            'contents': [{'role': 'user', 'parts': [
                {'text': turn['speech'], 'speechMetadata': {'style': style}},
            ]}],
            'generationConfig': {
                'responseModalities': ['AUDIO'],
                'speechConfig': {'voiceConfig': {'voice': voice}},
            },
        }
    elif pro_api == 'agent-platform':
        # Earlier TTS models use the older prebuiltVoiceConfig schema. Unlike
        # 3.8, their prompt and transcript share the contents text field.
        endpoint = f'https://aiplatform.googleapis.com/v1beta1/projects/{project}/locations/global/publishers/google/models/gemini-2.5-pro-tts:generateContent'
        family = 'agent-platform-generate-content-legacy'
        payload = {
            'contents': [{'role': 'user', 'parts': [{'text': style + '\n\n' + turn['speech']}]}],
            'generationConfig': {
                'responseModalities': ['AUDIO'],
                'speechConfig': {
                    'languageCode': spec['language'],
                    'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': voice}},
                },
            },
        }
    else:
        endpoint = 'https://texttospeech.googleapis.com/v1/text:synthesize'
        family = 'cloud-text-to-speech'
        payload = {
            'input': {'text': turn['speech'], 'prompt': style},
            'voice': {'languageCode': spec['language'], 'name': voice, 'modelName': model['id']},
            'audioConfig': {'audioEncoding': 'LINEAR16', 'sampleRateHertz': RATE},
        }
    # Endpoints come from fixed literals, never configuration-provided URLs.
    if family == 'cloud-text-to-speech' and (len(turn['speech'].encode()) > 4000
            or len(style.encode()) > 4000 or len((turn['speech'] + style).encode()) > 8000):
        raise ValueError('Cloud Text-to-Speech request exceeds documented byte limits.')
    return endpoint, family, payload, style, voice


def validate_wav(audio):
    with wave.open(io.BytesIO(audio), 'rb') as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (RATE, 1, 2, 'NONE'):
            raise ValueError('Expected uncompressed 24kHz mono PCM16 audio.')
        frames = wav.getnframes()
        pcm = wav.readframes(frames)
        if frames < RATE // 4 or len(pcm) != frames * 2:
            raise ValueError('Audio is empty or incomplete.')
        return frames / RATE, pcm


def audio_from_response(response):
    chunks = []
    finish_reasons = []
    if isinstance(response.get('audioContent'), str):
        chunks.append(('audio/wav', base64.b64decode(response['audioContent'], validate=True)))
    else:
        candidates = response.get('candidates', [])
        if len(candidates) != 1:
            raise ValueError('Expected one audio candidate.')
        for candidate in candidates:
            if candidate.get('finishReason'):
                finish_reasons.append(str(candidate['finishReason']))
            for part in candidate.get('content', {}).get('parts', []):
                inline = part.get('inlineData') or part.get('inline_data')
                if inline and isinstance(inline.get('data'), str):
                    chunks.append((inline.get('mimeType') or inline.get('mime_type') or '',
                                   base64.b64decode(inline['data'], validate=True)))
    if not chunks:
        raise ValueError('Response contained no audio; no automatic retry.')
    if any(reason != 'STOP' for reason in finish_reasons):
        raise ValueError('Generation did not finish normally; refusing partial audio.')
    pcm_parts, mimes, source_hashes = [], [], []
    for mime, raw in chunks:
        mimes.append(mime)
        source_hashes.append(digest(raw))
        if raw[:4] == b'RIFF' and raw[8:12] == b'WAVE':
            _, pcm = validate_wav(raw)
        elif re.match(r'^audio/(?:l16|pcm)(?:;|$)', mime, flags=re.I):
            rate_match = re.search(r'(?:rate|samplerate)=(\d+)', mime, flags=re.I)
            if rate_match and int(rate_match[1]) != RATE:
                raise ValueError('Unexpected raw PCM sample rate.')
            if len(raw) % 2:
                raise ValueError('Incomplete PCM sample.')
            # Google documents its L16 TTS output as little-endian PCM16.
            pcm = raw
        else:
            raise ValueError('Unsupported audio container in response.')
        pcm_parts.append(pcm)
    added_container = not (len(chunks) == 1 and chunks[0][1][:4] == b'RIFF')
    if not added_container:
        wav_audio = chunks[0][1]  # Keep the model's WAV bytes unchanged.
    else:
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(b''.join(pcm_parts))
        wav_audio = stream.getvalue()
    duration, _ = validate_wav(wav_audio)
    return wav_audio, {
        'rawDuration': duration, 'sampleRateHz': RATE, 'channels': 1,
        'sampleWidthBytes': 2, 'sourceMimeTypes': mimes,
        'sourceAudioPayloadSha256': source_hashes,
        'wavContainerAdded': added_container, 'waveformChanged': False,
        'finishReasons': finish_reasons,
    }


def safe_error(http_error):
    # Persist only typed Google error codes/reasons, never response bodies.
    try:
        value = json.loads(http_error.read(256 * 1024))
        error = value.get('error', {})
        status = str(error.get('status', ''))
        reasons = [str(d.get('reason', '')) for d in error.get('details', []) if isinstance(d, dict)]
        clean = lambda s: s if re.fullmatch(r'[A-Z0-9_]{1,80}', s) else ''
        return {'googleStatus': clean(status), 'googleReasons': list(filter(None, map(clean, reasons)))}
    except Exception:
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--account', required=True)
    parser.add_argument('--models', nargs='+', choices=tuple(MODEL_IDS), default=list(MODEL_IDS))
    parser.add_argument('--config', default='gemini-audition.json')
    parser.add_argument('--output', default='gemini-audition')
    parser.add_argument('--pro-api', choices=('cloud-tts', 'agent-platform'), default='cloud-tts')
    parser.add_argument('--dry-run', action='store_true', help='Validate and describe requests without authentication or network calls.')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', args.project):
        parser.error('Use a Google Cloud project ID, not a display name or number.')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', args.account):
        parser.error('Use the explicitly authenticated account email.')
    config_file = HERE / local_name(args.config)
    spec = json.loads(config_file.read_text())
    if spec.get('speed') != 1 or spec.get('language') != 'ja-JP':
        raise ValueError('Audition must use Japanese and normal speed.')
    if not 1 <= len(spec['turns']) <= 9:
        raise ValueError('This short audition is limited to nine turns per model.')
    if len({t['id'] for t in spec['turns']}) != len(spec['turns']):
        raise ValueError('Duplicate turn IDs.')
    selected = list(dict.fromkeys(args.models))
    models = {m['key']: m for m in spec['models']}
    plans = []
    for key in selected:
        identifier(key)
        model = models[key]
        for turn in spec['turns']:
            identifier(turn['id'])
            if turn['speaker'] not in spec['members'] or not isinstance(turn['speech'], str):
                raise ValueError('Invalid speaker or text.')
            endpoint, family, payload, style, voice = request_for(spec, model, turn, args.project, args.pro_api)
            body = encoded(payload)
            plans.append((model, turn, endpoint, family, body, style, voice))
    if args.dry_run:
        print(json.dumps({'networkRequestsMade': False, 'authenticationRead': False,
                          'project': args.project, 'account': args.account,
                          'models': selected, 'requests': len(plans),
                          'hosts': sorted({'aiplatform.googleapis.com' if 'aiplatform.googleapis.com/' in p[2] else 'texttospeech.googleapis.com' for p in plans})}))
        return 0

    out = HERE / 'output' / local_name(args.output)
    out.mkdir(parents=True, exist_ok=True)
    lock = out / '.gemini-generator.lock'
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(descriptor, str(os.getpid()).encode())
    os.close(descriptor)
    token = None
    journal_file = out / 'gemini-attempts.json'
    try:
        journal = json.loads(journal_file.read_text()) if journal_file.exists() else {}
        if not isinstance(journal, dict):
            raise ValueError('Attempt journal must be a JSON object.')
    except BaseException:
        lock.unlink(missing_ok=True)
        raise
    results = []
    state = 'running'
    created = now()

    def checkpoint():
        write_json(journal_file, journal)
        write_json(out / 'gemini-generation.json', {
            'schemaVersion': 1, 'projectId': args.project, 'account': args.account,
            'configuration': config_file.name, 'configurationSha256': digest(config_file.read_bytes()),
            'createdAt': created, 'updatedAt': now(), 'generationState': state,
            'models': selected, 'results': results, 'credentialsPersisted': False,
            'authentication': 'Existing gcloud user access token; memory only',
            'requestsRetried': 0, 'sourceAudioSpeedChanged': False,
        })

    opener = build_opener(NoRedirect)
    stopped_models = set()
    try:
        checkpoint()
        for index, (model, turn, endpoint, family, body, style, voice) in enumerate(plans):
            key = model['key'] + ':' + turn['id']
            folder = out / model['key']
            folder.mkdir(parents=True, exist_ok=True)
            audio_file = folder / (turn['id'] + '.wav')
            info_file = folder / (turn['id'] + '-raw.json')
            request_dir = out / 'requests-sent' / model['key']
            request_dir.mkdir(parents=True, exist_ok=True)
            request_file = request_dir / (turn['id'] + '.json')
            record = {
                'modelKey': model['key'], 'modelId': model['id'], 'turnId': turn['id'],
                'speaker': turn['speaker'], 'voice': voice, 'speed': 1,
                'text': turn['speech'], 'caption': turn['caption'], 'style': style,
                'endpoint': endpoint, 'apiFamily': family,
                'requestSha256': digest(body), 'requestHashEncoding': 'UTF-8 JSON, sorted keys, compact separators',
                'textSha256': digest(turn['speech'].encode()), 'styleSha256': digest(style.encode()),
                'file': str(audio_file.relative_to(out)),
                'requestFile': str(request_file.relative_to(out)),
            }
            if audio_file.exists():
                if not info_file.exists():
                    raise ValueError('Audio exists without bound provenance; refusing to regenerate.')
                saved = json.loads(info_file.read_text())
                if (saved.get('status') != 'succeeded'
                        or saved.get('requestSha256') != record['requestSha256']
                        or saved.get('endpoint') != endpoint
                        or saved.get('rawAudioSha256') != digest(audio_file.read_bytes())):
                    raise ValueError('Cached audio differs from the selected request; refusing to regenerate.')
                validate_wav(audio_file.read_bytes())
                results.append(dict(saved, reusedRaw=True))
                checkpoint()
                print(f'{index + 1}/{len(plans)} {model["key"]} {turn["id"]}: cached', flush=True)
                continue
            if key in journal:
                previous = dict(journal[key])
                if previous.get('status') == 'started':
                    previous['status'] = 'usage_unknown'
                previous['automaticResendRefused'] = True
                results.append(previous)
                stopped_models.add(model['key'])
                checkpoint()
                print(f'{index + 1}/{len(plans)} {model["key"]} {turn["id"]}: existing attempt; no resend', flush=True)
                continue
            if model['key'] in stopped_models:
                results.append(dict(record, status='not_attempted_after_error'))
                checkpoint()
                continue
            write_bytes(request_file, body)
            if token is None:
                token = token_for(args.account)
            record.update(status='started', requestedAt=now())
            results.append(record)
            journal[key] = dict(record)
            checkpoint()
            request = Request(endpoint, data=body, method='POST', headers={
                'Authorization': 'Bearer ' + token,
                'x-goog-user-project': args.project,
                'Content-Type': 'application/json; charset=utf-8',
                'Accept': 'application/json',
                'User-Agent': 'egakumi-study-gemini-audition/1.0',
            })
            started = time.monotonic()
            try:
                with opener.open(request, timeout=180) as response:
                    raw_response = response.read(MAX_RESPONSE_BYTES + 1)
                    record.update(httpStatus=response.status,
                                  requestId=response.headers.get('x-request-id') or response.headers.get('x-google-request-id'))
                if len(raw_response) > MAX_RESPONSE_BYTES:
                    raise ValueError('Response exceeded the audition size limit.')
                record['responsePayloadSha256'] = digest(raw_response)
                decoded = json.loads(raw_response)
                wav_audio, audio_info = audio_from_response(decoded)
                record.update(audio_info)
                record.update(status='succeeded', completedAt=now(),
                              elapsedSeconds=round(time.monotonic() - started, 3),
                              rawAudioSha256=digest(wav_audio))
                usage = decoded.get('usageMetadata') or decoded.get('usage_metadata')
                record['usageMetadata'] = usage if isinstance(usage, dict) else None
                record['usageReported'] = bool(usage)
                record['estimatedAudioTokensAt25PerSecond'] = round(record['rawDuration'] * 25)
                write_bytes(audio_file, wav_audio)
                write_json(info_file, record)
            except HTTPError as error:
                record.update(status='failed_http', httpStatus=error.code,
                              elapsedSeconds=round(time.monotonic() - started, 3), **safe_error(error))
                stopped_models.add(model['key'])
            except (URLError, TimeoutError, OSError) as error:
                record.update(status='usage_unknown', errorType=type(error).__name__,
                              elapsedSeconds=round(time.monotonic() - started, 3))
                stopped_models.add(model['key'])
            except (ValueError, KeyError, TypeError, wave.Error) as error:
                record.update(status='failed_response' if record.get('httpStatus') == 200 else 'usage_unknown',
                              errorType=type(error).__name__,
                              elapsedSeconds=round(time.monotonic() - started, 3))
                stopped_models.add(model['key'])
            journal[key] = dict(record)
            checkpoint()
            error_codes = list(dict.fromkeys(filter(None, [record.get('googleStatus')]
                                                     + record.get('googleReasons', []))))
            print(f'{index + 1}/{len(plans)} {model["key"]} {turn["id"]}: {record["status"]}'
                  + (f' HTTP {record["httpStatus"]}' if record.get('httpStatus') else '')
                  + (' [' + ', '.join(error_codes) + ']' if error_codes else ''), flush=True)
        state = 'complete' if all(r.get('status') == 'succeeded' for r in results) else 'failed'
        checkpoint()
        print(json.dumps({'state': state, 'succeeded': sum(r.get('status') == 'succeeded' for r in results),
                          'selectedRequests': len(plans), 'credentialsPersisted': False}))
        return 0 if state == 'complete' else 1
    finally:
        token = None
        for record in results:
            if record.get('status') == 'started':
                record['status'] = 'usage_unknown'
                journal[record['modelKey'] + ':' + record['turnId']] = dict(record)
        if state == 'running':
            state = 'interrupted'
            checkpoint()
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, FileExistsError, FileNotFoundError, subprocess.TimeoutExpired) as error:
        # No external command output or HTTP response body is ever printed.
        print(f'Stopped: {type(error).__name__}. {error}' if isinstance(error, ValueError)
              else f'Stopped: {type(error).__name__}. No automatic request retry.', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Interrupted. In-flight usage is unknown; no automatic resend.', file=sys.stderr)
        sys.exit(130)
