#!/usr/bin/env python3
"""Verify episode 08's fixed settings views without rendering or network access.

This checks the authored inputs, not the finished video's pixels or timing.
It uses only the standard library and episode-08 source/history files.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
STYLE = 'teaching-settings-v2'
HIGHLIGHT_KEYS = {'highlightFields', 'highlightRows'}
MOTION_KEY = re.compile(r'zoom|camera|motion|animation|pulse|pointer', re.I)
LOCAL_ID = re.compile(r'[-a-zA-Z0-9_]+\Z')
QUESTION_GROUPS = [(73, 74, 75), (76, 77, 78), (79, 80, 81)]


def fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()


def sha_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strip_visuals(document):
    result = deepcopy(document)
    result.pop('visualStyle', None)
    for turn in result.get('turns', []):
        turn.pop('visual', None)
    return result


def differences(left, right, path='$', limit=30):
    """Return useful paths, rather than dumping unchanged speech/style text."""
    if type(left) is not type(right):
        return [path]
    result = []
    if isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                result.append(f'{path}.{key}')
            else:
                result.extend(differences(left[key], right[key], f'{path}.{key}', limit))
            if len(result) >= limit:
                break
    elif isinstance(left, list):
        if len(left) != len(right):
            result.append(path + '.length')
        for index, (a, b) in enumerate(zip(left, right)):
            result.extend(differences(a, b, f'{path}[{index}]', limit))
            if len(result) >= limit:
                break
    elif left != right:
        result.append(path)
    return result[:limit]


def motion_paths(value, path='visual'):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if MOTION_KEY.search(key):
                found.append(f'{path}.{key}')
            found.extend(motion_paths(child, f'{path}.{key}'))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(motion_paths(child, f'{path}[{index}]'))
    return found


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def scalar(value):
    return isinstance(value, (str, bool)) or finite_number(value)


def canonical_xml(element):
    # Whitespace between XML elements is insignificant. SVG text and ordered
    # children, geometry, transforms, colors and route data remain significant.
    content = element.text if element.text and element.text.strip() else None
    tail = element.tail if element.tail and element.tail.strip() else None
    return [element.tag, sorted(element.attrib.items()), content,
            [canonical_xml(child) for child in element], tail]


def local_tag(element):
    return element.tag.rsplit('}', 1)[-1]


def verify_document(baseline, current, folder=HERE, svg_loader=None, expected_views=20):
    errors, view_records, turn_highlights, svg_records = [], {}, {}, {}

    def require(condition, message):
        if not condition:
            errors.append(message)
        return bool(condition)

    old_nonvisual, new_nonvisual = strip_visuals(baseline), strip_visuals(current)
    diff = differences(old_nonvisual, new_nonvisual)
    require(not diff, 'Non-visual inputs changed: ' + ', '.join(diff))
    require(current.get('visualStyle') == STYLE, 'Missing adopted visualStyle: ' + STYLE)
    turns = current.get('turns', [])
    require(isinstance(turns, list) and bool(turns), 'Missing authored turns')
    if not isinstance(turns, list):
        turns = []
    ids = [turn.get('id') for turn in turns]
    require(len(ids) == len(set(ids)), 'Turn IDs are not unique')
    old_opening = [turn.get('visual') for turn in baseline.get('turns', [])
                   if turn.get('chapterId') == 'opening']
    new_opening = [turn.get('visual') for turn in turns if turn.get('chapterId') == 'opening']
    require(old_opening == new_opening, 'Adopted opening visuals changed')
    body = [(index, turn) for index, turn in enumerate(turns, 1)
            if turn.get('chapterId') != 'opening']

    def check_settings(visual, turn_id):
        has_fields, has_table = 'fields' in visual, 'table' in visual
        require(has_fields != has_table, f'{turn_id}: settings needs exactly one of fields/table')
        rows, geometries, highlights = [], [], []
        if has_fields and not has_table:
            rows = visual['fields']
            if not require(isinstance(rows, list), f'{turn_id}: fields must be an array'):
                return [], []
            require(1 <= len(rows) <= 6, f'{turn_id}: fields must have 1–6 rows')
            top = 205 if visual.get('notice') else 151
            height = min(52, math.floor((475 - top) / max(len(rows), 1)))
            for index, row in enumerate(rows):
                require(isinstance(row, dict), f'{turn_id}: invalid field row')
                if not isinstance(row, dict):
                    continue
                require(scalar(row.get('label')) and scalar(row.get('value')),
                        f'{turn_id}: field label/value must be scalar')
                geometries.append({'id': row.get('id'), 'x': 214, 'y': top + index * height,
                                   'width': 972, 'height': height})
            highlights = visual.get('highlightFields', [])
            require(not visual.get('highlightRows'), f'{turn_id}: fields cannot highlight table rows')
        elif has_table and not has_fields:
            table = visual['table']
            if not require(isinstance(table, dict), f'{turn_id}: table must be an object'):
                return [], []
            columns, rows = table.get('columns', []), table.get('rows', [])
            if not require(isinstance(columns, list) and isinstance(rows, list),
                           f'{turn_id}: table columns/rows must be arrays'):
                return [], []
            require(2 <= len(columns) <= 5, f'{turn_id}: table must have 2–5 columns')
            require(1 <= len(rows) <= 6, f'{turn_id}: table must have 1–6 rows')
            fixed, remaining = 0, 0
            for column in columns:
                if not require(isinstance(column, dict), f'{turn_id}: invalid column'):
                    continue
                require(isinstance(column.get('label'), str), f'{turn_id}: missing column label')
                if 'width' in column:
                    width = column['width']
                    if require(finite_number(width) and width >= 100,
                               f'{turn_id}: column width must be finite and >=100'):
                        fixed += width
                else:
                    remaining += 1
            share = (972 - fixed) / remaining if remaining else 0
            require(share >= 100 if remaining else abs(fixed - 972) < .01,
                    f'{turn_id}: table columns do not fit the 972px content region')
            top = 199 if visual.get('notice') else 151
            height = min(56, math.floor((475 - top - 30) / max(len(rows), 1)))
            require(height >= 40, f'{turn_id}: table rows do not fit the content region')
            for index, row in enumerate(rows):
                if not require(isinstance(row, dict), f'{turn_id}: invalid table row'):
                    continue
                cells = row.get('cells', [])
                require(isinstance(cells, list) and len(cells) == len(columns)
                        and all(scalar(cell) for cell in cells),
                        f'{turn_id}: table cells must match the columns and be scalar')
                geometries.append({'id': row.get('id'), 'x': 214,
                                   'y': top + 30 + index * height,
                                   'width': 972, 'height': height})
            highlights = visual.get('highlightRows', [])
            require(not visual.get('highlightFields'), f'{turn_id}: table cannot highlight field rows')
        row_ids = [row.get('id') for row in rows if isinstance(row, dict)]
        require(len(row_ids) == len(set(row_ids)) and all(isinstance(v, str) and LOCAL_ID.fullmatch(v)
                                                        for v in row_ids),
                f'{turn_id}: row IDs must be unique simple local IDs')
        require(isinstance(highlights, list) and all(isinstance(v, str) and v in row_ids
                                                   for v in highlights)
                and len(highlights) == len(set(highlights)),
                f'{turn_id}: highlights must name existing rows exactly once')
        return geometries, highlights if isinstance(highlights, list) else []

    def check_svg(visual, turn_id):
        relative = visual.get('svg')
        if not require(isinstance(relative, str) and not Path(relative).is_absolute()
                       and '..' not in Path(relative).parts,
                       f'{turn_id}: SVG must use an episode-local relative path'):
            return None
        resolved = (folder / relative).resolve()
        if not require(resolved.is_relative_to(folder.resolve() / 'diagrams'),
                       f'{turn_id}: SVG path escapes episode-08 diagrams'):
            return None
        if relative in svg_records:
            return svg_records[relative]
        try:
            raw = svg_loader(relative) if svg_loader else resolved.read_bytes()
            text = raw.decode('utf-8')
            if not require(not re.search(r'<!DOCTYPE|<!ENTITY', text, re.I),
                           f'{relative}: XML declarations/entities are forbidden'):
                return None
            root = ET.fromstring(text)
        except (OSError, UnicodeError, ET.ParseError) as error:
            require(False, f'{relative}: cannot read SVG: {type(error).__name__}: {error}')
            return None
        require(local_tag(root) == 'svg' and root.get('viewBox') == '0 0 1207 511',
                f'{relative}: expected the fixed 1207×511 SVG viewport')
        forbidden = {'script', 'foreignObject', 'animate', 'animateMotion', 'animateTransform', 'set'}
        for element in root.iter():
            tag = local_tag(element)
            require(tag not in forbidden, f'{relative}: forbidden SVG tag {tag}')
            for name, value in element.attrib.items():
                attr = name.rsplit('}', 1)[-1]
                require(not attr.lower().startswith('on'), f'{relative}: event handler {attr}')
                if attr in {'href', 'src'}:
                    require(value.startswith(('#', 'data:')), f'{relative}: external resource reference')
                for reference in re.findall(r'url\(([^)]+)\)', value, re.I):
                    require(reference.strip(' \"\'').startswith('#'), f'{relative}: external URL reference')
                if attr == 'style':
                    require(not re.search(r'animation|transition|@import|@keyframes', value, re.I),
                            f'{relative}: animated or externally imported CSS')
            if tag == 'style':
                require(not re.search(r'animation|transition|@import|@keyframes', element.text or '', re.I),
                        f'{relative}: animated or externally imported CSS')
        groups = [element for element in root.iter()
                  if element.get('id') == 'explanation-highlights']
        require(len(groups) == 1, f'{relative}: expected one explicit explanation-highlights group')
        group = groups[0] if len(groups) == 1 else None
        highlight_count = 0
        if group is not None:
            require(local_tag(group) == 'g' and group.attrib == {'id': 'explanation-highlights'},
                    f'{relative}: highlight group cannot transform or restyle the view')
            require(not (group.text or '').strip(), f'{relative}: highlight group cannot contain text')
            for rectangle in group:
                highlight_count += 1
                require(local_tag(rectangle) == 'rect' and not list(rectangle),
                        f'{relative}: only independent rectangles may be excluded as highlights')
                allowed = {'x', 'y', 'width', 'height', 'rx', 'fill', 'stroke', 'stroke-width'}
                require(set(rectangle.attrib) <= allowed, f'{relative}: unexpected highlight attributes')
                require(rectangle.get('fill') == 'none' and rectangle.get('stroke') == '#de343b'
                        and rectangle.get('stroke-width') == '2.8' and rectangle.get('rx') == '5',
                        f'{relative}: excluded highlight must be the adopted red outline')
                try:
                    x, y, w, h = [float(rectangle.attrib[key]) for key in ('x', 'y', 'width', 'height')]
                    require(all(math.isfinite(v) for v in (x, y, w, h)) and w > 0 and h > 0
                            and x >= 0 and y >= 0 and x + w <= 1207 and y + h <= 511,
                            f'{relative}: invalid highlight rectangle geometry')
                except (KeyError, ValueError):
                    require(False, f'{relative}: nonnumeric highlight rectangle geometry')
            group_elements = set(group.iter())
            for element in root.iter():
                require(element in group_elements or element.get('stroke', '').lower() != '#de343b',
                        f'{relative}: red outline outside the declared highlight group')
        texts = [''.join(element.itertext()) for element in root.iter() if local_tag(element) == 'text']
        for label in [visual.get('areaLabel'), visual.get('title'),
                      f'{visual.get("kind")} / {visual.get("section")}',
                      visual.get('subtitle'), visual.get('footer'), *(visual.get('menu') or [])]:
            if label:
                require(label in texts, f'{relative}: settings shell text does not match metadata: {label}')
        # Check the recognizable shared shell geometry without importing or
        # executing the shared renderer or another episode's generator.
        rectangles = [element for element in root.iter() if local_tag(element) == 'rect']
        for x, y, width, height in [(0, 0, 1207, 511), (0, 0, 1207, 39),
                                     (0, 136, 192, 346), (0, 483, 1207, 28)]:
            expected = dict(zip(('x', 'y', 'width', 'height'), map(str, (x, y, width, height))))
            require(any(all(element.get(k) == v for k, v in expected.items()) for element in rectangles),
                    f'{relative}: missing fixed settings-shell rectangle {expected}')
        clean_root = deepcopy(root)
        for parent in clean_root.iter():
            for child in list(parent):
                if child.get('id') == 'explanation-highlights':
                    parent.remove(child)
        record = {'file': relative, 'sha256': hashlib.sha256(raw).hexdigest(),
                  'baseStructureSha256': fingerprint(canonical_xml(clean_root)),
                  'highlightRectangleCount': highlight_count,
                  'elementCount': sum(1 for _ in root.iter()),
                  'textCount': len(texts),
                  'pathCount': sum(local_tag(element) == 'path' for element in root.iter())}
        svg_records[relative] = record
        return record

    previous_view, closed, order = None, set(), []
    layouts = Counter()
    for index, turn in body:
        turn_id, visual = turn.get('id'), turn.get('visual')
        if not require(isinstance(visual, dict), f'{turn_id}: missing body visual'):
            continue
        view_id, layout = visual.get('viewId'), visual.get('layout')
        require(isinstance(view_id, str) and bool(LOCAL_ID.fullmatch(view_id)),
                f'{turn_id}: body needs a stable simple viewId')
        require(layout in {'settings', 'svg'}, f'{turn_id}: body must use settings or settings-shell SVG')
        layouts[layout] += 1
        require(not motion_paths(visual), f'{turn_id}: body motion controls found: {motion_paths(visual)}')
        menu = visual.get('menu', [])
        require(isinstance(menu, list) and all(isinstance(v, str) for v in menu)
                and len(menu) == len(set(menu)) and len(set(menu) | {visual.get('section')}) <= 8,
                f'{turn_id}: settings menu must contain at most eight unique labels')
        if view_id != previous_view:
            if previous_view is not None:
                closed.add(previous_view)
            require(view_id not in closed, f'{turn_id}: viewId reappears after another view: {view_id}')
            order.append(view_id)
        previous_view = view_id
        geometries, selected, svg = [], [], None
        if layout == 'settings':
            geometries, selected = check_settings(visual, turn_id)
        elif layout == 'svg':
            require(not visual.get('highlightFields') and not visual.get('highlightRows'),
                    f'{turn_id}: SVG emphasis must be in its declared XML group')
            svg = check_svg(visual, turn_id)
            selected = [f'SVG-rectangle-{i+1}' for i in range(svg['highlightRectangleCount'])] if svg else []
        turn_highlights[turn_id] = selected
        structural = {key: value for key, value in visual.items()
                      if key not in HIGHLIGHT_KEYS | {'svg'}}
        signature = fingerprint(structural)
        view = view_records.setdefault(view_id, {'viewId': view_id, 'layout': layout,
                    'firstTurn': turn_id, 'lastTurn': turn_id, 'turnIds': [],
                    'structureSha256': signature, 'rowGeometry': geometries,
                    'svgBaseStructureSha256': svg['baseStructureSha256'] if svg else None})
        require(view['layout'] == layout, f'{turn_id}: layout changed inside viewId {view_id}')
        require(view['structureSha256'] == signature,
                f'{turn_id}: menu/text/order/structure changed inside viewId {view_id}')
        require(view['rowGeometry'] == geometries,
                f'{turn_id}: settings row positions changed inside viewId {view_id}')
        if svg:
            require(view['svgBaseStructureSha256'] == svg['baseStructureSha256'],
                    f'{turn_id}: SVG geometry/text/routes changed beyond red outlines in {view_id}')
        view['lastTurn'] = turn_id
        view['turnIds'].append(turn_id)
    require(len(view_records) == expected_views,
            f'Expected {expected_views} body views; found {len(view_records)}')

    by_number = {}
    for turn in turns:
        match = re.fullmatch(r'lesson08-turn-(\d{3})-(?:mako|manabu|ikumi)', turn.get('id', ''))
        if match:
            by_number[int(match[1])] = turn
    questions = []
    for group in QUESTION_GROUPS:
        members = [by_number.get(number) for number in group]
        if not require(all(members), f'Question group {group}: missing turn'):
            continue
        question, *answers = members
        view_ids = [turn.get('visual', {}).get('viewId') for turn in members]
        require(len(set(view_ids)) == 1, f'Question group {group}: question/answer viewId changed')
        question_selected = turn_highlights.get(question['id'], [])
        require(not question_selected, f'{question["id"]}: answer is highlighted during the question')
        require(any(turn_highlights.get(turn['id']) for turn in answers),
                f'Question group {group}: answer never reveals its explained target')
        questions.append({'questionTurn': question['id'], 'throughTurn': members[-1]['id'],
                          'viewId': view_ids[0], 'questionHighlightCount': len(question_selected),
                          'answerHighlightCounts': [len(turn_highlights.get(turn['id'], []))
                                                    for turn in answers]})

    return {'status': 'passed' if not errors else 'failed', 'visualStyle': current.get('visualStyle'),
            'nonVisualInputsIdentical': not diff, 'nonVisualDifferencePaths': diff,
            'baselineNonVisualSha256': fingerprint(old_nonvisual),
            'currentNonVisualSha256': fingerprint(new_nonvisual),
            'openingVisualsUnchanged': old_opening == new_opening,
            'sourceTurnCount': len(turns), 'bodyTurnCount': len(body),
            'bodyLayouts': dict(layouts), 'viewCount': len(view_records),
            'contiguousViewOrder': order, 'views': list(view_records.values()),
            'svgFileCount': len(svg_records), 'svgFiles': list(svg_records.values()),
            'confirmationQuestions': questions, 'errors': errors,
            'limitations': ['Source-level QA only: visual fit and actual rendered video pixels require separate checks.',
                            'Equal settings inputs determine fixed row geometry; this does not execute the shared renderer.',
                            'SVG comparison excludes only the explicitly validated red rectangle group; all other XML is compared.',
                            'Continuous playback, subtitle-boundary frame stability and listening are not asserted here.'],
            'networkRequestsMade': False, 'dependenciesInstalled': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-views', type=int, default=20)
    args = parser.parse_args()
    baseline_file = HERE / 'revisions/diagram-v1/script.json'
    current_file = HERE / 'script.json'
    try:
        baseline = json.loads(baseline_file.read_text())
        current = json.loads(current_file.read_text())
        report = verify_document(baseline, current, expected_views=args.expected_views)
    except (OSError, ValueError, KeyError, TypeError) as error:
        report = {'status': 'failed', 'errors': [f'{type(error).__name__}: {error}'],
                  'networkRequestsMade': False, 'dependenciesInstalled': False}
    report['checkedAt'] = datetime.now(timezone(timedelta(hours=9))).isoformat()
    report['baseline'] = 'revisions/diagram-v1/script.json'
    report['script'] = 'script.json'
    if baseline_file.is_file():
        report['baselineFileSha256'] = sha_file(baseline_file)
    if current_file.is_file():
        report['scriptFileSha256'] = sha_file(current_file)
    destination = HERE / 'settings-verification.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ['status', 'sourceTurnCount', 'bodyTurnCount',
                      'viewCount', 'svgFileCount', 'nonVisualInputsIdentical', 'errors'] if key in report},
                     ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
