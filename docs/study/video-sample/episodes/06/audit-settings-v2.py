#!/usr/bin/env python3
"""Audit episode 06's display-only revision and fixed conversation views."""
import hashlib
import json
from collections import defaultdict
from pathlib import Path
import re
import xml.etree.ElementTree as ET

EP = Path(__file__).resolve().parent
BASE = EP.parents[1]
OUT = BASE / 'output' / 'lesson-06-gemini'
OLD = EP / 'revisions' / 'diagram-v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def svg_structure(name):
    root = ET.fromstring((EP / name).read_text())
    require(root.get('viewBox') == '0 0 1207 511', 'SVG viewport must stay fixed: ' + name)
    for parent in root.iter():
        for child in list(parent):
            tag = child.tag.rsplit('}', 1)[-1]
            require(tag not in ('animate', 'animateTransform', 'animateMotion', 'script', 'foreignObject'),
                    'Motion or executable SVG is not allowed: ' + name)
            if child.get('stroke', '').lower() in ('#de343b', '#de343bff'):
                parent.remove(child)
    return ET.tostring(root, encoding='unicode')


def main():
    before, after = read(OLD / 'script.json'), read(EP / 'script.json')
    require(after.get('visualStyle') == 'teaching-settings-v2', 'Expected current visual style')
    require(len(before['turns']) == len(after['turns']) == 87, 'Expected all 87 recorded turns')
    for field in ('chapters', 'firstCut', 'speed', 'logoIntroSeconds', 'logoClosingSeconds'):
        require(before.get(field) == after.get(field), 'Audio plan changed: ' + field)
    groups, bases, positions = defaultdict(list), {}, defaultdict(list)
    for index, (old, new) in enumerate(zip(before['turns'], after['turns'])):
        require({k:v for k,v in old.items() if k != 'visual'} ==
                {k:v for k,v in new.items() if k != 'visual'},
                'Nonvisual turn data changed: ' + old['id'])
        if new['chapterId'] == 'opening':
            require(old['visual'] == new['visual'], 'Opening was changed')
            continue
        visual = new['visual']
        key = visual.get('viewId')
        require(isinstance(key, str) and re.fullmatch(r'[-a-zA-Z0-9_]+', key),
                'Every body turn needs a stable viewId: ' + new['id'])
        require(visual.get('layout') in ('settings', 'svg'),
                'Body must start from teaching settings: ' + new['id'])
        require(not any(visual.get(k) for k in ('zoom', 'camera', 'motion', 'azureMotion')),
                'Automatic body motion is forbidden')
        base = {k:v for k,v in visual.items() if k not in ('highlightRows', 'highlightFields', 'svg')}
        if visual['layout'] == 'svg':
            base['svgStructureWithoutRedOutline'] = svg_structure(visual['svg'])
        if key in bases:
            require(bases[key] == base, 'View geometry/content changed inside question and answer: ' + key)
        else:
            bases[key] = base
        groups[key].append(new['id'])
        positions[key].append(index)
    for key, indexes in positions.items():
        require(indexes == list(range(indexes[0], indexes[-1] + 1)),
                'A conversation view is left and revisited: ' + key)
    old_config, new_config = read(OLD / 'generation.json'), read(EP / 'generation.json')
    require(old_config['candidates'] == new_config['candidates'],
            'Text, voice, style or caption in synthesis requests changed')
    generation = read(OUT / 'generation.json')
    require(generation['generationState'] == 'complete' and generation['attemptedThisRun'] == 0,
            'Expected cache-only audio rebinding with zero API requests')
    require(all(r.get('reusedRaw') and r.get('status') == 'succeeded' for r in generation['results']),
            'All recorded voices must be verified cached originals')
    result = {'status':'passed', 'visualStyle':after['visualStyle'], 'bodyCamera':'fixed',
              'bodyViews':len(groups), 'sourceTurns':len(after['turns']),
              'speechCaptionDeliveryAndTimingUnchanged':True, 'all87SynthesisCandidatesIdentical':True,
              'additionalSynthesisRequests':0, 'all87RawRecordingsReused':True,
              'viewGeometryStableAcrossQuestionAndAnswer':True,
              'viewGroups':dict(groups),
              'scriptSha256':hashlib.sha256((EP/'script.json').read_bytes()).hexdigest()}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
