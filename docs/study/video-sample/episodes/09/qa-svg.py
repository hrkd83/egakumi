#!/usr/bin/env python3
"""Compare all episode09 SVG variants after removing explicit highlight boxes."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

here = Path(__file__).resolve().parent
script = json.loads((here / 'script.json').read_text())
groups = {}
errors = []
for turn in script['turns']:
    v = turn['visual']
    if v.get('layout') != 'svg':
        continue
    group = groups.setdefault(v['viewId'], {})
    if v['svg'] in group:
        continue
    path = here / v['svg']
    root = ET.fromstring(path.read_text())
    assert root.attrib.get('viewBox') == '0 0 1207 511'
    highlights = []
    for parent in root.iter():
        for node in list(parent):
            if node.attrib.get('data-highlight') == 'true':
                highlights.append(dict(node.attrib))
                if not node.tag.endswith('rect') or node.attrib.get('fill') != 'none' or node.attrib.get('stroke') != '#de343b':
                    errors.append(f'{v["svg"]}: variant difference is not a standard outline')
                parent.remove(node)
    canonical = ET.canonicalize(ET.tostring(root, encoding='unicode'))
    group[v['svg']] = {'geometrySha256': hashlib.sha256(canonical.encode()).hexdigest(),
                       'sourceSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                       'highlights': highlights}
for view, variants in groups.items():
    if len({v['geometrySha256'] for v in variants.values()}) != 1:
        errors.append(f'{view}: content or geometry differs after removing highlights')
report = {'status': 'passed' if not errors else 'failed',
          'scope': 'Exact XML content/geometry comparison; explicit red outline variants only',
          'scriptSha256': hashlib.sha256((here / 'script.json').read_bytes()).hexdigest(),
          'groups': groups, 'errors': errors}
(here / 'qa-svg.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'status': report['status'], 'groups': len(groups),
                  'variants': sum(map(len, groups.values())), 'errors': errors}))
raise SystemExit(bool(errors))
