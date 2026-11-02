#!/usr/bin/env python3
"""Episode 09 independent, read-only source/continuity audit.

Writes only its own qa-audit.json report. Does not synthesize or render video.
The shared settings renderer is called to validate actual field/table geometry.
Semantic and visual review are reported separately from these mechanical checks.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

EPISODE = Path(__file__).resolve().parent
HERE = EPISODE.parent.parent
SOURCE = EPISODE / 'script.json'
REPORT = EPISODE / 'qa-audit.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if not SOURCE.exists():
        print(json.dumps({'status': 'pending', 'reason': 'script.json not authored yet'}))
        return 0
    script = json.loads(SOURCE.read_text())
    errors, warnings = [], []
    turns = script.get('turns', [])
    active = [t for t in turns if t['id'] not in script.get('firstCut', {}).get('omitTurnIds', [])]
    views, source_refs, subtitle_count = {}, [], 0

    def require(condition, message):
        if not condition:
            errors.append(message)

    require(script.get('episodeKey') == '09' and script.get('episodeNumber') == 9,
            'Episode identity differs from 09 / 9')
    require(script.get('visualStyle') == 'teaching-settings-v2', 'Wrong visual style')
    require(script.get('speed') == 1, 'Speech speed must be 1')
    require(script.get('status') in ('ready-for-generation', 'first-cut-produced'), 'Script is not ready')
    require(not re.search(r'\bTODO\b', json.dumps(script), re.I), 'TODO remains in source')
    ids = [t['id'] for t in turns]
    require(len(set(ids)) == len(ids), 'Turn IDs are not unique')
    chapters = script.get('chapters', [])
    chapter_ids = {c['id'] for c in chapters}
    require(len(chapter_ids) == len(chapters), 'Chapter IDs are not unique')
    require({t['speaker'] for t in active if t['chapterId'] == 'opening'} == {'mako', 'manabu', 'ikumi'},
            'Opening does not include all three speakers')

    for ref in script.get('sourceReferences', []) + [r for c in chapters for r in c.get('sourceRefs', [])]:
        file = (HERE / ref['file']).resolve()
        require(file.is_file(), f'Missing source: {ref["file"]}')
        if file.is_file() and ref.get('anchor'):
            require(re.search(r'\bid=["\']' + re.escape(ref['anchor']) + r'["\']', file.read_text()) is not None,
                    f'Missing source anchor: {ref["file"]}#{ref["anchor"]}')
        if ref not in source_refs:
            source_refs.append(ref)
    require(bool(source_refs), 'No sources listed')

    for turn in active:
        tid, visual = turn['id'], turn.get('visual', {})
        require(turn.get('chapterId') in chapter_ids, f'{tid}: missing chapter')
        require(turn.get('speaker') in ('mako', 'manabu', 'ikumi'), f'{tid}: invalid speaker')
        require(bool(turn.get('speech')) and bool(turn.get('caption')), f'{tid}: empty speech/caption')
        chunks = turn.get('subtitleChunks') or [{'caption': turn['caption'], 'lines': turn.get('subtitleLines', [])}]
        require(''.join(c['caption'] for c in chunks) == turn['caption'], f'{tid}: caption/chunk text differs')
        for chunk in chunks:
            subtitle_count += 1
            lines = chunk.get('lines', [])
            require(1 <= len(lines) <= 2, f'{tid}: subtitle must have 1–2 lines')
            require(''.join(lines) == chunk['caption'], f'{tid}: subtitle lines omit/duplicate text')
            if 'weight' in chunk:
                require(isinstance(chunk['weight'], (int, float)) and chunk['weight'] > 0,
                        f'{tid}: invalid subtitle weight')
            max_len = max([len(s) for s in lines] + [1])
            size = min(32, max(24, 1370 / max_len))
            for line in lines:
                units = sum(.56 if ord(c) < 128 else 1 for c in line)
                require(units * size <= 1464, f'{tid}: subtitle may exceed fixed caption panel')
                if len(line) > 55:
                    warnings.append(f'{tid}: long subtitle line ({len(line)} characters), inspect visually')
        if turn['chapterId'] == 'opening':
            require(visual.get('layout') == 'hologram', f'{tid}: opening should use hologram')
            continue
        require(bool(visual.get('viewId')), f'{tid}: body viewId missing')
        require(visual.get('layout') in ('settings', 'svg'), f'{tid}: body not in settings or settings-shell SVG')
        for key in ('zoom', 'camera', 'motion', 'azureMotion', 'pulse', 'pointer'):
            require(not visual.get(key), f'{tid}: forbidden body movement {key}')
        vid = visual.get('viewId', '<missing>')
        base = {k: v for k, v in visual.items() if k not in ('highlightFields', 'highlightRows', 'focus', 'highlight')}
        if visual.get('layout') == 'svg':
            svg = (EPISODE / visual.get('svg', '')).resolve()
            require(svg.is_relative_to(EPISODE) and svg.is_file(), f'{tid}: missing/unsafe SVG')
            # SVG variants need actual XML/visual comparison; filenames alone cannot prove identical layout.
            base.pop('svg', None)
        group = views.setdefault(vid, {'turnIds': [], 'layout': visual.get('layout'), 'base': base, 'svgFiles': []})
        require(base == group['base'], f'{tid}: base visual changes within viewId {vid}')
        group['turnIds'].append(tid)
        if visual.get('svg') and visual['svg'] not in group['svgFiles']:
            group['svgFiles'].append(visual['svg'])
        if turn['speaker'] == 'mako' and (turn['chapterId'] in ('check', 'quiz') or 'quiz' in vid):
            require(not visual.get('highlightRows') and not visual.get('highlightFields') and not visual.get('focus') and not visual.get('highlight'),
                    f'{tid}: quiz question exposes a highlighted answer')

    # Validate exact renderer constraints rather than duplicating width/wrapping rules.
    code = '''
import fs from 'node:fs/promises';
import {renderSettingsSvg} from './settings-visual.mjs';
const s=JSON.parse(await fs.readFile('episodes/09/script.json','utf8'));
const rows=[];
for (const t of s.turns.filter(t=>t.visual?.layout==='settings')) {
 try {renderSettingsSvg(t.visual); rows.push({id:t.id,status:'passed'});}
 catch(e){rows.push({id:t.id,status:'failed',error:e.message});}
}
console.log(JSON.stringify(rows));
'''
    result = subprocess.run(['node', '--input-type=module', '-e', code], cwd=HERE, capture_output=True, text=True)
    renderer = []
    if result.returncode:
        errors.append('Cannot execute shared settings renderer: ' + result.stderr[-1000:])
    else:
        renderer = json.loads(result.stdout)
        errors.extend(f'{r["id"]}: {r["error"]}' for r in renderer if r['status'] != 'passed')
    speech_chars = sum(len(t['speech']) for t in active)
    if not 5000 <= speech_chars <= 6000:
        warnings.append(f'Speech length is {speech_chars}; 5,000–6,000 is an estimate, duration must use actual recordings')
    if not 80 <= len(active) <= 100:
        warnings.append(f'Turn count is {len(active)}; guideline is 80–100, not a quota')
    for vid, group in views.items():
        if len(group['svgFiles']) > 1:
            warnings.append(f'{vid}: compare {len(group["svgFiles"])} SVG variants for invariant geometry and answer reveal')
        group.pop('base')
    report = {
        'status': 'passed' if not errors else 'failed', 'scriptSha256': digest(SOURCE),
        'scope': 'Mechanical source, subtitle, settings geometry and view-base checks; semantic/visual/listening reviews separate',
        'turnCount': len(active), 'speechCharacters': speech_chars, 'subtitleChunks': subtitle_count,
        'viewCount': len(views), 'views': views, 'sources': source_refs,
        'settingsRendererCheckedTurns': len(renderer), 'errors': errors, 'warnings': warnings,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('status', 'turnCount', 'speechCharacters', 'viewCount', 'errors', 'warnings')}, ensure_ascii=False))
    return int(bool(errors))


if __name__ == '__main__':
    sys.exit(main())
