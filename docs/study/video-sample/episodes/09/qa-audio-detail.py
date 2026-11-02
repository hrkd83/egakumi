#!/usr/bin/env python3
"""Inspect exact integer PCM endpoints and draw the longest full-scale plateau.

No waveform is modified. This quantifies digital saturation candidates; it
cannot determine whether a listener hears a defect or whether rerecording is
necessary. Near-full-scale and exact endpoint counts remain distinct.
"""
from array import array
import hashlib
import html
import json
import math
from pathlib import Path
import sys
import wave

HERE = Path(__file__).resolve().parent
OUT = HERE.parent.parent / 'output/lesson-09-gemini'


def load_wav(path):
    with wave.open(str(path), 'rb') as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        rate = w.getframerate()
        values = array('h')
        values.frombytes(w.readframes(w.getnframes()))
        if sys.byteorder != 'little':
            values.byteswap()
        return rate, values


def main():
    script = json.loads((HERE / 'script.json').read_text())
    config = json.loads((HERE / 'generation.json').read_text())
    assert config['output'] == 'lesson-09-gemini'
    rows = []
    for candidate in config['candidates']:
        path = OUT / (candidate['id'] + '-raw.wav')
        rate, values = load_wav(path)
        counts = {str(v): values.count(v) for v in (32767, 32766, 32765, -32765, -32766, -32767, -32768)}
        longest = run = exact_longest = exact_run = at = 0
        near = 0
        for i, value in enumerate(values):
            if abs(value) >= 32768 * .9999:
                near += 1
                run += 1
                if run > longest:
                    longest, at = run, i - run + 1
            else:
                run = 0
            if value in (32767, -32768):
                exact_run += 1
                exact_longest = max(exact_longest, exact_run)
            else:
                exact_run = 0
        exact = counts['32767'] + counts['-32768']
        rows.append({'id': candidate['id'], 'samples': len(values), 'rate': rate,
                     'rawSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                     'endpointAndNearbyCounts': counts, 'nearFullScaleCount': near,
                     'exactEndpointCount': exact, 'exactEndpointFraction': exact / len(values),
                     'longestNearFullScaleRun': longest, 'longestExactEndpointRun': exact_longest,
                     'longestRunStartSample': at, 'longestRunStartSeconds': at / rate,
                     'longestRunSeconds': longest / rate, 'longestRunValues': list(values[at:at+longest])})
    total = sum(r['samples'] for r in rows)
    exact = sum(r['exactEndpointCount'] for r in rows)
    worst = max(rows, key=lambda r: r['longestExactEndpointRun'])
    largest_fraction = max(rows, key=lambda r: r['exactEndpointFraction'])
    frames = math.ceil(4 * 24) + math.ceil(1.1 * 24) + math.ceil(8 * 24)
    previous = None
    durations = {r['id']: r['samples'] / r['rate'] for r in rows}
    for turn in script['turns']:
        if turn['id'] in script['firstCut']['omitTurnIds']:
            continue
        opening = turn['chapterId'] == 'opening'
        if not opening and previous != turn['chapterId']:
            frames += math.ceil(1.25 * 24)
        frames += math.ceil((durations[turn['id']] + (.12 if opening else .06)
                             + turn.get('gapAfterSeconds', .10) + turn.get('pauseAfterSeconds', 0)) * 24)
        previous = turn['chapterId']
    report = {
        'status': 'review_candidates_quantified', 'waveformChanged': False,
        'speechRecognitionOrListeningPerformed': False,
        'scriptSha256': hashlib.sha256((HERE / 'script.json').read_bytes()).hexdigest(),
        'totalPcmSamples': total, 'exactPositiveEndpointCount': sum(r['endpointAndNearbyCounts']['32767'] for r in rows),
        'exactNegativeEndpointCount': sum(r['endpointAndNearbyCounts']['-32768'] for r in rows),
        'nearFullScaleCount': sum(r['nearFullScaleCount'] for r in rows),
        'exactEndpointCount': exact, 'exactEndpointFraction': exact / total,
        'turnsWithAnyExactEndpoint': sum(r['exactEndpointCount'] > 0 for r in rows),
        'longestExactPlateau': worst, 'largestTurnEndpointFraction': largest_fraction,
        'plannedFinalFrameCount': frames, 'plannedFinalDurationSeconds': frames / 24,
        'conclusion': 'Short exact digital full-scale plateaus exist. Their auditory impact and a rerecording decision cannot be determined by waveform measurements alone.',
        'limitations': ['A final true-peak limiter prevents final-output overs but cannot reconstruct saturated raw peaks.',
                        'All originals are preserved unchanged. No automatic rerecording is triggered.'],
        'records': rows,
    }
    (HERE / 'qa-audio-detail.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')

    rate, values = load_wav(OUT / (worst['id'] + '-raw.wav'))
    at = worst['longestRunStartSample']
    center = at + worst['longestExactEndpointRun'] // 2
    def text(x, y, label, size=17, color='#dfeaf4'):
        return f'<text x="{x}" y="{y}" font-family="sans-serif" font-size="{size}" fill="{color}">{html.escape(str(label))}</text>'
    content = '<rect width="1400" height="920" fill="#071827"/>'
    content += text(45, 42, 'Episode 09 — exact 16-bit PCM full-scale plateau', 28)
    content += text(45, 75, f'{worst["id"]} / {at / rate:.6f}s / longest run {worst["longestExactEndpointRun"]} samples = {worst["longestRunSeconds"]*1000:.3f} ms')
    for panel, half in enumerate((240, 48)):
        start, end = max(0, center-half), min(len(values)-1, center+half)
        x, y, width, height = 105, 135+panel*320, 1240, 250
        content += text(x, y-18, f'{(end-start)/rate*1000:.3f} ms window; raw integer samples, no processing')
        for level in (-32768, -16384, 0, 16384, 32767):
            py = y + (32768-level)/65536*height
            content += f'<path d="M{x} {py}H{x+width}" stroke="#3b5367" stroke-width="1"/>' + text(13, py+5, level, 14)
        points = ' '.join(f'{x+(i-start)/(end-start)*width:.3f},{y+(32768-values[i])/65536*height:.3f}' for i in range(start, end+1))
        content += f'<polyline points="{points}" fill="none" stroke="#8ce5e0" stroke-width="1.8"/>'
        for i in range(start, end+1):
            if values[i] in (32767, -32768):
                px = x+(i-start)/(end-start)*width
                py = y+(32768-values[i])/65536*height
                content += f'<circle cx="{px:.3f}" cy="{py:.3f}" r="3" fill="#ff746d"/>'
        content += text(x, y+height+23, f'{start/rate:.6f}s', 14)
        content += text(x+width-110, y+height+23, f'{end/rate:.6f}s', 14)
    content += text(45, 805, 'Red points are exact +32767 / -32768 endpoints. This is a digital full-scale plateau.', 18)
    content += text(45, 838, f'All raw audio: {exact} / {total} samples = {exact/total*100:.6f}%; maximum single-turn fraction {largest_fraction["exactEndpointFraction"]*100:.6f}%.', 18)
    content += text(45, 871, 'Waveform measurements do not determine audible distortion, pronunciation, omissions, or naturalness.', 18)
    (HERE / 'qa-audio-detail-waveform.svg').write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="920" viewBox="0 0 1400 920">{content}</svg>')
    print(json.dumps({k: report[k] for k in ('status', 'totalPcmSamples', 'nearFullScaleCount', 'exactEndpointCount', 'exactEndpointFraction', 'turnsWithAnyExactEndpoint', 'plannedFinalFrameCount', 'plannedFinalDurationSeconds')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
