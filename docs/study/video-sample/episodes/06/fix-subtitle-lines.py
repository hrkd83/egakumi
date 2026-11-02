from pathlib import Path
import json,re
p=Path(__file__).with_name('script.json')
s=json.loads(p.read_text())
# Only display line breaks change. Narration, captions, chunk timing and text stay fixed.
cuts={
(1,0):'と言われました。',
(4,0):'見えるか。',
(5,0):'外向き通信です。',
(7,0):'をそろえます。',
(8,0):'基づきます。',
(13,0):'分かります。',
(16,0):'分ける。',
(22,0):'通信は別。',
(23,0):'空です。',
(24,0):'決められません。',
(25,0):'わけですね。',
(28,0):'CloudflareならそのAPI、',
(29,0):'Portalのホストも、',
(32,0):'Azure管理APIや、',
(37,0):'次は出口です。',
(39,0):'あります。',
(40,0):'falseなのは、',
(45,0):'言えませんね。',
(47,0):'NAT Gatewayや',
(50,0):'private IPではなく、',
(56,0):'Firewallへ戻す',
(63,0):'Portalには',
(64,1):'Firewallの公開IPを',
(64,2):'EXPECTED_EGRESS_IP',
(65,0):'出口確認用の',
(66,0):'用意されている。',
(69,0):'記録します。',
(72,0):'大切です。',
(74,0):'許可FQDN配列が空でした。',
(77,0):'snat.privateRangesに',
(78,0):'宛先範囲です。',
(82,0):'共通していますね。',
(84,1):'Firewall出口IPを区別し、',
}
changes=[]
for (turn_number,chunk_number),end in cuts.items():
 t=s['turns'][turn_number-1];c=t['subtitleChunks'][chunk_number]
 caption=c['caption'];cut=caption.index(end)+len(end)
 lines=[caption[:cut],caption[cut:]]
 assert all(lines) and max(map(len,lines))<=43,(turn_number,lines)
 changes.append((turn_number,chunk_number,c['lines'],lines))
 c['lines']=lines
# A short complete phrase can occupy one line.
for t in s['turns']:
 for c in t['subtitleChunks']:
  if len(c['caption'])<=36 and len(c['lines'])==2:
   c['lines']=[c['caption']]
 t['subtitleLines']=t['subtitleChunks'][0]['lines']
for t in s['turns']:
 assert ''.join(c['caption'] for c in t['subtitleChunks'])==t['caption']
 for c in t['subtitleChunks']:
  assert ''.join(c['lines'])==c['caption'] and 0<len(c['lines'])<=2
  if len(c['lines'])==2:
   a,b=c['lines']
   assert not (re.search(r'[A-Za-z0-9_.]$',a) and re.match(r'[A-Za-z0-9_.]',b)),(t['id'],c)
   assert not re.match(r'[、。）」』！？]',b),(t['id'],c)
p.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'changedExplicitBreaks':len(changes),'maximumLineCharacters':max(len(line) for t in s['turns'] for c in t['subtitleChunks'] for line in c['lines']),'captionCharacters':sum(len(t['caption']) for t in s['turns']),'speechCharacters':sum(len(t['speech']) for t in s['turns'])},ensure_ascii=False))
for n,i,b,a in changes:
 if max(map(len,a))>40:print('wide',n,i,a)
