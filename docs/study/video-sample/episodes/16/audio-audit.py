#!/usr/bin/env python3
"""Read-only waveform audit of episode 16's completed Gemini originals.

This measures PCM signals only. It does not recognize words, assess pronunciation,
listen to speech, call APIs, modify originals, or change synthesis records.
"""
from __future__ import annotations

import array
from collections import Counter
import json
import math
from pathlib import Path
import statistics
import sys
import unicodedata
import wave

EPISODE = Path(__file__).resolve().parent
TOOLKIT = EPISODE.parent.parent
OUTPUT = TOOLKIT / 'output' / 'lesson-16-gemini'
RATE = 24000
SILENCE_DBFS = -50.0
WINDOW_SECONDS = .02


def dbfs(value: float) -> float:
    return round(20 * math.log10(max(value, 1e-12)), 3)


def speech_characters(text: str) -> int:
    return sum(unicodedata.category(character)[0] in ('L', 'N') for character in text)


def run_lengths(values):
    result = []
    start = None
    for index, quiet in enumerate(values + [False]):
        if quiet and start is None:
            start = index
        elif not quiet and start is not None:
            result.append((start, index))
            start = None
    return result


def measure(turn):
    identifier = turn['id']
    path = OUTPUT / (identifier + '-raw.wav')
    with wave.open(str(path), 'rb') as audio:
        channels, width, rate, frames = (audio.getnchannels(), audio.getsampwidth(),
                                        audio.getframerate(), audio.getnframes())
        if (channels, width, rate) != (1, 2, RATE) or frames <= 0:
            raise ValueError(f'Unexpected PCM format: channels={channels}, width={width}, rate={rate}, frames={frames}')
        payload = audio.readframes(frames)
        if len(payload) != frames * width:
            raise ValueError('Truncated PCM payload')
    samples = array.array('h')
    samples.frombytes(payload)
    if sys.byteorder != 'little':
        samples.byteswap()
    squares = sum(value * value for value in samples)
    nonzero = sum(value != 0 for value in samples)
    peak = max(abs(value) for value in samples)
    clipped = 0
    clip_run = longest_clip_run = 0
    for value in samples:
        if abs(value) >= 32760:
            clipped += 1
            clip_run += 1
            longest_clip_run = max(longest_clip_run, clip_run)
        else:
            clip_run = 0
    window = int(rate * WINDOW_SECONDS)
    quiet = []
    for start in range(0, frames, window):
        block = samples[start:start + window]
        block_rms = math.sqrt(sum(value * value for value in block) / len(block)) / 32768
        quiet.append(dbfs(block_rms) <= SILENCE_DBFS)
    silences = run_lengths(quiet)
    leading = silences[0][1] * WINDOW_SECONDS if silences and silences[0][0] == 0 else 0
    trailing = min(frames / rate, (len(quiet) - silences[-1][0]) * WINDOW_SECONDS) if silences and silences[-1][1] == len(quiet) else 0
    internal = [end - start for start, end in silences if start > 0 and end < len(quiet)]
    duration = frames / rate
    characters = speech_characters(turn['speech'])
    rms = math.sqrt(squares / frames) / 32768
    return {
        'id': identifier,
        'speaker': turn['speaker'],
        'file': str(path.relative_to(TOOLKIT)),
        'durationSeconds': round(duration, 6),
        'spokenCharacterCount': characters,
        'charactersPerSecond': round(characters / duration, 3),
        'rmsDBFS': dbfs(rms),
        'peakDBFS': dbfs(peak / 32768),
        'nonzeroSampleFraction': round(nonzero / frames, 6),
        'nearFullScaleFraction': round(clipped / frames, 8),
        'longestNearFullScaleRunSamples': longest_clip_run,
        'dcOffset': round(sum(samples) / frames / 32768, 6),
        'leadingSilenceSeconds': round(leading, 3),
        'trailingSilenceSeconds': round(trailing, 3),
        'longestInternalSilenceSeconds': round(max(internal, default=0) * WINDOW_SECONDS, 3),
        'silentWindowFraction': round(sum(quiet) / len(quiet), 6),
        'flags': [],
    }


def main():
    script = json.loads((EPISODE / 'script.json').read_text())
    generation = json.loads((OUTPUT / 'generation.json').read_text())
    turns = [turn for turn in script['turns'] if turn['id'] not in script.get('firstCut', {}).get('omitTurnIds', [])]
    rows = generation.get('results', [])
    if len(turns) != 85 or len(rows) != 85 or generation.get('generationState') != 'complete' or any(row.get('status') != 'succeeded' for row in rows):
        raise SystemExit('Audit deferred: wait for all 85 episode 16 originals to complete successfully.')
    if {turn['id'] for turn in turns} != {row['id'] for row in rows}:
        raise SystemExit('Episode script and completed recordings do not identify the same turns.')
    measured = []
    errors = []
    for turn in turns:
        try:
            measured.append(measure(turn))
        except (OSError, EOFError, ValueError, wave.Error) as error:
            errors.append({'id': turn['id'], 'error': str(error)})
    median_rates = {
        speaker: statistics.median(row['charactersPerSecond'] for row in measured
                                   if row['speaker'] == speaker and row['spokenCharacterCount'] >= 30)
        for speaker in ('mako', 'manabu', 'ikumi')
    }
    for row in measured:
        flags = row['flags']
        if row['nonzeroSampleFraction'] == 0:
            flags.append('zero_waveform')
        if row['rmsDBFS'] < -35:
            flags.append('very_low_rms')
        if row['rmsDBFS'] > -6:
            flags.append('very_high_rms')
        if row['nearFullScaleFraction'] > .001 or (row['nearFullScaleFraction'] > .0001 and row['longestNearFullScaleRunSamples'] >= 8):
            flags.append('possible_clipping')
        if abs(row['dcOffset']) > .05:
            flags.append('large_dc_offset')
        if row['leadingSilenceSeconds'] > 1.5:
            flags.append('long_leading_silence')
        if row['trailingSilenceSeconds'] > 1.8:
            flags.append('long_trailing_silence')
        if row['longestInternalSilenceSeconds'] > 1.8:
            flags.append('long_internal_silence')
        if row['silentWindowFraction'] > .5:
            flags.append('mostly_silent')
        if row['spokenCharacterCount'] >= 30:
            rate = row['charactersPerSecond']
            median = median_rates[row['speaker']]
            if rate < 2.7 or rate > 14:
                flags.append('unusual_character_rate')
            if rate < median * .5 or rate > median * 1.8:
                flags.append('speaker_relative_duration_outlier')
    suspects = [row for row in measured if row['flags']]
    report = {
        'status': 'needs-review' if suspects or errors else 'passed',
        'episode': '16',
        'mechanicalOnly': True,
        'speechRecognitionPerformed': False,
        'listeningPerformed': False,
        'originalsModified': False,
        'networkRequestsMade': False,
        'completedRecordingCount': len(rows),
        'waveformCount': len(measured),
        'totalOriginalDurationSeconds': round(sum(row['durationSeconds'] for row in measured), 6),
        'medianCharactersPerSecondBySpeaker': median_rates,
        'ruleNotes': {
            'silence': '20ms RMS windows <= -50dBFS; flag leading >1.5s, trailing/internal >1.8s or >50% silent windows.',
            'level': 'Flag RMS <-35 or >-6dBFS and DC offset magnitude >0.05.',
            'clipping': 'Flag samples with |value| >=32760 above 0.1%, or above 0.01% with a consecutive run >=8 samples.',
            'duration': 'For >=30 spoken letters/numbers: flag <2.7 or >14 chars/s, or outside 0.5–1.8 times the same-speaker median.',
            'interpretation': 'Flags identify suspicious signals for review; they do not prove incorrect words or assess pronunciation/naturalness.',
        },
        'suspectCount': len(suspects),
        'flagCounts': dict(Counter(flag for row in suspects for flag in row['flags'])),
        'errors': errors,
        'suspects': suspects,
        'turnMetrics': measured,
    }
    destination = EPISODE / 'audio-audit.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('status', 'waveformCount', 'totalOriginalDurationSeconds', 'suspectCount', 'flagCounts', 'errors', 'suspects')}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
