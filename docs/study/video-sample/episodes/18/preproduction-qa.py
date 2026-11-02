"""Episode 18 local authoring checks; does not synthesize or validate final media."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import html
import json
import re
import sys

P = Path(__file__).resolve().parent
BASE = P.parent.parent
OUT = BASE / 'output/lesson-18-gemini'
raw = (P / 'script.json').read_bytes()
s = json.loads(raw)
turns = s['turns']
errors, warnings = [], []
views, spans = {}, {}

def check(condition, message):
    if not condition:
        errors.append(message)

def units(text):
    return sum(.58 if ord(c) < 128 else 1 for c in text)

check(s.get('status') in ('ready-for-generation', 'first-cut-produced'), 'script is not reviewed/ready')
check(s.get('visualStyle') == 'teaching-settings-v2', 'unexpected visual style')
check(s.get('speed') == 1, 'speech speed must be one')
check(s.get('episodeKey') == '18' and s.get('episodeNumber') == 18, 'episode identity mismatch')
check(not re.search(r'\bTODO\b', raw.decode(), re.I), 'TODO remains')
check(len({t['id'] for t in turns}) == len(turns), 'duplicate turn IDs')
chapters = {c['id']: c for c in s['chapters']}
check(len(chapters) == len(s['chapters']), 'duplicate chapter IDs')
check({t['speaker'] for t in turns if t['chapterId'] == 'opening'} == {'mako', 'manabu', 'ikumi'}, 'opening must include all three people')
cue_count, max_line_units = 0, 0
for index, t in enumerate(turns):
    label = t['id']
    check(t['chapterId'] in chapters, label + ': unknown chapter')
    check(t['speaker'] in {'mako', 'manabu', 'ikumi'}, label + ': unknown speaker')
    check(bool(t.get('speech')) and bool(t.get('caption')), label + ': empty speech/caption')
    chunks = t.get('subtitleChunks') or [{'caption': t['caption'], 'lines': t.get('subtitleLines', [])}]
    check(''.join(c['caption'] for c in chunks) == t['caption'], label + ': caption chunks incomplete')
    for c in chunks:
        cue_count += 1
        check(1 <= len(c['lines']) <= 2, label + ': subtitle must have one or two lines')
        check(''.join(c['lines']) == c['caption'], label + ': subtitle lines differ from chunk')
        check(c.get('weight', 1) > 0, label + ': nonpositive subtitle weight')
        for line in c['lines']:
            max_line_units = max(max_line_units, units(line))
            check(units(line) <= 44, label + ': subtitle line exceeds 44 full-width units')
    v = t['visual']
    check(not any(k in v for k in ('zoom', 'camera', 'motion', 'azureMotion', 'pulse', 'pointer')), label + ': body camera/motion directive')
    if t['chapterId'] == 'opening':
        continue
    view_id = v.get('viewId')
    check(bool(view_id), label + ': missing body viewId')
    canonical = {key: value for key, value in v.items() if key not in ('highlightFields', 'highlightRows')}
    if view_id in views:
        check(views[view_id] == canonical, label + ': view structure changed within ' + str(view_id))
    views[view_id] = canonical
    spans.setdefault(view_id, []).append(index)
    if v['layout'] == 'settings':
        check(bool(v.get('table')) != bool(v.get('fields')), label + ': settings needs table or fields')
        rows = v.get('fields') or v.get('table', {}).get('rows', [])
        check(1 <= len(rows) <= 6, label + ': settings row count')
        row_ids = [r['id'] for r in rows]
        check(len(row_ids) == len(set(row_ids)), label + ': duplicate setting row IDs')
        check(set(v.get('highlightFields', []) + v.get('highlightRows', [])) <= set(row_ids), label + ': highlight target missing')
        check(len(v.get('menu', [])) <= 8, label + ': too many menu entries')
    if ('check' in t['chapterId'] or 'quiz' in t['chapterId']) and t['speaker'] == 'mako':
        check(not v.get('highlightFields') and not v.get('highlightRows'), label + ': question reveals highlighted answer')

for view_id, positions in spans.items():
    if positions[-1] - positions[0] + 1 != len(positions):
        warnings.append('view is revisited: ' + str(view_id))
refs = s.get('sourceReferences', []) + [r for c in s['chapters'] for r in c.get('sourceRefs', [])]
check(bool(s.get('sourceReferences')), 'no source references')
for c in s['chapters']:
    check(bool(c.get('sourceRefs')), 'chapter lacks source references: ' + c['id'])
for ref in refs:
    path = (BASE / ref['file']).resolve()
    check(path.is_file(), 'missing source: ' + ref['file'])
    if path.is_file() and ref.get('anchor'):
        check('id="' + ref['anchor'] + '"' in path.read_text(), 'missing source anchor: ' + ref['anchor'])

result = {
    'status': 'passed' if not errors else 'failed',
    'checkedAt': datetime.now(timezone.utc).isoformat(),
    'episode': '18', 'turnCount': len(turns), 'captionCueCount': cue_count,
    'captionChars': sum(len(t['caption']) for t in turns),
    'speechChars': sum(len(t['speech']) for t in turns),
    'maximumSubtitleLineFullWidthUnits': max_line_units,
    'bodyViews': len(views), 'bodyCamera': 'fixed',
    'viewTurnRanges': {k: [turns[i]['id'] for i in v] for k, v in spans.items()},
    'scriptSha256': hashlib.sha256(raw).hexdigest(),
    'errors': errors, 'warnings': warnings,
    'scope': 'Local source/schema/subtitle/continuity checks. Final media and speech listening require separate review.'
}
(P / 'preproduction-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
lines = ['# 第18回 対話原稿の確認', '', s.get('description', s['topic']), '']
for chapter in s['chapters']:
    lines.extend(['## ' + chapter['title'], ''])
    for t in turns:
        if t['chapterId'] == chapter['id']:
            lines.extend(['**' + t['speaker'].upper() + '**（' + t['id'] + '）', '', t['caption'], ''])
(P / 'script-review.md').write_text('\n'.join(lines))
OUT.mkdir(parents=True, exist_ok=True)
cards = ['<article><h2>' + html.escape(t['id']) + '</h2><img loading="lazy" src="previews/' + t['id'] + '.png"><p>' + html.escape(t['caption']) + '</p></article>' for t in turns]
(OUT / 'storyboard.html').write_text('<!doctype html><html lang="ja"><meta charset="utf-8"><title>第18回 制作プレビュー</title><style>body{max-width:1200px;margin:2rem auto;padding:0 1rem;font-family:system-ui;background:#08131c;color:#e5f4f5}article{margin:2rem 0}img{width:100%}p{line-height:1.8}h2{font-size:1rem}</style><h1>第18回 組織・SQL・APIMの状態を整える</h1>' + ''.join(cards) + '</html>')
print(json.dumps({k: v for k, v in result.items() if k != 'viewTurnRanges'}, ensure_ascii=False))
sys.exit(1 if errors else 0)
