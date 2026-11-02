#!/usr/bin/env python3
"""Migrate only episode 07 visuals; preserve every speech/style/timing input."""
import copy,hashlib,json
from pathlib import Path
E=Path(__file__).resolve().parent
s=json.loads((E/'history/diagram-v1/script.json').read_text())
MENU=['通信の特定','DNS','実効経路','NSG','Firewallログ','収集範囲','通知条件','確認・まとめ']
def view(key,title,section,rows,graph=None):
 v={'layout':'settings','viewId':key,'areaLabel':'ネットワーク調査 / 教材用の設定メニュー','heading':title,'title':title,'kind':'通信の到達性を調べる','section':section,'menu':MENU,'subtitle':'現行定義・公開設定例に基づく教材。実環境の値は調査時に確認します。','footer':'教材注釈：公開設定例と、調査時に照合する項目・判断を示しています。','table':{'columns':[{'label':'確認する設定・記録','width':260},{'label':'定義・照合する内容','width':712}],'rows':[{'id':i,'cells':[a,b]} for i,a,b in rows]},'highlightRows':[]}
 if graph:
  v['layout']='svg';v['settingsView']=copy.deepcopy(v);v['settingsView']['layout']='settings';v['settingsView']['graph']=graph;del v['table'];del v['highlightRows']
 return v
def select(base,focus=(),values=None,nodes=()):
 v=copy.deepcopy(base);d=v.get('settingsView',v);d['highlightRows']=list(focus)
 for r in d['table']['rows']:
  if values and r['id'] in values:r['cells'][1]=values[r['id']]
 if 'graph' in d:d['graphFocus']=list(nodes)
 return v
V={}
V['identity']=view('connection-identity','調査する通信を一つに絞る','通信の特定',[
 ('time','失敗時刻','時刻・タイムゾーン・検索する時間幅'),('source','通信元','AppGWのprivate IP。元利用者のIPとは区別'),('destination','宛先','APIMの名前と、DNS回答の内部IP'),('transport','通信条件','TCP / 宛先80 / 新規接続か・失敗の症状'),('records','検索する対象','同じ環境のFirewall・LAW')], 'u1')
V['dns']=view('source-dns','送信元でDNS回答を確かめる','DNS',[
 ('source','失敗した送信元','AppGW。端末側の成功だけで判断しない'),('dns','参照するDNS','送信元設定・転送・ゾーンのリンクを確認'),('answer','宛先の名前と回答','APIMゲートウェイ名 → APIMの実内部IP'),('evidence','確認した場所','DNS回答と、調べた送信元を一緒に残す')], 'dns')
V['route']=view('u1-roundtrip','宛先IPの往復経路を照合する','実効経路',[
 ('forward','AppGW側のUDR','apim-via-firewall：APIM実内部VIP /32 → FW'),('return','APIM側の復路UDR','agw-return-via-firewall：AppGW区画 → 同じFW'),('effective','実効情報と関連付け','具体的な経路・区画・取得可能な実効情報を照合'),('permission','次ホップと許可','経路を選ぶUDRと、許可・拒否の判定を分ける')], 'roundtrip')
V['nsg']=view('u1-nsg','同じTCP 80を送受信のNSGで照合する','NSG',[
 ('send','AppGW送信','allow-apim-http：AppGW区画 → APIM区画'),('receive','APIM受信','allow-agw-api：AppGW区画 → APIM区画'),('firewall','FW Networkルール','agw-to-apim-http：AppGW区画 → 実APIM VIP'),('source','送信元と優先順位','標準Network/privateでは送信元保持。向きと優先順位も確認')], 'u1')
V['kind']=view('fw-log-kind','評価されたルール種別のログへ進む','Firewallログ',[
 ('network','Networkルール','AZFWNetworkRule / IP・ポート・Protocol'),('application','Applicationルール','AZFWApplicationRule / Web宛先・FQDN'),('order','評価順','Network一致ならApplicationへ進まない'),('example','U1の内部中継','HTTPでもNetworkルール。通信と評価順で選ぶ')])
V['log']=view('fw-log-evidence','同じ通信のログと適用条件を照合する','Firewallログ',[
 ('resource','対象・時刻','Firewall Resource ID / LAW / 検索する時間帯'),('tuple','通信条件','SourceIp / DestinationIp / DestinationPort / Protocol'),('action','Action','Allow / Deny。判定された地点を確認'),('reason','ActionReason','既定拒否では Default Action の場合もある'),('rule','ポリシーと規則','Policy / RuleCollectionGroup / RuleCollection / Rule')])
V['allow']=view('fw-allow-scope','FWのAllowが証明する範囲を確認する','Firewallログ',[
 ('record','今回の観測','同じ通信にFirewall Allowの記録'),('scope','判定できる範囲','何の成功を確認したかを分けて考える'),('next','続く確認','受信側・復路・接続先の記録を照合')])
V['absent']=view('fw-log-absence','ログがない通信の経路と収集状態を照合する','Firewallログ',[
 ('search','検索する条件','対象FW・LAW・時刻・通信条件・ログの種類'),('collect','収集と到着','診断設定・取り込み状態も確認する'),('direct','比較 D2','APIM → Foundry PE / TCP 443 は標準の直接経路'),('next','直接経路の確認先','DNS・実効経路・APIM送信/PE受信NSG・サービスログ')], 'direct')
V['walk']=view('u1-triage-example','演習：一つのU1接続失敗を追う','Firewallログ',[
 ('condition','通信条件','AppGW → APIM / TCP 80 / 時刻とIPを特定'),('dnsroute','DNS・往復経路','演習の観測は、説明の進行に合わせて確認'),('nsg','送信・受信NSG','演習の観測は、説明の進行に合わせて確認'),('action','FWログの判定','演習の観測は、説明の進行に合わせて確認'),('config','次の確認先','適用先・通信条件・ポリシー定義'),('change','変更後の確認','理由と元設定を残し、同条件の新規接続で確かめる')])
V['follow']=view('fw-allow-followup','FWで許可された先の状態を調べる','Firewallログ',[
 ('allow','FWでの観測','同じ条件のAllow。API全体の成功とは区別'),('network','通信の続き','受信側の状態・復路・APIM側の記録'),('access','AppGWアクセスログ','時刻・パス・status・バックエンド・TransactionId等'),('separate','別の判定','WAFログ、認証・要求処理の詳細と分ける'),('handoff','引き継ぐ内容','観測した事実 / 判断の範囲 / 未確認の層 / 次の確認先')])
V['stores']=view('log-destinations','診断設定の保存先と収集条件を見る','収集範囲',[
 ('law','LAWへの送信','Network / Application をDedicated形式で保存'),('audit','監査Storageへの送信','監査収集を有効にした構成で、日本側へ送信'),('arrival','調査時の確認','診断設定と、保存先への到着状況を分けて確認')], 'stores')
V['scope']=view('signal-coverage','通信ログ・数値・管理操作の範囲を分ける','収集範囲',[
 ('flow','VNet全体の観測','現行infraにVNet/NSG Flow Logs・Packet Captureの定義なし'),('communication','通信ログ','その資源での通信の許可・拒否を調べる'),('metric','メトリック','状態を表す数値を調べる'),('activity','Activity Log','管理操作や基盤状態を調べる'),('limit','観測できる経路','PE直接通信を何でもFirewallログで追える構成ではない')])
V['notify']=view('notification-chain','保存するログと通知条件を分ける','通知条件',[
 ('deny','個別通信のDeny','通知する条件を、ログ保存とは別に確認'),('agw','AppGWの状態','UnhealthyHostCount 平均 > 0 / 5分窓・毎分評価'),('fw','Firewallの状態','FirewallHealth 平均 < 100 / 5分窓・毎分評価'),('action','Action Group','運用者のメール。Webhook・自動復旧/撤去は未登録')], 'notify')
V['activity']=view('activity-alert-scope','変更通知の操作名・結果・対象IDを見る','通知条件',[
 ('match','照合する条件','操作名 / Succeeded・Failed / 対象Resource ID'),('include','対象の例','VNet・NSG・FW・指定ルートテーブル'),('exclude','一覧に含めない資源','別宣言のACA用RT / PE / Private DNSゾーン'),('scope','収集と通知の違い','Activityの収集範囲は、個別通知対象より広い'),('result','操作結果の解釈','Failedを設定反映済みとは判断しない')])
V['flag']=view('log-alert-flag','enableLogAlertsの切替範囲を確認する','通知条件',[
 ('config','公開設定例','本番 false / テスト true'),('kql','切り替える3本のKQL通知','監査Blob書込失敗 / 取り込み量 / API失敗率'),('other','別の設定で有効','診断ログ収集 / メトリック / Activity通知'),('deny','FW Denyの通知','全件通知を有効にするフラグではない')])
V['quiz1']=view('check-pe-no-log','確認① PE通信のFWログがない','確認・まとめ',[
 ('condition','条件','APIM → Foundry PE / TCP 443 / FWログなし'),('question','問い','Firewall Allowを足せばよい？'),('judgment','判断','回答で確認する'),('next','最初に調べること','回答で確認する')])
V['quiz2']=view('check-fw-allow','確認② 同じ通信のFW Allowがある','確認・まとめ',[
 ('condition','条件','同じ通信のFirewall Allowを確認'),('question','問い','APIの認証・処理も成功した？'),('judgment','判断','回答で確認する'),('next','残る確認','回答で確認する')])
V['quiz3']=view('check-no-notification','確認③ Denyは保存されたがメールがない','確認・まとめ',[
 ('condition','条件','FW Denyは保存済み / 通知メールはなし'),('question','問い','enableLogAlerts=falseだけが原因？'),('judgment','判断','回答で確認する'),('next','調べる設定・状態','回答で確認する')])
V['summary']=view('troubleshooting-summary','同じ通信で調べる順序を振り返る','確認・まとめ',[
 ('identity','1. 通信を特定','時刻・通信元・宛先・プロトコル・ポート'),('control','2. 経路と制御','DNS回答 → 実効経路と往復 → 両側NSG'),('log','3. 経由FWの判定','評価されたログ種類・Action・適用規則'),('limit','4. 判断の範囲','ログ不在 / Allow / 通知不在だけで全体を決めない'),('record','5. 記録して引き継ぐ','確認した事実と未確認を分ける'),('next','次回 第8回','基盤管理者のprivate接続・NSG・SSH・Managed Run Command')])

def assign(nums,key,focus=(),values=None,nodes=()):
 for n in nums:
  t=s['turns'][n-1];t['visual']=select(V[key],focus,values,nodes)
assign(range(7,18),'identity')
for n,f in {7:[],8:['transport'],9:['source'],10:[],11:['time','source','destination','transport'],12:['records'],13:['time'],14:['time','records'],15:[],16:['source'],17:['time','destination']}.items():assign([n],'identity',f,nodes=['agw'] if n in [8,9,16] else [])
assign(range(18,22),'dns');assign([19,20],'dns',['source','dns'],nodes=['source']);assign([21],'dns',['answer','evidence'],nodes=['answer'])
assign(range(22,28),'route');assign([23],'route',['effective']);assign([24],'route',['forward','return'],nodes=['agw','fw','apim']);assign([25],'route',['return']);assign([26],'route',['effective']);assign([27],'route',['permission'])
assign(range(28,32),'nsg');assign([29],'nsg',['send','receive']);assign([30],'nsg',['source']);assign([31],'nsg',['send','receive','firewall'])
assign(range(32,37),'kind');assign([33],'kind',['network','example']);assign([35],'kind',['application','order']);assign([36],'kind',['order'])
assign(range(37,42),'log');assign([37],'log',['resource','tuple','action']);assign([39],'log',['reason']);assign([40],'log',['reason','rule']);assign([41],'log',['rule'])
assign([42,43],'allow');assign([43],'allow',['scope','next'],{'scope':'Allowが示すのは、そのFirewallでの許可'})
assign(range(44,48),'absent');assign([45],'absent',['search','collect']);assign([46],'absent',['direct'],nodes=['apim','pe']);assign([47],'absent',['next'])
assign(range(48,56),'walk')
for n in range(50,56):assign([n],'walk',[],{'dnsroute':'APIM内部IP・往復FW経由（演習の仮定）','nsg':'条件上は送受信とも許可（演習の仮定）','action':'Deny / Default Action（演習の仮定）' if n>=51 else 'まだ確認していない'})
assign([49],'walk',['condition']);assign([50],'walk',['dnsroute','nsg'],{'dnsroute':'APIM内部IP・往復FW経由（演習の仮定）','nsg':'条件上は送受信とも許可（演習の仮定）','action':'まだ確認していない'})
for n in [51,52]:s['turns'][n-1]['visual']['highlightRows']=['action','config']
for n in [53,54]:s['turns'][n-1]['visual']['highlightRows']=['config']
s['turns'][54]['visual']['highlightRows']=['change']
assign(range(56,60),'follow');assign([57],'follow',['network','access']);assign([58],'follow',['access','separate']);assign([59],'follow',['handoff'])
assign(range(60,63),'stores');assign([61],'stores',['law','audit'],nodes=['law','storage']);assign([62],'stores',['arrival'])
assign(range(63,66),'scope');assign([63],'scope',['flow']);assign([65],'scope',['metric','activity'])
assign(range(66,71),'notify');
for n in range(67,71):assign([n],'notify',[],{'deny':'現在、Deny全件を自動通知する定義はない'})
assign([67],'notify',['deny'],{'deny':'現在、Deny全件を自動通知する定義はない'},nodes=['rule']);assign([68],'notify',['agw','fw'],{'deny':'現在、Deny全件を自動通知する定義はない'});assign([69],'notify',['agw','fw'],{'deny':'現在、Deny全件を自動通知する定義はない'});assign([70],'notify',['action'],{'deny':'現在、Deny全件を自動通知する定義はない'},nodes=['mail'])
assign(range(71,74),'activity');assign([72],'activity',['match','exclude']);assign([73],'activity',['scope','result'])
assign(range(74,77),'flag');assign([75],'flag',['kql','other']);assign([76],'flag',['config','deny'])
assign(range(77,80),'quiz1');assign([78,79],'quiz1',['judgment','next'],{'judgment':'標準のD2はPEへの直接経路。FW追加と決めない','next':'DNS・実効経路・両側NSG・サービス側の記録'})
assign([80,81,82],'quiz2');assign([81,82],'quiz2',['judgment','next'],{'judgment':'FWでの許可。API全体の成功までは未確認','next':'受信側・復路・認証・認可・処理結果'})
assign([83,84,85],'quiz3');assign([84,85],'quiz3',['judgment','next'],{'judgment':'Deny全件通知はない。フラグだけで断定しない','next':'対象のアラート条件・評価結果・通知先'})
assign(range(86,92),'summary');
for n,f in {86:['identity'],87:['control','log'],88:['limit','record'],89:['limit','record'],90:['next'],91:['next']}.items():assign([n],'summary',f)
# Custom SVG variants differ only by highlighted targets or revealed values.
custom={}
for t in s['turns']:
 v=t['visual']
 if v['layout']=='svg':
  d=v['settingsView'];key=hashlib.sha256(json.dumps(d,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:12];name=d['viewId']+'-'+key+'.svg'
  v['svg']='diagrams/settings-v2/'+name;custom[name]=d
s['visualStyle']='teaching-settings-v2';s['visualRevision']='2026-11-02-settings-v2';s['visualCameraPolicy']={'body':'fixed','automaticZoom':False,'automaticPan':False,'redOutlinePulse':False,'holdQuestionAndAnswerView':True}
# No speech, delivery, chapter, ID, omission, gap, caption, or subtitle edits.
old=json.loads((E/'history/diagram-v1/script.json').read_text())
for a,b in zip(old['turns'],s['turns']):assert {k:v for k,v in a.items() if k!='visual'}=={k:v for k,v in b.items() if k!='visual'}
(E/'script.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n');(E/'settings-diagrams.json').write_text(json.dumps(custom,ensure_ascii=False,indent=2)+'\n')
active=[t for t in s['turns'] if t['id'] not in set(s['firstCut']['omitTurnIds'])]
print(json.dumps({'speechUnchanged':True,'activeTurns':len(active),'bodyViews':len({t['visual'].get('viewId') for t in active if t['chapterId']!='opening'}),'svgVariants':len(custom),'layouts':sorted({t['visual']['layout'] for t in active})}))
