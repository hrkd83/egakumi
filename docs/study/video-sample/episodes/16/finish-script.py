"""Apply reviewed content, episode diagrams, and readable subtitle wrapping."""
from pathlib import Path
import json
import re

HERE=Path(__file__).resolve().parent
p=HERE/'script.json'
s=json.loads(p.read_text())
replacements=[('portalApiKey.id','ポータルエーピーアイキーのアイディー'),('apimSubscriptionId','エーピーアイエムサブスクリプションアイディー'),('apiKeyId','エーピーアイキーアイディー'),('primaryKey','プライマリーキー'),('listSecrets','リストシークレッツ'),('Microsoft Graph','マイクロソフトグラフ'),('Realtime','リアルタイム'),('Gateway','ゲートウェイ'),('Portal','ポータル'),('JSON','ジェイソン'),('HTTP','エイチティーティーピー'),('REST','レスト'),('APIM','エーピーアイエム'),('SQL','エスキューエル'),('ARM','アーム'),('API','エーピーアイ'),('MCP','エムシーピー'),('PII','ピーアイアイ'),('URL','ユーアールエル'),('GET','ゲット'),('ID','アイディー'),('Model','モデル'),('Tool','ツール'),('active','アクティブ'),('scope','スコープ'),('origin','オリジン')]
def spoken(text):
    for a,b in replacements:text=text.replace(a,b)
    return text
def units(text):return sum(.55 if ord(c)<128 else 1 for c in text)
def wrap(text):
    if units(text)<=40:return [text]
    candidates=[]
    for i in range(1,len(text)):
        if text[i] in '、。，．？！：；）］」』】ー':continue
        if re.match(r'[A-Za-z0-9_.]',text[i-1]) and re.match(r'[A-Za-z0-9_.]',text[i]):continue
        if text[i-1] in '（［「『【':continue
        a,b=units(text[:i]),units(text[i:])
        if max(a,b)>40:continue
        score=abs(a-b)+(0 if text[i-1] in '、。？！' else 3)
        candidates.append((score,i))
    if not candidates:raise ValueError('Split long subtitle chunk: '+text)
    i=min(candidates)[1]
    return [text[:i],text[i:]]

diagram_map={9:('catalog','neutral'),10:('catalog','source'),11:('catalog','source'),12:('catalog','generated'),13:('catalog','display'),33:('execution','neutral'),34:('execution','permission'),35:('execution','permission'),36:('execution','secret'),37:('execution','result'),38:('execution','neutral')}
for t in s['turns']:
    n=int(t['id'].split('-')[2])
    if n==32:
        t['caption']=t['caption'].replace('実行時にも確認','実行時に確認')
        t['speech']=t['speech'].replace('実行時にも確認','実行時に確認')
    if n==40:
        t['caption']=t['caption'].replace('所有グループの承認、会社や台帳の状態','所有グループの製品承認、台帳の状態')
        t['speech']=t['speech'].replace('所有グループの承認、会社や台帳の状態','所有グループの製品承認、台帳の状態')
    if t['visual'].get('viewId')=='execution-lock':
        for f in t['visual'].get('fields',[]):
            if f['id']=='sql':f['value']='承認・所有・台帳状態'
    if n in diagram_map:
        name,variant=diagram_map[n]
        view='catalog-build-flow' if name=='catalog' else 'execution-flow'
        heading='API定義からカタログを生成する' if name=='catalog' else '通常RESTの試用要求を追う'
        t['visual']={'layout':'svg','viewId':view,'heading':heading,'svg':f'diagrams/{name}-{variant}.svg'}
    captions=re.findall(r'[^。！？!?]+[。！？!?]?|[。！？!?]',t['caption'])
    chunks=[]
    for c in captions:
        if units(c)>78:
            # Keep each caption readable; preserve every character in order.
            parts=re.findall(r'[^、]+、?|、',c)
            rows=[];current=''
            for part in parts:
                if current and units(current+part)>70:rows.append(current);current=part
                else:current+=part
            if current:rows.append(current)
        else:rows=[c]
        for row in rows:chunks.append({'caption':row,'lines':wrap(row),'weight':len(spoken(row))})
    assert ''.join(c['caption'] for c in chunks)==t['caption']
    t['subtitleChunks']=chunks
    t['subtitleLines']=chunks[0]['lines']
s['status']='draft'
p.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'turns':len(s['turns']),'speechCharacters':sum(len(t['speech']) for t in s['turns']),'status':s['status']},ensure_ascii=False))
