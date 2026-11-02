#!/usr/bin/env python3
"""Read-only production QA for A2 subtitles and diagram text geometry."""
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent


def units(value):
    return sum(.56 if ord(ch) < 128 else 1 for ch in str(value))


def port(a, b):
    cx, cy = a['x'] + a.get('width', 200) / 2, a['y'] + a.get('height', 100) / 2
    dx, dy = b['x'] + b.get('width', 200) / 2 - cx, b['y'] + b.get('height', 100) / 2 - cy
    scale = min(abs(a.get('width', 200) / 2 / dx) if dx else float('inf'),
                abs(a.get('height', 100) / 2 / dy) if dy else float('inf'))
    return cx + dx * scale, cy + dy * scale


def main():
    script = json.loads((HERE / 'script.json').read_text())
    issues = []
    warnings = []
    maximum = 0
    maximum_units = 0
    for turn in script['turns']:
        tid = turn['id']
        chunks = turn.get('subtitleChunks') or [dict(caption=turn['caption'], lines=turn['subtitleLines'])]
        if ''.join(chunk['caption'] for chunk in chunks) != turn['caption']:
            issues.append([tid, 'subtitle chunks differ from caption'])
        for chunk in chunks:
            lines = chunk['lines']
            if not 1 <= len(lines) <= 2:
                issues.append([tid, 'subtitle cue needs 1 or 2 lines'])
            if ''.join(lines) != chunk['caption']:
                issues.append([tid, 'subtitle lines differ from caption'])
            for index, line in enumerate(lines):
                maximum = max(maximum, len(line))
                maximum_units = max(maximum_units, units(line))
                if units(line) > 39:
                    warnings.append([tid, 'subtitle line wider than 39 fullwidth units', line])
                if index and re.match(r'^[、。！？）」』】・：；,.!?)]', line):
                    warnings.append([tid, 'subtitle line begins with punctuation', line])
            if len(lines) == 2 and re.search(r'[A-Za-z0-9]$', lines[0]) and re.match(r'^[A-Za-z0-9]', lines[1]):
                warnings.append([tid, 'possible Latin token split', lines])
        visual = turn.get('visual', {})
        if visual.get('layout') == 'diagram':
            nodes = visual['nodes']
            for node in nodes:
                w, h = node.get('width', 200), node.get('height', 100)
                if not (0 <= node['x'] <= 1207 - w and 0 <= node['y'] <= 511 - h):
                    issues.append([tid, 'diagram node outside viewport', node['id']])
                detail = node.get('detail', '')
                if units(detail) * 18 > w - 24:
                    warnings.append([tid, 'diagram detail may overflow', node['id'], detail])
            for i, left in enumerate(nodes):
                for right in nodes[i + 1:]:
                    if (max(left['x'], right['x']) < min(left['x'] + left.get('width', 200), right['x'] + right.get('width', 200))
                            and max(left['y'], right['y']) < min(left['y'] + left.get('height', 100), right['y'] + right.get('height', 100))):
                        issues.append([tid, 'diagram nodes overlap', left['id'], right['id']])
            by_id = {node['id']: node for node in nodes}
            for edge in visual.get('edges', []):
                if not edge.get('label'):
                    continue
                if edge['from'] not in by_id or edge['to'] not in by_id:
                    issues.append([tid, 'edge names unknown node', edge])
                    continue
                a, b = by_id[edge['from']], by_id[edge['to']]
                points = edge.get('points') or [port(a, b), port(b, a)]
                mid, before = points[len(points) // 2], points[(len(points) - 1) // 2]
                cx, baseline = (mid[0] + before[0]) / 2, (mid[1] + before[1]) / 2 - 12
                half_width = units(edge['label']) * 19 / 2
                for node in nodes:
                    if (max(cx - half_width, node['x']) < min(cx + half_width, node['x'] + node.get('width', 200))
                            and max(baseline - 19, node['y']) < min(baseline + 4, node['y'] + node.get('height', 100))):
                        warnings.append([tid, 'edge label may be hidden by node', edge['label'], node['id']])
        if visual.get('layout') == 'cards':
            cards = visual['cards']
            columns = len(cards) if len(cards) <= 2 else 3
            width = (1207 - 20 * (columns - 1)) / columns
            for card in cards:
                if isinstance(card.get('body'), list):
                    for line in card['body']:
                        if units(line) * 23 > width - 48:
                            warnings.append([tid, 'card text may overflow', card['title'], line])
    print(json.dumps(dict(turns=len(script['turns']), maxSubtitleLineCharacters=maximum,
                          maxSubtitleLineFullwidthUnits=round(maximum_units, 2),
                          issues=issues, warnings=warnings), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
