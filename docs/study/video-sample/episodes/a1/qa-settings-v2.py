#!/usr/bin/env python3
"""Read-only A1 revision audit; pixel and continuous-video review remain separate."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
BASELINE = HERE / 'revisions/initial-v1/script.json'
PROTECTED = ('schemaVersion', 'productionProfile', 'episodeKey', 'episodeNumber',
             'episodeId', 'title', 'topic', 'cast', 'speed', 'targetDurationSeconds',
             'durationPolicy', 'logoIntroSeconds', 'logoClosingSeconds', 'speechSettings',
             'scope', 'learningObjectives', 'sourceReferences', 'chapters', 'firstCut')
QA_GROUPS = ((6, 7), (9, 10), (14, 16), (18, 19), (21, 22), (24, 26),
             (27, 29), (30, 32), (33, 34), (35, 37), (39, 40), (42, 43),
             (44, 46), (48, 49), (50, 51), (52, 53), (54, 56), (57, 58),
             (59, 61), (62, 64), (66, 67), (70, 72), (73, 75), (76, 78), (81, 82))
QUIZ_QUESTIONS = (70, 73, 76)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def main():
    raw = (HERE / 'script.json').read_bytes()
    source = json.loads(raw)
    baseline = json.loads(BASELINE.read_bytes())
    issues, warnings = [], []
    if source.get('visualStyle') != 'teaching-settings-v2':
        issues.append('Top-level visualStyle is not teaching-settings-v2')
    for key in PROTECTED:
        if source.get(key) != baseline.get(key):
            issues.append(f'Protected top-level source changed: {key}')
    old_turns = {t['id']: t for t in baseline['turns']}
    if [t['id'] for t in source['turns']] != [t['id'] for t in baseline['turns']]:
        issues.append('Turn IDs or order changed')
    views = defaultdict(list)
    structural = defaultdict(set)
    svg_variants = defaultdict(set)
    numbered = {}
    for number, turn in enumerate(source['turns'], 1):
        numbered[number] = turn
        old = old_turns.get(turn['id'])
        if old is None:
            continue
        if {k: v for k, v in turn.items() if k != 'visual'} != {k: v for k, v in old.items() if k != 'visual'}:
            issues.append(f'{turn["id"]}: non-visual turn data changed')
        if turn['chapterId'] == 'opening':
            if turn['visual'] != old['visual']:
                issues.append(f'{turn["id"]}: opening visual changed')
            continue
        visual = turn['visual']
        view_id = visual.get('viewId')
        if not view_id or not re.fullmatch(r'[-a-zA-Z0-9_]+', view_id):
            issues.append(f'{turn["id"]}: missing or invalid stable viewId')
            continue
        views[view_id].append(turn['id'])
        for key in ('zoom', 'camera', 'motion', 'azureMotion', 'pulse', 'pointer'):
            if visual.get(key):
                issues.append(f'{turn["id"]}: forbidden automatic body effect {key}')
        layout = visual.get('layout')
        if layout == 'settings':
            if bool(visual.get('table')) == bool(visual.get('fields')):
                issues.append(f'{turn["id"]}: settings needs exactly one table or fields')
            base = {k: v for k, v in visual.items() if k not in ('highlightRows', 'highlightFields')}
            structural[view_id].add(json.dumps(base, ensure_ascii=False, sort_keys=True))
        elif layout == 'svg':
            target = (HERE / visual.get('svg', '')).resolve()
            if not target.is_relative_to(HERE) or not target.is_file():
                issues.append(f'{turn["id"]}: missing or unsafe SVG path')
                continue
            text = target.read_text()
            if re.search(r'<(?:script|foreignObject|animate|animateTransform|set)\b|<!DOCTYPE|<!ENTITY|\bon[a-z]+\s*=|(?:href|src)\s*=\s*["\'](?!#|data:)', text, re.I):
                issues.append(f'{turn["id"]}: external or animated SVG content')
            root = ET.fromstring(text)
            if root.attrib.get('viewBox') != '0 0 1207 511':
                issues.append(f'{turn["id"]}: unexpected SVG viewBox')
            targets = []
            for parent in root.iter():
                for child in list(parent):
                    if 'highlight' in child.attrib.get('class', '').split():
                        targets.append(child.attrib.get('data-target'))
                        parent.remove(child)
            if sorted(targets) != sorted(visual.get('highlightTargets', [])):
                issues.append(f'{turn["id"]}: SVG red frames differ from declared targets')
            base = {k: v for k, v in visual.items() if k not in ('svg', 'highlightTargets')}
            structural[view_id].add(json.dumps(dict(visual=base, svg=ET.tostring(root, encoding='unicode')), ensure_ascii=False, sort_keys=True))
            # Red outlines are the only removed elements. All other SVG content,
            # geometry, labels, shell and scale must remain identical per view.
            svg_variants[view_id].add(str(target.relative_to(HERE)))
        else:
            warnings.append(f'{turn["id"]}: supplementary body layout {layout}; check settings context manually')
        if number in QUIZ_QUESTIONS and (visual.get('highlightRows') or visual.get('highlightFields')):
            issues.append(f'{turn["id"]}: quiz question highlights an answer before the response')
    for view_id, variants in structural.items():
        if len(variants) != 1:
            issues.append(f'{view_id}: settings base content/composition changes within the same viewId')
    for begin, end in QA_GROUPS:
        group = [numbered[i]['visual'].get('viewId') for i in range(begin, end + 1)]
        if len(set(group)) != 1:
            issues.append(f'Question/answer {begin:03}–{end:03} changes viewId: {group}')
    report = dict(schemaVersion=1, visualStyle=source.get('visualStyle'),
                  scriptSha256=digest(raw), baselineSha256=digest(BASELINE.read_bytes()),
                  turns=len(source['turns']), stableBodyViewCount=len(views),
                  protectedDataUnchanged=not any('changed' in i for i in issues),
                  viewGroups=dict(views), svgVariants={k: sorted(v) for k, v in svg_variants.items()},
                  questionAnswerGroups=[list(v) for v in QA_GROUPS],
                  issues=issues, warnings=warnings,
                  limitations=['SVG variants, red-frame timing, and diagram references require visual inspection.',
                               'This audit does not replace uninterrupted video or audio listening.'])
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
