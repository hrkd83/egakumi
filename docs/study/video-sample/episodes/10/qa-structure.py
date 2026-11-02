"""Episode-local checks for caption coverage and fixed question/answer views."""
import collections
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
source = HERE / 'script.json'
script = json.loads(source.read_text())
errors = []
views = {}
groups = []
captions = 0
max_line = 0
counts = collections.Counter()
for turn in script['turns']:
    counts[turn['speaker']] += 1
    chunks = turn.get('subtitleChunks') or [{'caption': turn['caption'], 'lines': turn['subtitleLines']}]
    if ''.join(c['caption'] for c in chunks) != turn['caption']:
        errors.append(turn['id'] + ': caption coverage')
    for chunk in chunks:
        captions += 1
        if not 1 <= len(chunk['lines']) <= 2 or ''.join(chunk['lines']) != chunk['caption']:
            errors.append(turn['id'] + ': subtitle lines')
        max_line = max(max_line, *(len(line) for line in chunk['lines']))
    if turn['chapterId'] == 'opening':
        continue
    visual = turn['visual']
    view_id = visual.get('viewId')
    if not view_id:
        errors.append(turn['id'] + ': missing viewId')
    if any(visual.get(k) for k in ['camera', 'zoom', 'motion', 'azureMotion', 'pulse']):
        errors.append(turn['id'] + ': body motion')
    stable = {k: v for k, v in visual.items() if k not in ['highlightRows', 'highlightFields']}
    if view_id in views and stable != views[view_id]:
        errors.append(turn['id'] + ': composition changed inside viewId ' + view_id)
    views[view_id] = stable
    if not groups or groups[-1]['viewId'] != view_id:
        groups.append({'viewId': view_id, 'turnIds': []})
    groups[-1]['turnIds'].append(turn['id'])

report = {
    'status': 'passed' if not errors else 'failed',
    'scriptSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'turns': len(script['turns']),
    'speechCharacters': sum(len(t['speech']) for t in script['turns']),
    'speakers': dict(counts), 'subtitleChunks': captions,
    'maximumSubtitleLineCharacters': max_line,
    'fixedBodyViews': len(views), 'viewGroups': groups,
    'bodyCamera': 'fixed', 'errors': errors,
    'limitations': 'Structural checks do not establish pronunciation or listening quality.'
}
(HERE / 'structural-qa.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k: v for k, v in report.items() if k != 'viewGroups'}, ensure_ascii=False))
raise SystemExit(bool(errors))
