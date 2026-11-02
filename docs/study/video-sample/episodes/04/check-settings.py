#!/usr/bin/env python3
"""Check episode 04's fixed teaching views and limited speech revision.

Read-only. Prints a JSON report; writes no files and makes no network requests.
Run after render-diagrams.mjs has generated the final episode SVGs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
OLD = HERE / 'versions/diagram-v1/script.json'
STYLE = 'teaching-settings-v2'
TURN_FIELDS = (
    'id', 'speaker', 'chapterId', 'speech', 'caption', 'subtitleLines',
    'subtitleChunks', 'delivery', 'style', 'gapAfterSeconds', 'pauseAfterSeconds',
)
RERECORD = {
    'lesson04-turn-071-mako': 'lesson04-turn-071r2-mako',
    'lesson04-turn-072-ikumi': 'lesson04-turn-072r2-ikumi',
}
MOVEMENT = {'zoom', 'camera', 'motion', 'azureMotion', 'pulse', 'pointer', 'mockPointer'}
FORBIDDEN_TAGS = {'script', 'foreignObject', 'animate', 'animateMotion', 'animateTransform', 'set'}
SHELL_MARKERS = ('egakumi STUDY / 設定ビュー', '確認する設定')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def local_name(tag):
    return tag.rsplit('}', 1)[-1]


def svg_base(data):
    """Return geometry/text fingerprint after removing only highlight groups."""
    source = data.decode('utf-8')
    if re.search(r'<!DOCTYPE|<!ENTITY|<\?xml-stylesheet|@import|@keyframes|\banimation\s*:', source, re.I):
        raise ValueError('SVG contains a DTD, external CSS, or animation')
    for target in re.findall(r'url\(([^)]+)\)', source, re.I):
        if not target.strip().strip('"\'').startswith('#'):
            raise ValueError('SVG contains an external URL reference')
    root = ET.fromstring(data)
    if local_name(root.tag) != 'svg':
        raise ValueError('SVG root is missing')
    box = [float(x) for x in re.split(r'[\s,]+', root.get('viewBox', '').strip()) if x]
    if box != [0, 0, 1207, 511]:
        raise ValueError('SVG viewBox must be 0 0 1207 511')
    rendered_text = ''.join(root.itertext())
    if not all(marker in rendered_text for marker in SHELL_MARKERS):
        raise ValueError('SVG does not contain the shared teaching settings shell')
    for element in root.iter():
        if local_name(element.tag) in FORBIDDEN_TAGS:
            raise ValueError('SVG contains a script, foreignObject, or animated element')
        for attribute, value in element.attrib.items():
            name = local_name(attribute).lower()
            if name.startswith('on'):
                raise ValueError('SVG contains an event handler')
            if name in {'href', 'src'} and not value.startswith(('#', 'data:')):
                raise ValueError('SVG contains an external resource reference')
            for target in re.findall(r'url\(([^)]+)\)', value, re.I):
                if not target.strip().strip('"\'').startswith('#'):
                    raise ValueError('SVG contains an external URL reference')
    highlighted = [element for element in root.iter() if element.get('id') == 'highlights']
    if len(highlighted) != 1 or local_name(highlighted[0].tag) != 'g':
        raise ValueError('SVG needs exactly one <g id="highlights">')
    highlight_count = len(list(highlighted[0]))
    for parent in root.iter():
        for child in list(parent):
            if child is highlighted[0]:
                parent.remove(child)
    # Formatting whitespace and XML attribute order are not visual changes.
    for element in root.iter():
        if element.text is not None and not element.text.strip():
            element.text = None
        if element.tail is not None and not element.tail.strip():
            element.tail = None
        attributes = sorted(element.attrib.items())
        element.attrib.clear()
        element.attrib.update(attributes)
    return digest(ET.tostring(root, encoding='utf-8')), highlight_count


def projection(turn):
    return {key: turn[key] for key in TURN_FIELDS if key in turn}


def check_revision(source, previous, errors):
    for key in ('episodeKey', 'episodeNumber', 'episodeId', 'cast', 'speed',
                'speechSettings', 'logoIntroSeconds', 'logoClosingSeconds',
                'chapters', 'firstCut'):
        if source.get(key) != previous.get(key):
            errors.append(f'Unplanned audio/episode structure change: {key}')
    if source.get('speed') != 1:
        errors.append('Speech speed must remain 1')
    turns, old_turns = source.get('turns', []), previous.get('turns', [])
    if len(turns) != len(old_turns) or len(turns) != 87:
        errors.append('The existing 87-turn order and count must remain')
    for turn, old in zip(turns, old_turns):
        expected = projection(old)
        expected['id'] = RERECORD.get(old['id'], old['id'])
        actual = projection(turn)
        if old['id'] == 'lesson04-turn-071-mako':
            expected['speech'] = old['speech'].replace('応用編', '第八回の後')
            expected['caption'] = old['caption'].replace('応用編', '第8回の後')
            # New line breaks/chunk weights may accompany the changed caption.
            for key in ('subtitleLines', 'subtitleChunks'):
                expected.pop(key, None)
                actual.pop(key, None)
        if actual != expected:
            keys = sorted(key for key in set(actual) | set(expected)
                          if key not in actual or key not in expected or actual[key] != expected[key])
            errors.append(f'{old["id"]}: unplanned revision in {", ".join(keys)}')
        caption, chunks = turn.get('caption'), turn.get('subtitleChunks')
        if chunks:
            if ''.join(chunk.get('caption', '') for chunk in chunks) != caption:
                errors.append(f'{turn.get("id")}: chunks differ from the full caption')
            for chunk in chunks:
                lines = chunk.get('lines', [])
                if not 1 <= len(lines) <= 2 or ''.join(lines) != chunk.get('caption'):
                    errors.append(f'{turn.get("id")}: invalid two-line subtitle chunk')
                if not isinstance(chunk.get('weight', 1), (int, float)) or chunk.get('weight', 1) <= 0:
                    errors.append(f'{turn.get("id")}: invalid subtitle weight')
        elif not 1 <= len(turn.get('subtitleLines', [])) <= 2:
            errors.append(f'{turn.get("id")}: invalid subtitle lines')


def check(source_file):
    source_bytes, old_bytes = source_file.read_bytes(), OLD.read_bytes()
    source, previous = json.loads(source_bytes), json.loads(old_bytes)
    errors, warnings = [], []
    check_revision(source, previous, errors)
    if source.get('visualStyle') != STYLE:
        errors.append(f'visualStyle must be {STYLE}')
    views, runs, last_view = {}, [], None
    svg_cache = {}
    body = [turn for turn in source['turns'] if turn.get('chapterId') != 'opening']
    generator = HERE / 'render-diagrams.mjs'
    generator_bytes = generator.read_bytes() if generator.is_file() else b''
    generator_text = generator_bytes.decode('utf-8')
    shared_shell_reference = (
        '../../settings-visual.mjs' in generator_text
        and re.search(r'import\s*\{[^}]*\brenderSettingsShell\b', generator_text) is not None
        and re.search(r'\brenderSettingsShell\s*\(', generator_text) is not None
    )
    if any(turn.get('visual', {}).get('layout') == 'svg' for turn in body) and not shared_shell_reference:
        errors.append('Episode SVG generator must import and call the shared renderSettingsShell')
    for turn in body:
        turn_id, visual = turn.get('id'), turn.get('visual', {})
        view_id, layout = visual.get('viewId'), visual.get('layout')
        if not isinstance(view_id, str) or not re.fullmatch(r'[-a-zA-Z0-9_]+', view_id):
            errors.append(f'{turn_id}: missing or invalid viewId')
            continue
        if layout not in {'settings', 'svg'}:
            errors.append(f'{turn_id}: body layout must be settings or settings-shell SVG')
            continue
        if any(visual.get(key) for key in MOVEMENT):
            errors.append(f'{turn_id}: body camera or highlight animation is not fixed')
        state = {key: visual.get(key, []) for key in ('highlightRows', 'highlightFields')}
        if any(not isinstance(value, list) or any(not isinstance(x, str) for x in value)
               for value in state.values()):
            errors.append(f'{turn_id}: highlight state must be an array of IDs')
        highlight_elements = None
        if layout == 'settings':
            base = {key: value for key, value in visual.items()
                    if key not in {'highlightRows', 'highlightFields'}}
            base_hash = digest(encoded(base))
        else:
            try:
                raw_path = visual.get('svg')
                if not isinstance(raw_path, str) or Path(raw_path).is_absolute():
                    raise ValueError('use an episode-local SVG path')
                file = (HERE / raw_path).resolve()
                if not file.is_relative_to(HERE) or not file.is_file():
                    raise ValueError('SVG is missing or escapes the episode folder')
                if file not in svg_cache:
                    svg_cache[file] = svg_base(file.read_bytes())
                base_hash, highlight_elements = svg_cache[file]
            except (ValueError, OSError, ET.ParseError) as error:
                errors.append(f'{turn_id}: {error}')
                continue
        heading = visual.get('heading')
        if not isinstance(heading, str) or not heading:
            errors.append(f'{turn_id}: stable visual.heading is required')
        if view_id not in views:
            views[view_id] = {
                'viewId': view_id, 'layout': layout, 'heading': heading,
                'baseSha256': base_hash, 'turnIds': [], 'highlightTransitions': [],
            }
        current = views[view_id]
        if current['layout'] != layout or current['heading'] != heading:
            errors.append(f'{turn_id}: {view_id} changes heading or layout')
        if current['baseSha256'] != base_hash:
            errors.append(f'{turn_id}: {view_id} changes content/geometry beyond highlights')
        current['turnIds'].append(turn_id)
        entry = {'turnId': turn_id, **state}
        if highlight_elements is not None:
            entry['svgHighlightElements'] = highlight_elements
        current['highlightTransitions'].append(entry)
        if view_id != last_view:
            if any(run['viewId'] == view_id for run in runs):
                warnings.append(f'{view_id} reappears after another view; review the return')
            runs.append({'viewId': view_id, 'fromTurn': turn_id, 'throughTurn': turn_id})
            last_view = view_id
        else:
            runs[-1]['throughTurn'] = turn_id
        # This episode's three quiz views are explicitly neutral before answers.
        if turn.get('chapterId') == 'check' and current['turnIds'] == [turn_id]:
            if any(state.values()) or highlight_elements:
                errors.append(f'{turn_id}: quiz question highlights a candidate before the answer')
    return {
        'status': 'failed' if errors else 'passed', 'errors': errors, 'warnings': warnings,
        'episode': 4, 'visualStyle': source.get('visualStyle'),
        'scriptSha256': digest(source_bytes), 'previousScriptSha256': digest(old_bytes),
        'diagramGeneratorSha256': digest(generator_bytes) if generator_bytes else None,
        'sharedSettingsShellImportAndCall': shared_shell_reference,
        'bodyTurnCount': len(body), 'viewCount': len(views),
        'rawReusableTurnCount': 85, 'intentionalRerecordIds': list(RERECORD.values()),
        'updatedSpeechIds': [RERECORD['lesson04-turn-071-mako']],
        'continuousViewRuns': runs, 'views': list(views.values()),
        'scope': 'Source structure and fixed SVG geometry; visual meaning and continuous video still require review.',
        'networkRequestsMade': False, 'filesWritten': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script', default='script.json', help='Path within episodes/04; default script.json.')
    args = parser.parse_args()
    try:
        file = (HERE / args.script).resolve()
        if not file.is_relative_to(HERE):
            raise ValueError('Script must stay in episodes/04')
        report = check(file)
    except (ValueError, OSError, KeyError, TypeError, ET.ParseError) as error:
        report = {'status': 'failed', 'errors': [str(error)], 'networkRequestsMade': False, 'filesWritten': False}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
