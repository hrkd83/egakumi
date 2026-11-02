#!/usr/bin/env python3
"""Independent checks for lesson 03's fixed teaching settings revision.

Reads only source/media, and writes reports inside this episode's QA directory.
Use --source-only before the full encode; the default also compares the finished
lesson timing and cached audio with the preserved original version.
"""
from __future__ import annotations
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
EPISODE = HERE.parent
TOOLKIT = EPISODE.parents[1]
OUT = TOOLKIT / 'output' / 'lesson-03-gemini'
QA = OUT / 'settings-v2-qa'
RED = {'#de343b', '#de343bff', '#e53935', '#dc2626', '#ef4444'}
FORBIDDEN = {'zoom', 'camera', 'motion', 'pan', 'pulse', 'pointer', 'mockPointer'}
QUIZ_QUESTIONS = {f'lesson03-turn-{n:03d}-{speaker}' for n, speaker in [(106, 'mako'), (107, 'manabu'), (109, 'mako'), (111, 'mako')]}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def read(path):
    return json.loads(path.read_text())

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def strip_visual(script):
    value = copy.deepcopy(script)
    value.pop('visualStyle', None)
    for turn in value['turns']:
        turn.pop('visual', None)
    return value

def recursive_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from recursive_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_keys(child)

def highlighted(element):
    if any(k.startswith('data-highlight') or k.startswith('data-focus') for k in element.attrib):
        return True
    stroke = element.attrib.get('stroke', '').lower().replace(' ', '')
    return stroke in RED

def canonical_svg(file):
    text = file.read_text()
    require(not re.search(r'<!DOCTYPE|<!ENTITY|<script\b|<foreignObject\b|<animate\b|<animateTransform\b|<set\b|\bon[a-z]+\s*=|@import', text, re.I), f'{file.name}: active SVG content')
    require(not re.search(r'(?:href|src)\s*=\s*[\"\'](?!#|data:)', text, re.I), f'{file.name}: external SVG reference')
    require(not any(not m.group(1).strip().strip('\"\'').startswith('#') for m in re.finditer(r'url\(([^)]+)\)', text, re.I)), f'{file.name}: external SVG URL')
    root = ET.fromstring(text)
    require(root.attrib.get('viewBox') == '0 0 1207 511', f'{file.name}: body viewport changed')
    highlights = []
    def visit(parent):
        for child in list(parent):
            if highlighted(child):
                # Neutral SVGs keep an empty highlight group for identical
                # construction. Count visible target geometry only.
                if len(child) or child.tag.rsplit('}', 1)[-1] not in {'g', 'defs'}:
                    highlights.append(ET.tostring(child, encoding='unicode'))
                parent.remove(child)
            else:
                visit(child)
    visit(root)
    # ElementTree preserves white space. Ignore indentation only, never values.
    for element in root.iter():
        if element.text and not element.text.strip(): element.text = None
        if element.tail and not element.tail.strip(): element.tail = None
    return ET.tostring(root, encoding='unicode'), len(highlights)

def geometry(visual):
    if visual['layout'] == 'svg':
        file = (EPISODE / visual['svg']).resolve()
        require(file.is_relative_to(EPISODE.resolve()) and file.is_file(), 'SVG escapes the episode source directory')
        return canonical_svg(file)
    value = copy.deepcopy(visual)
    count = len(value.pop('highlightFields', [])) + len(value.pop('highlightRows', []))
    value.pop('focus', None)
    return json.dumps(value, sort_keys=True, ensure_ascii=False), count

def verify_source():
    before = read(EPISODE / 'versions/diagram-v1/script.json')
    script = read(EPISODE / 'script.json')
    require(strip_visual(before) == strip_visual(script), 'Spoken text, captions, delivery, chapter order, or nonvisual production input changed')
    require(script.get('visualStyle') == 'teaching-settings-v2', 'Latest teaching settings style was not selected')
    require(len(script['turns']) == 119, 'Expected all 119 original turns')
    body = [turn for turn in script['turns'] if turn['chapterId'] != 'opening']
    require(len(body) == 115, 'Expected all 115 body turns')
    groups = []
    seen = set()
    shapes = {}
    svg_assets = {}
    counts = {'settings': 0, 'svg': 0}
    question_checks = []
    for turn in body:
        visual = turn['visual']
        require(visual.get('layout') in counts, f'{turn["id"]}: body is not a settings view or settings shell')
        counts[visual['layout']] += 1
        require(not FORBIDDEN.intersection(recursive_keys(visual)), f'{turn["id"]}: forbidden body motion option')
        view_id = visual.get('viewId')
        require(isinstance(view_id, str) and bool(view_id), f'{turn["id"]}: no stable viewId')
        shape, highlights = geometry(visual)
        if visual['layout'] == 'svg':
            svg_assets[visual['svg']] = digest(EPISODE/visual['svg'])
        if view_id not in shapes:
            shapes[view_id] = shape
        require(shapes[view_id] == shape, f'{turn["id"]}: {view_id} changes geometry/content beyond red target outlines')
        if not groups or groups[-1]['viewId'] != view_id:
            require(view_id not in seen, f'{turn["id"]}: returns to earlier view {view_id}')
            seen.add(view_id)
            groups.append({'viewId': view_id, 'turnIds': []})
        groups[-1]['turnIds'].append(turn['id'])
        if turn['id'] in QUIZ_QUESTIONS:
            require(highlights == 0, f'{turn["id"]}: quiz question reveals a red answer highlight')
            question_checks.append(turn['id'])
    require(set(question_checks) == QUIZ_QUESTIONS, 'Quiz question coverage incomplete')
    # The revision-time snapshot is immutable evidence, not a list of files that
    # must exist forever. Authorized toolkit cleanup may retire old sample files.
    # Check every current shared source/asset against the production profile.
    historical_shared_file = HERE / 'shared-before.json'
    historical_shared = read(historical_shared_file)
    require(isinstance(historical_shared, dict) and bool(historical_shared), 'Missing historical shared-file snapshot')
    profile_file = TOOLKIT / 'production-profile.json'
    profile = read(profile_file)
    shared = {}
    for group in ('assetSha256', 'commonFileSha256'):
        entries = profile.get(group)
        require(isinstance(entries, dict) and bool(entries), f'Current production profile has no {group}')
        for name, value in entries.items():
            require(isinstance(name, str) and isinstance(value, str) and bool(re.fullmatch(r'[0-9a-f]{64}', value)), f'Invalid shared hash entry: {name}')
            file = (TOOLKIT / name).resolve()
            require(file.is_relative_to(TOOLKIT.resolve()), f'Shared profile path escapes the toolkit: {name}')
            require(name not in shared or shared[name] == value, f'Conflicting shared profile hash: {name}')
            shared[name] = value
    changed = [name for name, value in shared.items() if not (TOOLKIT/name).is_file() or digest(TOOLKIT/name) != value]
    require(not changed, 'Current shared production files/assets differ from profile: ' + ', '.join(changed))
    return script, {'status': 'passed', 'sourceScriptSha256': digest(EPISODE/'script.json'), 'preservedSourceScriptSha256': digest(EPISODE/'versions/diagram-v1/script.json'), 'svgAssets': svg_assets, 'visualStyle': script['visualStyle'], 'turnCount': 119, 'bodyTurnCount': len(body), 'layoutCounts': counts, 'viewGroupCount': len(groups), 'viewGroups': groups, 'quizQuestionsWithoutAnswerOutlines': question_checks, 'bodyCamera': 'fixed', 'speechCaptionsDeliveryAndOrderUnchanged': True, 'sharedFilesAndAssetsMatchCurrentProfile': True, 'sharedFilesAndAssetsChecked': len(shared), 'sharedProductionProfileId': profile['id'], 'sharedProductionProfileSha256': digest(profile_file), 'historicalSharedSnapshot': {'file': str(historical_shared_file.relative_to(TOOLKIT)), 'sha256': digest(historical_shared_file), 'entries': len(historical_shared), 'comparedWithCurrentFiles': False}, 'listeningReview': 'not performed; source audio reused', 'networkRequestsMade': False}

def verify_media(script, report):
    timeline = read(OUT / 'lesson-timeline.json')
    before = read(OUT / 'versions/diagram-v1/lesson-timeline.json')
    require(timeline['frameCount'] == before['frameCount'] == 35509, 'Frame count changed')
    require(timeline['duration'] == before['duration'], 'Duration changed')
    require(len(timeline['scenes']) == len(before['scenes']), 'Scene count changed')
    for current, original in zip(timeline['scenes'], before['scenes']):
        for key in ('id', 'start', 'end', 'frameCount', 'speaker', 'phase', 'voice', 'speech', 'audioDuration', 'rawAudioSha256'):
            require(current.get(key) == original.get(key), f'{original["id"]}: source audio/timing changed ({key})')
    source_body = {turn['id']: turn for turn in script['turns'] if turn['chapterId'] != 'opening'}
    rendered_body = {scene['id']: scene for scene in timeline['scenes']
                     if scene['phase'] == 'body' and scene.get('speaker')}
    require(source_body.keys() == rendered_body.keys(), 'Rendered body scenes differ from the latest source selection')
    for turn_id, turn in source_body.items():
        actual = copy.deepcopy(rendered_body[turn_id]['visual'])
        expected = turn['visual']
        if expected['layout'] == 'svg':
            source_file = EPISODE/expected['svg']
            require(actual.pop('svgSha256', None) == digest(source_file), f'{turn_id}: rendered SVG hash differs from latest source')
            encoded = actual.pop('svgDataUri', '')
            prefix = 'data:image/svg+xml;base64,'
            require(encoded.startswith(prefix), f'{turn_id}: rendered SVG has no bound data URI')
            require(base64.b64decode(encoded[len(prefix):], validate=True) == source_file.read_bytes(), f'{turn_id}: rendered SVG content differs from latest source')
        require(actual == expected, f'{turn_id}: rendered visual differs from latest source (including native settings)')
    original_cache = read(OUT / 'versions/diagram-v1/audio-cache.json')
    cache = read(OUT / 'audio-cache.json')
    require(cache == original_cache, 'Audio cache plan changed')
    for name, value in cache['files'].items():
        require(digest(OUT/name) == value, f'{name}: cached mix/narration changed')
    originals = read(OUT/'versions/diagram-v1/generation.json')['results']
    results = read(OUT/'generation.json')['results']
    require(len(originals) == len(results) == 119, 'Generation result count changed')
    for original, current in zip(originals, results):
        for key in ('id', 'voice', 'speed', 'requestSha256', 'rawAudioSha256', 'sourceSpeechSha256', 'parameters'):
            require(original.get(key) == current.get(key), f'{current["id"]}: recorded source changed ({key})')
        require(current.get('reusedRaw') is True, f'{current["id"]}: audio was not reported as reused')
    require(read(OUT/'generation.json').get('attemptedThisRun') == 0, 'Additional TTS request detected')
    report.update({'durationSeconds': timeline['duration'], 'frameCount': timeline['frameCount'], 'latestSourceVisualsMatchedToAllRenderedBodyScenes': len(rendered_body), 'originalAudioTimelineUnchanged': True, 'cachedNarrationAndMixUnchanged': True, 'all119RawRecordingsReused': True, 'ttsRequestsThisRevision': 0, 'mp4': read(OUT/'lesson-export.json')})


def extract_frames():
    """Create QA stills from the final MP4, never from source SVG previews."""
    timeline = read(OUT/'lesson-timeline.json')
    scenes = {scene['id']: scene for scene in timeline['scenes']}
    movie = OUT/read(OUT/'lesson-export.json')['file']
    selections = [('cast-establish', .3)]
    selections += [(turn['id'], 1.5) for turn in read(EPISODE/'script.json')['turns']
                   if int(turn['id'].split('-')[2]) in
                   {1, 2, 3, 9, 13, 16, 20, 22, 28, 34, 35, 36, 37, 38,
                    46, 51, 56, 60, 65, 77, 85, 89, 94, 96, 100, 105,
                    106, 108, 109, 110, 111, 112, 114}]
    selections.append(('closing', 3.5))
    destination = QA/'frames'
    destination.mkdir(parents=True, exist_ok=True)
    manifest = []
    for index, (scene_id, offset) in enumerate(selections):
        scene = scenes[scene_id]
        time = scene['start'] + min(offset, scene['duration']/2)
        filename = f'{index:02d}-{scene_id}.png'
        result = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(time),
                                 '-i', str(movie), '-frames:v', '1', str(destination/filename)],
                                capture_output=True, text=True)
        require(result.returncode == 0, f'Cannot extract final frame {scene_id}: {result.stderr[-1000:]}')
        manifest.append({'file': filename, 'sceneId': scene_id, 'timeSeconds': time,
                         'viewId': scene.get('visual', {}).get('viewId')})
    (destination/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    for start in range(0, len(manifest), 12):
        files = manifest[start:start+12]
        listing = destination/f'contact-{start//12+1}.txt'
        listing.write_text(''.join(f"file '{(destination/item['file']).resolve()}'\nduration 1\n" for item in files))
        result = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0',
                                 '-i', str(listing), '-vf', 'fps=1,scale=533:300,tile=3x4:padding=5:margin=5:color=gray',
                                 '-frames:v', '1', str(QA/f'contact-{start//12+1}.png')],
                                capture_output=True, text=True)
        require(result.returncode == 0, 'Cannot make contact sheet: '+result.stderr[-1000:])
    return len(manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', action='store_true')
    parser.add_argument('--extract-frames', action='store_true')
    args = parser.parse_args()
    script, report = verify_source()
    if not args.source_only:
        verify_media(script, report)
    if args.extract_frames:
        require(not args.source_only, 'Extract only after final media verification')
        report['finalFramesExtracted'] = extract_frames()
    QA.mkdir(parents=True, exist_ok=True)
    destination = QA / ('source-verification.json' if args.source_only else 'revision-verification.json')
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'viewGroups': report['viewGroupCount'], 'bodyTurns': report['bodyTurnCount'], 'report': str(destination)}, ensure_ascii=False))

if __name__ == '__main__':
    main()
