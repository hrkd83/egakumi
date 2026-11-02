#!/usr/bin/env python3
"""Verify episode 07's settings-v2 migration without rendering or API access.

Reads the frozen diagram-v1 source, current script, and episode-local SVGs.
Writes only settings-v2-verification.json beside this script. This is source /
layout verification; full MP4 decoding and continuous viewing are separate.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

EP = Path(__file__).resolve().parent
SOURCE = EP / 'script.json'
HISTORY = EP / 'history' / 'diagram-v1' / 'script.json'
REPORT = EP / 'settings-v2-verification.json'
SHELL_KEYS = ('viewId', 'areaLabel', 'heading', 'title', 'kind', 'section',
              'menu', 'subtitle', 'footer', 'notice')
CAMERA_KEYS = {'zoom', 'camera', 'motion', 'azureMotion', 'automaticZoom',
               'automaticPan', 'redOutlinePulse', 'mockPointer', 'pulse'}
GEOMETRY_KEYS = {'x', 'y', 'width', 'height', 'rx', 'ry', 'cx', 'cy', 'r',
                 'x1', 'y1', 'x2', 'y2', 'd', 'points', 'transform', 'viewBox',
                 'font-size', 'font-family', 'text-anchor', 'marker-start',
                 'marker-mid', 'marker-end', 'markerWidth', 'markerHeight',
                 'refX', 'refY', 'orient'}
ANIMATION_TAGS = {'animate', 'animateMotion', 'animateTransform', 'set', 'mpath'}
RED = '#de343b'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tag(element):
    return element.tag.rsplit('}', 1)[-1]


def plain_turn(turn):
    return {key: value for key, value in turn.items() if key != 'visual'}


def local_path(raw):
    path = (EP / raw).resolve()
    if Path(raw).is_absolute() or not path.is_relative_to(EP.resolve()):
        raise ValueError('SVG path must stay inside episode 07: ' + str(raw))
    return path


def forbidden_motion_keys(value):
    if isinstance(value, dict):
        return sorted((set(value) & CAMERA_KEYS) | {
            found for nested in value.values() for found in forbidden_motion_keys(nested)})
    if isinstance(value, list):
        return sorted({found for item in value for found in forbidden_motion_keys(item)})
    return []


def structure(view):
    table = view.get('table')
    fields = view.get('fields')
    return {
        'shell': {key: view.get(key) for key in SHELL_KEYS},
        'columns': table.get('columns') if table else None,
        'rowIds': [row['id'] for row in (table['rows'] if table else fields or [])],
        'rowLabels': [row['cells'][0] for row in table['rows']] if table else
                     [row['label'] for row in fields or []],
        'graph': view.get('graph'),
    }


def geometry_signature(root):
    """Keep layout, text positions, paths and graph labels; ignore highlight paint.

    Selected row outlines are additive paint with no layout meaning. Graph
    node outlines change paint in-place and remain in the geometry signature.
    """
    entries = []
    for element in root.iter():
        name = tag(element)
        if (name == 'rect' and element.get('stroke', '').lower() == RED
                and element.get('fill', '').lower() == 'none'):
            continue
        entries.append({
            'tag': name,
            'geometry': {key: value for key, value in element.attrib.items()
                         if key in GEOMETRY_KEYS},
            'text': ''.join(element.itertext()) if name == 'text' and
                    float(element.get('y', '0')) < 274 else None,
        })
    return sha(json.dumps(entries, ensure_ascii=False, sort_keys=True).encode())


def main():
    checks = []
    errors = []

    def check(condition, label, detail=None):
        checks.append(label)
        if not condition:
            errors.append({'check': label, 'detail': detail})
        return condition

    report = {
        'checkedAt': datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(),
        'kind': 'episode-07-settings-v2-source-and-layout-verification',
        'networkRequestsMade': False,
        'authenticationRead': False,
        'sourceMutated': False,
        'scope': 'Source/layout invariants and SVG safety. Full AV decode, '
                 'continuous-preview viewing and audio listening are separate.',
    }
    try:
        current_bytes = SOURCE.read_bytes()
        old_bytes = HISTORY.read_bytes()
        source, old = json.loads(current_bytes), json.loads(old_bytes)
        report.update(sourceScriptSha256=sha(current_bytes),
                      diagramV1ScriptSha256=sha(old_bytes))
        check(source.get('visualStyle') == 'teaching-settings-v2', 'adopted visual style')
        check(source.get('visualCameraPolicy') == {
            'body': 'fixed', 'automaticZoom': False, 'automaticPan': False,
            'redOutlinePulse': False, 'holdQuestionAndAnswerView': True,
        }, 'fixed body camera policy')
        check(len(source['turns']) == len(old['turns']) == 91,
              'all 91 source turns retained')
        check([plain_turn(t) for t in source['turns']] ==
              [plain_turn(t) for t in old['turns']],
              'all non-visual turn data identical to frozen v1')
        unchanged_top_keys = ['chapters', 'firstCut', 'cast', 'speed', 'speechSettings',
                              'logoIntroSeconds', 'logoClosingSeconds', 'episodeKey',
                              'episodeNumber', 'episodeId', 'sourceReferences']
        check(all(source.get(key) == old.get(key) for key in unchanged_top_keys),
              'chapters, cast, speech, editorial cut and episode identity unchanged')
        omitted = set(source['firstCut']['omitTurnIds'])
        active = [turn for turn in source['turns'] if turn['id'] not in omitted]
        body = [turn for turn in active if turn['chapterId'] != 'opening']
        opening = [turn for turn in active if turn['chapterId'] == 'opening']
        check(len(active) == 89 and len(body) == 83 and len(opening) == 6,
              'active selection is 89 turns / 83 body / 6 opening')
        check(all(turn['visual'] == next(oldturn['visual'] for oldturn in old['turns']
                                        if oldturn['id'] == turn['id']) for turn in opening),
              'opening hologram data unchanged')

        grouped = defaultdict(list)
        svg_refs = defaultdict(list)
        diagram_data = json.loads((EP / 'settings-diagrams.json').read_text())
        for turn in body:
            visual = turn['visual']
            settings = visual.get('settingsView', visual)
            identifier = turn['id']
            check(visual.get('layout') in {'settings', 'svg'}, identifier + ': settings body layout')
            check(settings.get('layout') == 'settings', identifier + ': settings metadata')
            view_id = settings.get('viewId')
            check(isinstance(view_id, str) and bool(re.fullmatch(r'[-a-zA-Z0-9_]+', view_id)),
                  identifier + ': simple stable viewId')
            check(not forbidden_motion_keys(visual), identifier + ': no body motion instructions',
                  forbidden_motion_keys(visual))
            check(bool(settings.get('table')) != bool(settings.get('fields')),
                  identifier + ': exactly one settings table or fields')
            rows = settings.get('table', {}).get('rows') or settings.get('fields', [])
            ids = [row['id'] for row in rows]
            check(1 <= len(ids) <= 6 and len(ids) == len(set(ids)),
                  identifier + ': valid unique row IDs')
            highlights = settings.get('highlightRows', []) + settings.get('highlightFields', [])
            check(len(highlights) == len(set(highlights)) and set(highlights) <= set(ids),
                  identifier + ': highlights reference existing rows')
            menu = settings.get('menu', [])
            check(1 <= len(menu) <= 8 and len(menu) == len(set(menu))
                  and settings.get('section') in menu, identifier + ': stable valid menu')
            if settings.get('table'):
                columns = settings['table']['columns']
                check(2 <= len(columns) <= 5 and all('width' in col for col in columns)
                      and abs(sum(col['width'] for col in columns) - 972) < .01,
                      identifier + ': fixed column widths sum to 972')
                check(all(len(row['cells']) == len(columns) for row in rows),
                      identifier + ': table shape matches columns')
            grouped[view_id].append((turn, settings))
            if visual.get('layout') == 'svg':
                check(all(visual.get(key) == settings.get(key) for key in SHELL_KEYS),
                      identifier + ': SVG shell matches settings metadata')
                svg_path = local_path(visual['svg'])
                check(svg_path.is_file(), identifier + ': referenced SVG exists')
                check(diagram_data.get(svg_path.name) == settings,
                      identifier + ': SVG source recipe matches turn settings')
                svg_refs[svg_path].append((identifier, view_id))

        check(len(grouped) == 19, 'exactly 19 stable body viewIds', len(grouped))
        view_reports = []
        for view_id, members in grouped.items():
            expected = structure(members[0][1])
            check(all(structure(settings) == expected for _, settings in members),
                  view_id + ': same menu/title/heading/section/columns/row order/graph')
            numbers = [next(i for i, turn in enumerate(body) if turn['id'] == member['id'])
                       for member, _ in members]
            check(numbers == list(range(min(numbers), max(numbers) + 1)),
                  view_id + ': one contiguous conversation block')
            view_reports.append({'viewId': view_id, 'fromTurn': members[0][0]['id'],
                                 'throughTurn': members[-1][0]['id'],
                                 'turnCount': len(members), **expected})

        quiz_reports = []
        by_id = {turn['id']: turn for turn in active}
        quizzes = [
            ('lesson07-turn-077-mako', 'lesson07-turn-078-ikumi', '直接経路'),
            ('lesson07-turn-080r2-mako', 'lesson07-turn-081-ikumi', 'FWでの許可'),
            ('lesson07-turn-083r2-mako', 'lesson07-turn-084-ikumi', 'Deny全件通知はない'),
        ]
        for question_id, answer_id, answer_phrase in quizzes:
            question, answer = by_id[question_id]['visual'], by_id[answer_id]['visual']
            qrows = {row['id']: row for row in question['table']['rows']}
            arows = {row['id']: row for row in answer['table']['rows']}
            check(question.get('layout') == 'settings' and not question.get('svg')
                  and not question.get('graph'), question_id + ': no answer diagram in question')
            check(not question.get('highlightRows') and not question.get('highlightFields')
                  and not question.get('graphFocus'), question_id + ': no answer highlight')
            check(all(qrows[key]['cells'][1] == '回答で確認する' for key in ('judgment', 'next')),
                  question_id + ': answer values withheld until reply')
            check(answer_phrase not in json.dumps(question, ensure_ascii=False),
                  question_id + ': correct answer phrase not displayed')
            check(structure(question) == structure(answer),
                  question_id + ': reply preserves view and row geometry')
            check(all(qrows[key] == arows[key] for key in ('condition', 'question')),
                  question_id + ': question conditions held in reply')
            check(all(arows[key]['cells'][1] not in {'', '回答で確認する'}
                      for key in ('judgment', 'next'))
                  and {'judgment', 'next'} <= set(answer.get('highlightRows', [])),
                  answer_id + ': reply reveals and highlights the same answer rows')
            check(answer_phrase in arows['judgment']['cells'][1],
                  answer_id + ': expected explanation in judgment row')
            quiz_reports.append({'questionTurn': question_id, 'answerTurn': answer_id,
                                 'viewId': question['viewId'],
                                 'heldRowIds': list(qrows), 'revealedRows': ['judgment', 'next']})

        svg_files = sorted((EP / 'diagrams').rglob('*.svg'))
        check(len(svg_refs) == len(diagram_data) == 31,
              'all 31 active SVG variants are referenced and recipe-bound',
              {'allFiles': len(svg_files), 'referenced': len(svg_refs), 'recipes': len(diagram_data)})
        check(set(svg_refs) <= set(svg_files), 'all active SVG variants exist')
        check({path.name for path in svg_refs} == set(diagram_data),
              'all current SVG recipes are used by active source')
        svg_reports = []
        geometries = defaultdict(set)
        for path in svg_files:
            raw = path.read_bytes()
            data = raw.decode('utf-8')
            check(not re.search(r'<!DOCTYPE|<!ENTITY|@import|@keyframes|\banimation\s*:|\btransition\s*:', data, re.I),
                  path.name + ': no external entities/imports or CSS animation')
            root = ET.fromstring(raw)
            check(tag(root) == 'svg' and root.get('viewBox', '').split() == ['0', '0', '1207', '511'],
                  path.name + ': fixed 1207x511 viewBox')
            declared_ids = {element.get('id') for element in root.iter() if element.get('id')}
            unsafe = []
            for element in root.iter():
                name = tag(element)
                if name in {'script', 'foreignObject', 'iframe', 'object'} | ANIMATION_TAGS:
                    unsafe.append(name)
                for key, value in element.attrib.items():
                    local_key = key.rsplit('}', 1)[-1]
                    if local_key.lower().startswith('on'):
                        unsafe.append(local_key)
                    if local_key in {'href', 'src'} and not value.startswith(('#', 'data:')):
                        unsafe.append(local_key + '=' + value)
                    for url in re.findall(r'url\(\s*[\"\x27]?([^\)\"\x27]+)[\"\x27]?\s*\)', value, re.I):
                        if not url.startswith('#') or url[1:] not in declared_ids:
                            unsafe.append('url=' + url)
                if name == 'style' and re.search(r'url\(|@import|@keyframes|animation|transition', element.text or '', re.I):
                    unsafe.append('unsafe style')
            check(not unsafe, path.name + ': self-contained script-free non-animated SVG', unsafe)
            geometry = geometry_signature(root)
            view_ids = {view_id for _, view_id in svg_refs.get(path, [])}
            check(len(view_ids) == 1 if path in svg_refs else not view_ids,
                  path.name + ': active artifact has one conversation view')
            for view_id in view_ids:
                geometries[view_id].add(geometry)
            svg_reports.append({'file': str(path.relative_to(EP)), 'sha256': sha(raw),
                                'viewId': next(iter(view_ids)) if view_ids else None,
                                'referencedByCurrentScript': path in svg_refs,
                                'geometrySha256': geometry})
        for view_id, signatures in geometries.items():
            check(len(signatures) == 1,
                  view_id + ': SVG graph, text positions and table geometry held across variants',
                  sorted(signatures))
        check(SOURCE.read_bytes() == current_bytes, 'source remained unchanged during verification')
        report.update(sourceTurns=len(source['turns']), activeTurns=len(active),
                      bodyTurns=len(body), openingTurns=len(opening),
                      stableViewCount=len(grouped), stableViews=view_reports,
                      confirmationQuestions=quiz_reports, svgVariantCount=len(svg_refs),
                      allSvgFilesChecked=len(svg_files),
                      retainedUnreferencedSvgCount=len(set(svg_files) - set(svg_refs)),
                      svgGraphViewCount=len(geometries), svgVariants=svg_reports,
                      unchangedNonVisualTurns=len(source['turns']))
    except (ValueError, KeyError, TypeError, FileNotFoundError, ET.ParseError) as error:
        errors.append({'check': 'verification completed without unexpected source errors',
                       'detail': str(error)})
    report.update(status='failed' if errors else 'passed', checkCount=len(checks),
                  errors=errors, warnings=[])
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'checks': len(checks),
                      'stableViewCount': report.get('stableViewCount'),
                      'svgVariantCount': report.get('svgVariantCount'),
                      'errors': errors, 'report': str(REPORT)}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
