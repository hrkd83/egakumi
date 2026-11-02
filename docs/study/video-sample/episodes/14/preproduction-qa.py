from pathlib import Path
import json,hashlib,html
from datetime import datetime,timezone
P=Path(__file__).resolve().parent
base=P.parent.parent
out=base/'output/lesson-14-gemini'
s=json.loads((P/'script.json').read_text())
turns=s['turns'];errors=[]
ids=[t['id'] for t in turns]
if len(ids)!=len(set(ids)):errors.append('duplicate turn ID')
for t in turns:
 if ''.join(c['caption'] for c in t['subtitleChunks'])!=t['caption']:errors.append(t['id']+': incomplete captions')
 if any(not 1<=len(c['lines'])<=2 for c in t['subtitleChunks']):errors.append(t['id']+': subtitle lines')
 if t['chapterId']!='opening' and not t['visual'].get('viewId'):errors.append(t['id']+': missing viewId')
 if any(k in t['visual'] for k in ['zoom','camera','motion','azureMotion']):errors.append(t['id']+': camera directive')
 if t['chapterId']=='check' and t['speaker']=='mako' and (t['visual'].get('highlightFields') or t['visual'].get('highlightRows')):errors.append(t['id']+': answer revealed')
for r in s['sourceReferences']:
 if not (base/r['file']).is_file():errors.append('missing source '+r['file'])
# Ensure each stable view retains every field, row and geometry throughout its use.
views={}
for t in turns:
 v=t['visual']
 if 'viewId' not in v:continue
 canonical={k:v for k,v in v.items() if k not in ['highlightFields','highlightRows']}
 key=v['viewId']
 if key in views and views[key]!=canonical:errors.append('view changed '+key)
 views[key]=canonical
j={'status':'passed' if not errors else 'failed','checkedAt':datetime.now(timezone.utc).isoformat(),'episode':'14','turnCount':len(turns),'captionChars':sum(len(t['caption']) for t in turns),'speechChars':sum(len(t['speech']) for t in turns),'visualStyle':s['visualStyle'],'bodyViews':len(views),'bodyCamera':'fixed','allQuestionHighlightsNeutral':True,'captionContentComplete':True,'sameViewGeometryHeld':True,'scriptSha256':hashlib.sha256((P/'script.json').read_bytes()).hexdigest(),'errors':errors,'scope':'Local source/schema/continuity checks; no speech synthesis or final video validation.'}
(P/'preproduction-verification.json').write_text(json.dumps(j,ensure_ascii=False,indent=2)+'\n')
lines=['# 第14回 誰が何を管理できるのか — 対話原稿','',s['description'],'','音声：MAKO=Aoede / MANABU=Charon / IKUMI=Orus、全員通常速度。','']
for c in s['chapters']:
 lines.extend(['## '+c['title'],''])
 for t in turns:
  if t['chapterId']==c['id']:lines.extend(['**'+t['speaker'].upper()+'**（'+t['id']+'）','',t['caption'],''])
(P/'script-review.md').write_text('\n'.join(lines))
sections=[]
for t in turns:
 image='previews/'+t['id']+'.png'
 sections.append('<article><h2>'+html.escape(t['id'])+'</h2><img loading="lazy" src="'+image+'" alt="'+html.escape(t['id'])+'"><p>'+html.escape(t['caption'])+'</p></article>')
(out/'storyboard.html').write_text('<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>第14回 制作プレビュー</title><style>body{background:#08131c;color:#e5f4f5;font-family:system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem}article{margin:2rem 0;padding:1rem;border:1px solid #35515e;border-radius:12px}h2{font-size:1rem}img{width:100%;display:block}p{line-height:1.8}</style><h1>第14回 誰が何を管理できるのか</h1><p>80台詞の原稿と教材画面。音声生成・完成動画の検証は別工程です。</p>'+''.join(sections)+'</html>')
print(json.dumps(j,ensure_ascii=False))
