import json
from pathlib import Path
P=Path(__file__).resolve().parent
s=json.loads((P/'script.json').read_text())
assert s['speed']==1 and s['visualStyle']=='teaching-settings-v2'
assert 'TODO' not in json.dumps(s,ensure_ascii=False)
assert len({t['id'] for t in s['turns']})==len(s['turns'])
views={}
for t in s['turns']:
 assert ''.join(c['caption'] for c in t['subtitleChunks'])==t['caption'], t['id']
 for c in t['subtitleChunks']:
  assert 1<=len(c['lines'])<=2 and ''.join(c['lines'])==c['caption']
  assert all(len(line)<=40 for line in c['lines']),t['id']
 v=t['visual']
 assert not any(k in v for k in ['zoom','camera','motion','azureMotion'])
 if t['chapterId']=='opening':continue
 assert v['viewId']
 base={k:value for k,value in v.items() if k not in ['highlightFields','highlightRows']}
 assert views.setdefault(v['viewId'],base)==base,t['id']
 if v['viewId'].startswith('lesson15-quiz') and t['speaker']=='mako':
  assert not v.get('highlightFields') and not v.get('highlightRows'),t['id']
for r in s['sourceReferences']:
 assert (P.parent.parent/r['file']).is_file(),r
result={'status':'passed','turns':len(s['turns']),'chapters':len(s['chapters']),'bodyViews':len(views),'captionChars':sum(len(t['caption']) for t in s['turns']),'checks':['source files exist','all subtitles preserve caption text','maximum two lines per chunk','stable body structure for each viewId','no body motion directives','no answer outline during quiz questions'],'auditoryReview':False}
(P/'preproduction-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False))
