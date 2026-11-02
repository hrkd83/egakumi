"""Episode-local content/continuity checks; no API calls or synthesis."""
from collections import defaultdict
from pathlib import Path
import hashlib
import json
import re

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
source=json.loads((HERE/'script.json').read_text())
groups=defaultdict(list)
errors=[]
seen=set()
previous=None
closed=set()
for turn in source['turns']:
    if turn['id'] in seen: errors.append('Duplicate turn '+turn['id'])
    seen.add(turn['id'])
    for chunk in turn.get('subtitleChunks',[{'caption':turn['caption'],'lines':turn['subtitleLines']}]):
        if not 1<=len(chunk['lines'])<=2: errors.append('Caption lines '+turn['id'])
        if ''.join(chunk['lines'])!=chunk['caption']: errors.append('Caption wrapping '+turn['id'])
    if ''.join(c['caption'] for c in turn.get('subtitleChunks',[])) not in ('',turn['caption']):
        errors.append('Caption completeness '+turn['id'])
    visual=turn['visual']
    if turn['chapterId']=='opening': continue
    view=visual.get('viewId')
    if not view: errors.append('Missing viewId '+turn['id'])
    if any(k in visual for k in ('zoom','motion','camera','azureMotion')): errors.append('Body camera '+turn['id'])
    if previous and previous!=view: closed.add(previous)
    if view in closed: errors.append('Non-contiguous view '+str(view))
    previous=view
    if visual['layout']=='svg':
        svg=(HERE/visual['svg']).read_text()
        baseline=re.sub(r'<g id="highlights">.*?</g>','',svg,flags=re.S)
    else:
        baseline=json.dumps({k:v for k,v in visual.items() if k not in ('highlightFields','highlightRows')},ensure_ascii=False,sort_keys=True)
    groups[view].append((turn['id'],hashlib.sha256(baseline.encode()).hexdigest()))
for view,rows in groups.items():
    if len({r[1] for r in rows})!=1: errors.append('Changing view structure '+view)
for ref in source['sourceReferences']:
    file=(ROOT/ref['file']).resolve()
    if not file.is_file(): errors.append('Missing source '+ref['file'])
result={
    'status':'passed' if not errors else 'failed',
    'episodeKey':'16','visualStyle':source.get('visualStyle'),
    'turnCount':len(source['turns']),
    'speechCharacters':sum(len(t['speech']) for t in source['turns']),
    'openingSpeechCharacters':sum(len(t['speech']) for t in source['turns'] if t['chapterId']=='opening'),
    'viewCount':len(groups),
    'views':{v:{'turns':[r[0] for r in rows],'structureSha256':rows[0][1]} for v,rows in groups.items()},
    'errors':errors,'networkRequestsMade':False
}
print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(bool(errors))
