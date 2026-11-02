import json,re,copy
from pathlib import Path
P=Path(__file__).parent
script=json.loads((P/'script.json').read_text())
source='../02-external-protection.html'
refs=lambda *a:[{'file':source,'anchor':x} for x in a]
script.update(status='ready-for-generation',description='通信規則とIDPSの動作を分け、経路・暗号化・方向から検査範囲を確かめる。',sourceReferences=refs('idps-settings','idps-detection','idps-coverage','ingress','idps-logs'),chapters=[
 {'id':'opening','title':'Allowなのに検知される？','goal':'許可ログと攻撃検知が両立する問いを共有する。','sourceRefs':refs('idps-detection')},
 {'id':'purpose','title':'本番定義と二つの判断','goal':'通信規則とIDPSの役割、本番とテストの指定値を区別する。','sourceRefs':refs('idps-settings','idps-detection')},
 {'id':'mechanism','title':'AlertとDenyの動き','goal':'並列検知とinline遮断、署名ごとの有効モードを説明する。','sourceRefs':refs('idps-detection')},
 {'id':'coverage','title':'経路・TLS・方向の境界','goal':'FW通過、復号の有無、方向分類と例外から検査範囲を絞る。','sourceRefs':refs('ingress','idps-coverage')},
 {'id':'walkthrough','title':'一つの要求とログを追う','goal':'通常API要求の許可・検知・最終結果を分けて確認する。','sourceRefs':refs('idps-detection','idps-logs')},
 {'id':'check','title':'三つの確認問題','goal':'モード・暗号化・方向の誤解を自分で訂正できる。','sourceRefs':refs('idps-detection','idps-coverage')},
 {'id':'summary','title':'判断を分けて次の層へ','goal':'確認順を振り返り、第11回のAPIM認証と入力検査へ接続する。','sourceRefs':refs('idps-settings','idps-coverage','apim-scope')}
],turns=[])
menu=['判断の分担','環境とモード','署名の動作','検査範囲','例外と関連設定','ログの確認','確認問題','まとめ']
def settings(view,title,section,rows,cols=None,notice=None):
 v={'layout':'settings','viewId':view,'areaLabel':'Firewall Policy / 教材用設定','title':title,'kind':'Firewall Policy','section':section,'menu':menu,'subtitle':'リポジトリ定義・公開設定例から確認する'}
 if cols:
  v['table']={'columns':[{'label':a,'width':b} for a,b in cols],'rows':[{'id':a,'cells':b} for a,b in rows]};v['highlightRows']=[]
 else:v['fields']=[{'id':a,'label':b,'value':c} for a,b,c in rows];v['highlightFields']=[]
 if notice:v['notice']=notice
 return v
views={
 'roles':settings('decision-roles','同じ通信を、別の観点で判断する','判断の分担',[
  ('rules','Network / Application規則','接続条件により Allow / Deny'),('idps','IDPS','既知の攻撃パターンと通信を照合'),('outcome','利用者から見た結果','下流の認証・処理結果も別に確認')]),
 'config':settings('environment-modes','本番とテストの指定値を比較する','環境とモード',[
  ('sku',['SKU','Premium','Standard']),('mode',['idpsMode','Alert','Off']),('override',['signatureOverrides','[]','[]']),('threat',['threatIntelMode','Alert','Alert'])],[('設定',332),('本番 config',320),('テスト config',320)],'稼働環境を取得した画面ではなく、現行定義の比較'),
 'modes':{'layout':'svg','viewId':'idps-modes','heading':'Alertの並列検知とDenyのinline遮断','svg':'diagrams/idps-modes.svg'},
 'signatures':settings('signature-actions','モード名と、署名の有効な動作を分ける','署名の動作',[
  ('signature','署名が見る特徴','攻撃に特徴的なパターンと適用条件'),('mode','ポリシーが Deny の場合','一部署名は既定で Alert Only'),('override','signatureOverrides','本番定義 []：利用者による上書きなし'),('conditions','照合する条件','方向・プロトコル・ポートなど')]),
 'paths':{'layout':'svg','viewId':'coverage-paths','heading':'FW通過と、暗号化の境界を確認する','svg':'diagrams/coverage-paths.svg'},
 'direction':{'layout':'svg','viewId':'direction','heading':'IDPSから見えるIPで方向を分類する','svg':'diagrams/direction.svg'},
 'exceptions':settings('idps-exceptions','署名の上書きと通信のbypass','例外と関連設定',[
  ('range','idpsPrivateRanges','公開params例 []：独自範囲の指定なし'),('override','signatureOverrides','指定した署名の動作を変更'),('bypass','bypassTrafficSettings','指定した通信を IDPS 検査から外す'),('example','公開params例の bypass','[]：通信単位の追加除外なし')]),
 'related':settings('related-settings','似た名前の設定も、判断する対象は別','例外と関連設定',[
  ('idps','IDPS private範囲','Inbound / Outbound / Internal の分類'),('snat','SNAT private範囲','Network規則の送信元変換を判断'),('dns','DNS proxy','DNS問い合わせの中継'),('threat','Threat Intelligence','悪性IP・FQDN等の脅威情報と照合')]),
 'walk':settings('request-walkthrough','通常API要求：AppGWからAPIMへ','判断の分担',[
  ('path','今回選ぶ区間','AppGW → Firewall → APIM / HTTP 80'),('rule','通信規則','必要な接続を Allow'),('idps','本番定義の IDPS','Alert：署名一致を記録'),('final','APIM以降','キー認証・入力検査・処理結果を確認')]),
 'logs':settings('log-evidence','Allowと検知Actionを、別の証拠として読む','ログの確認',[
  ('network','AZFWNetworkRule','Network規則の Allow / Deny'),('app','AZFWApplicationRule','Application規則の Allow / Deny'),('idps','AZFWIdpsSignature','署名・Action・送受信情報'),('join','照合の手掛かり','FWリソースID・UTC時刻・IP / port / protocol'),('notify','IDPS Alert','検知ログの動作。メール通知の設定とは別')]),
 'q1':settings('quiz-alert-deny','確認1：どの判断が通信を止める？','確認問題',[
  ('condition','条件','通常規則 Deny / IDPS Alert'),('a','候補 A','IDPSがAlertなら、通常規則の拒否も解除'),('b','候補 B','通常規則の拒否は、そのまま有効')]),
 'q2':settings('quiz-tls-path','確認2：どこまで読めると言える？','確認問題',[
  ('condition','条件','Portal向け HTTPS / FW通過 / TLS復号なし'),('a','候補 A','FWを通るので、暗号化内部の本文も検査'),('b','候補 B','暗号化内部の本文を読めるとは扱わない')]),
 'q3':settings('quiz-direction','確認3：利用者の居場所と方向は同じ？','確認問題',[
  ('condition','条件','AppGW → APIM / private IP同士'),('range','private範囲の指定','idpsPrivateRanges=[] / 既定 RFC 1918'),('a','候補 A','Internet利用者の要求なので Inbound'),('b','候補 B','FWが見る区間は Internal')]),
 'summary':settings('lesson10-summary','経路と設定から、判断の意味を確かめる','まとめ',[
  ('rule','通信規則と IDPS','Allow / Deny と Alert / Deny を区別'),('scope','検査範囲','FW通過・TLS境界・方向・署名条件'),('evidence','確認する証拠','定義・ログAction・下流の最終結果'),('next','第11回の問い','APIMはどの利用契約で受ける？')])
}
# Captions preserve every character; chunks have at most two lines.
def chunks(text):
 parts=re.findall(r'[^。！？]+[。！？]?|[。！？]',text)
 segments=[]
 for part in parts:
  while len(part)>64:
   cuts=[m.end() for m in re.finditer('、',part[:64])]
   cut=cuts[-1] if cuts and cuts[-1]>=24 else 64
   segments.append(part[:cut]);part=part[cut:]
  if part:segments.append(part)
 out=[]
 for part in segments:
  if len(part)<=34:lines=[part]
  else:
   target=min(34,(len(part)+1)//2)
   cut=target
   choices=[i+1 for i,c in enumerate(part[:34]) if c in '、：／ ' and i+1>=max(12,target-9)]
   if choices:cut=choices[-1]
   if len(part)-cut>34:cut=len(part)-34
   lines=[part[:cut],part[cut:]]
  out.append({'caption':part,'lines':lines,'weight':len(part)})
 assert ''.join(x['caption'] for x in out)==text
 return out
pronunciations={
 'Threat Intelligence':'スレットインテリジェンス','signatureOverrides':'シグネチャーオーバーライド','bypassTrafficSettings':'バイパストラフィック設定','idpsPrivateRanges':'アイディーピーエスのプライベート範囲','snatPrivateRanges':'エスナットのプライベート範囲','AZFWIdpsSignature':'エーゼットエフダブリュー・アイディーピーエス・シグネチャー','AZFWApplicationRule':'エーゼットエフダブリュー・アプリケーションルール','AZFWNetworkRule':'エーゼットエフダブリュー・ネットワークルール','Alert and Deny':'アラートアンドデナイ','Alert Only':'アラートオンリー','Application':'アプリケーション','Network':'ネットワーク','Private Endpoint':'プライベートエンドポイント','AppGW':'アップジーダブリュー','Firewall':'ファイアウォール','Premium':'プレミアム','Standard':'スタンダード','Inbound':'インバウンド','Outbound':'アウトバウンド','Internal':'インターナル','RFC 1918':'アールエフシー、いちきゅういちはち','TLS':'ティーエルエス','HTTPS':'エイチティーティーピーエス','HTTP':'エイチティーティーピー','APIM':'エーピーアイエム','IDPS':'アイディーピーエス','SNAT':'エスナット','DNS':'ディーエヌエス','FQDN':'エフキューディーエヌ','TCP':'ティーシーピー','RST':'リセット','UTC':'ユー・ティー・シー','Allow':'アロー','Alert':'アラート','Deny':'デナイ','Off':'オフ','SKU':'エスケーユー','config':'コンフィグ','params':'パラムズ','bypass':'バイパス','inline':'インライン','Portal':'ポータル','gRPC':'ジーアールピーシー','Foundry':'ファウンドリー','PE':'ピーイー','IP':'アイピー','API':'エーピーアイ','WAF':'ワフ','Bicep':'バイセップ','port':'ポート','protocol':'プロトコル','C2':'シーツー','80':'はちじゅう','443':'よんよんさん','[]':'空の配列'}
def speech(text):
 for a,b in sorted(pronunciations.items(),key=lambda x:-len(x[0])):text=text.replace(a,b)
 return text
chapter='opening';view=None
def group(ch,v):
 global chapter,view
 chapter=ch;view=v

def t(who,text,highlight=(),delivery=None):
 n=len(script['turns'])+1;v=copy.deepcopy(views[view] if view else {'layout':'hologram','heading':'Firewallの許可とIDPSの検知'})
 if v['layout']=='settings':v['highlightRows' if 'table'in v else 'highlightFields']=list(highlight)
 c=chunks(text)
 script['turns'].append({'id':f'lesson10-turn-{n:03d}-{who}','speaker':who,'chapterId':chapter,'speech':speech(text),'caption':text,'subtitleLines':c[0]['lines'],'subtitleChunks':c,'gapAfterSeconds':0.16 if who=='mako' else 0.14,'delivery':delivery or ('視聴者の疑問を自然に確かめる。' if who=='mako' else '相手の問いに落ち着いて答える。'),'visual':v})

t('mako','FirewallのログはAllow。でも、攻撃を検知したログもある。これ、同じ通信なら矛盾していませんか？')
t('manabu','許可した条件と、見つけた危険を分ければ説明できます。管理者が知りたいのは、何を根拠に通し、何を記録したかです。')
t('ikumi','今日は通信規則とIDPSを分け、経路、暗号化、方向から、検査範囲を確かめましょう。')
t('mako','ログの単語だけで結論を出さず、通信の条件を見るんですね。二つの判断を整理します。')

group('purpose','roles')
t('mako','通信規則とIDPSは、どちらも危ない通信を止める設定ですか？')
t('ikumi','役割を分けましょう。NetworkやApplication規則は、接続元と宛先、ポートやFQDNなど、規則の条件から許可や拒否を判断します。',['rules'])
t('manabu','業務に必要な接続の条件ですね。許可先への通信にも、攻撃の特徴が含まれる可能性は残ります。',['rules'])
t('ikumi','そこでIDPSが通信の特徴を、既知の攻撃パターンである署名と照合します。接続の許可と、攻撃らしい特徴の検知は、違う観点です。',['idps'])
t('mako','なるほど。Allowは、安全な業務要求として最後まで成功した、という印でもないんですね。')
t('manabu','はい。次のAPIMで認証に失敗する場合もあります。接続条件、攻撃の検知、下流の結果を分けて確認します。',['outcome'])

group('purpose','config')
t('mako','今のegakumiは何をする設定ですか？本番とテストを並べて見ましょう。')
t('ikumi','本番のconfigはFirewallがPremiumで、IDPSのモードはAlertです。署名の一致を検知して記録する指定で、IDPSによる遮断は選んでいません。',['sku','mode'])
t('mako','テストはStandardとOffですね。本番と同じ検査の、簡略版でしょうか？')
t('ikumi','いいえ。テストはIDPS機能を使いません。入力検証でStandardのOffを取り除き、BicepもPremiumの場合だけIDPSのプロパティを構成します。',['sku','mode'])
t('manabu','Standardのテスト環境では、本番IDPSの実検知や遮断を確認できません。検証できる範囲が違うんですね。',['mode'])
t('ikumi','ここに表示するのは現行のリポジトリ定義です。実際の配備値や実測結果とは分けます。署名の上書きは両方とも空で、Threat Intelligenceも別にAlertです。',['override','threat'])

group('mechanism','modes')
t('mako','最初の疑問へ戻ります。上段のAlertでは、通常規則のAllowとIDPSの検知が、どう両立するのでしょう？')
t('ikumi','上段の二つの枝が本番定義です。通信規則とIDPSの検知が並列に動きます。IDPSの合格を待って送る、直列の関門ではありません。')
t('manabu','接続条件でAllowとなり、別の観点で署名に一致した、という二つの記録が残るわけですね。')
t('mako','では、通常規則がDenyでも、IDPSがAlertなら、検知だけで通してくれるのでしょうか？')
t('ikumi','IDPSのAlertは、通常規則のDenyを解除しません。同じ通信に検知ログがあっても、規則の拒否は有効です。')
t('mako','Alertは、IDPS自身が検知をどう扱うかの指定なんですね。')
t('mako','次は下段です。IDPSをDenyへ変える場合、どの位置で通信を止めるのですか？')
t('ikumi','下段は機能の説明です。通常規則の処理後に、IDPSが通信経路の中でinlineに動きます。Deny対象の署名に一致すると、記録して通信を遮断できます。')
t('manabu','後段のIDPSで止まる可能性がある。通常規則のAllowログだけでは、最終的な通過を保証できないんですね。')
t('ikumi','その通りです。configのDenyは、製品の画面でいうAlert and Denyに対応します。ただし、ポリシーのモード名だけで、すべての署名が遮断すると決めないでください。')
t('mako','拒否なら、WAFのようにHTTPのエラー応答が返る、と予想してしまいそうです。見え方も同じですか？')
t('ikumi','IDPSの遮断はTCPのRSTを返さず、timeoutなどで見える場合があります。エラー名だけで、拒否した機能を特定しないでください。')

group('mechanism','signatures')
t('mako','Denyでも全部の署名が遮断するわけではない。署名の何を確認すればよいですか？')
t('ikumi','署名は脆弱性を突く通信や、マルウェアの指令通信であるC2などの特徴を表します。Microsoftが管理、更新する署名を、適用条件に沿って照合します。',['signature','conditions'])
t('manabu','既知の特徴への一致を見る判断ですね。検知がないことだけを、安全の保証にはできません。',['signature'])
t('ikumi','ポリシーがDenyでも、一部の署名は既定でAlert Onlyです。署名ごとの有効モードと、変更できる範囲を確認して、記録なのか遮断なのかを判断します。',['mode'])
t('mako','本番のsignatureOverridesが空なら、署名は一つも有効でない、という読み方でしょうか？')
t('ikumi','利用者によるモードの上書きがない、という意味です。署名をなくす設定ではありません。実際の署名条件や更新も含めて確認します。',['override'])

group('coverage','paths')
t('mako','この図の三つの区間で、Firewallは同じように本文まで検査できますか？')
t('ikumi','最初に実際の経路を選びます。上段の通常APIは、AppGWからFirewallを通り、APIMへHTTPの80番で進みます。この区間は平文なので、条件に合う署名で内容を検査できます。')
t('manabu','利用者からはHTTPSでも、AppGWが終端した後の接続を見るんですね。区間ごとに暗号化の状態を確認します。')
t('mako','中段のPortal向けHTTPSはFirewallを通ります。同じ機器を通るのなら、こちらも本文を読めますか？')
t('ikumi','現行のFirewallにはTLS復号検査の設定がありません。PortalやgRPC、HTTPSの外向き通信では、暗号化内部の本文をIDPSが読めるとは扱いません。')
t('manabu','Premiumを選ぶだけで、TLSの復号に必要な設定まで整うわけではないんですね。')
t('mako','下段はAPIMからFoundryのPEへ直接進んでいます。この経路にも、FirewallのIDPSが関わるのでしょうか？')
t('ikumi','Firewallを通らないPEへの直接経路は、このFirewallのIDPSでは検査しません。まず通るか、次に暗号化内部が見えるか、その先に署名条件、という順で確認します。')

group('coverage','direction')
t('mako','次は方向です。Internetの利用者から来た通常API要求なら、IDPSでも必ずInbound扱いですか？')
t('ikumi','Firewallから見える通信のIPで考えます。独自のprivate範囲を指定しない公開例では、既定のRFC 1918を使い、送信元と宛先がprivateかどうかで方向を分類します。')
t('manabu','観測する接続の両端を見るんですね。AppGWが代理接続を作ることが、ここで効いてきます。')
t('ikumi','図の下段にあるprivate同士がInternalです。公開例のAppGWからAPIMはこの分類です。元の利用者がInternet上でも、Inbound専用の署名がすべて適用されるとは限りません。')
t('mako','図の上二段では、公開側からprivate側がInbound、private側から公開側がOutbound。署名は、その方向の条件も見るわけですね。')
t('ikumi','はい。idpsPrivateRangesの空配列は独自範囲を指定しない意味で、検査対象が空という意味ではありません。企業で独自範囲を変える場合も、経路と署名の適用を合わせて確認します。')

group('coverage','exceptions')
t('mako','誤検知があったら、どこで例外を作るのでしょう？前回のWAFで扱った除外と、同じ粒度ですか？')
t('ikumi','設定ごとに範囲が違います。signatureOverridesは、指定した署名の動作を変更します。公開の本番定義は空で、利用者による署名モードの上書きはありません。',['override'])
t('manabu','その署名が見る通信へ影響しますね。一要求だけの調整と決めつけず、ほかの正規通信への影響も考えます。',['override'])
t('ikumi','bypassTrafficSettingsは、指定した通信をIDPS検査から外します。公開params例は空で、通信単位の追加除外はありません。署名のモード変更と、通信の検査除外は別です。',['bypass','example'])
t('mako','WAFで扱った、パスと引数と原因ルールを絞る除外を、そのまま移して考えることはできないんですね。')
t('manabu','はい。再現条件を調べ、変更の対象と残る検査を説明できる範囲に絞ります。原因を調べず、例外だけを増やさないようにします。',['override','bypass'])

group('coverage','related')
t('mako','private範囲という名前を見ると、送信元を書き換えるSNATの設定も思い出します。同じものですか？')
t('ikumi','別です。IDPSのprivate範囲は方向分類、snatPrivateRangesはNetwork規則の送信元変換を判断します。IDPSの範囲を変えても、SNATを設定したことにはなりません。',['idps','snat'])
t('ikumi','公開例のAppGWからAPIMは、private宛てのNetwork規則なので、送信元AppGWのIPを保持します。Application規則には別のSNAT動作があるため、一律に説明しません。',['snat'])
t('mako','DNS proxyやThreat Intelligenceも並んでいますが、IDPSのモードを変えると、一緒に変わるのでしょうか？')
t('ikumi','DNS proxyは名前解決の問い合わせを中継する設定です。Threat Intelligenceは悪性IPやFQDNなどの脅威情報と照合します。どちらもIDPSの署名検知と別に読みます。',['dns','threat'])
t('manabu','本番もテストもThreat IntelligenceはAlert。IDPSをDenyへ変えても、そちらの動作モードは自動では変わらないんですね。',['threat'])

group('walkthrough','walk')
t('mako','ここまでの条件を、一つの要求でつなげましょう。通常APIで、WAFを通過してAppGWからAPIMへ向かう区間を選びます。')
t('ikumi','選んだ区間はHTTPの80番です。経路はAppGW、Firewall、APIM。通信規則は必要な接続をAllowします。方向はprivate同士なのでInternalとして考えます。',['path','rule'])
t('mako','そこで条件に合う署名が一致したとします。今の本番定義はAlertだから、検知ログを残す。要求はどうなりますか？')
t('ikumi','IDPSの検知だけでは遮断しません。通常規則が許可していればAPIMへ進めます。ただし、認証や入力検査で拒否される可能性は、その先に残っています。',['idps','final'])
t('manabu','もし同じ条件でIDPSのDenyを有効にし、その署名も遮断対象なら、通常規則のAllow後に止まる場合があります。モードを変えるときは正規通信への影響を確認するわけですね。',['rule','idps'])
t('ikumi','はい。無害な試験入力、ログのAction、利用側のtimeoutや遅延も確認します。今日は設定と機能の説明で、稼働環境へDenyを適用して検証した結果ではありません。',['idps'])

group('walkthrough','logs')
t('mako','調査では、Allowログと検知ログをどう並べて読みますか？')
t('ikumi','Network規則とApplication規則のログで、どの接続がAllowかDenyかを確認します。IDPSは専用のログで署名とActionを読みます。異なる判断の証拠として扱います。',['network','app','idps'])
t('manabu','同じ時刻の検知でも、目の前の要求とは限りませんね。照合の手掛かりは何ですか？')
t('ikumi','FWリソースID、UTC時刻、送受信IP、ポート、プロトコルを照合します。IDPSのテーブルに共通のTransactionIdがあるとは扱わず、AppGWの代理接続も考慮します。',['join'])
t('mako','Alertという名前だと、担当者にメールが届く設定まで含んでいるようにも聞こえます。そこも区別が必要ですか？')
t('ikumi','必要です。IDPSのAlertは検知ログの動作モードです。通知の設定とは別で、現行のログアラート定義にはIDPS検知用クエリがありません。記録されたことと通知されたことを分けます。',['notify'])

group('check','q1')
t('mako','確認問題です。通常規則はDeny、IDPSはAlert。この通信は、IDPSが検知だけのモードだから通せるでしょうか？候補を選んでみてください。',delivery='問題の条件を落ち着いて提示し、候補を考える短い間を取る。')
t('manabu','候補Bです。IDPSのモードと、通常規則の許可は別なので、拒否はそのまま有効です。',['b'])
t('ikumi','正解です。Alertは通常規則のDenyを解除しません。IDPSをDenyにすると、規則がAllowでも、遮断対象の署名に一致して止まる場合があります。',['b'])
t('mako','通信規則のDenyかIDPSのDenyか、主語と一緒に読む。最初の疑問も解けました。',['b'])

group('check','q2')
t('mako','次の問題です。Portal向けHTTPSはFirewallを通り、TLSの復号設定はありません。IDPSは暗号化内部の本文も検査できる、と言えますか？',delivery='条件を一つずつ示して答えを考える間を取る。')
t('manabu','候補Bです。通過しただけでは、暗号化内部が見えるとは言えません。',['b'])
t('ikumi','はい。通常APIのHTTP区間とは条件が違います。PEの直接経路は、Firewallを通らない点も確認しましょう。',['b'])
t('mako','まず経路、次にその区間のTLS境界。サービス名が同じだから同じ検査、とまとめないことが大切なんですね。',['b'])

group('check','q3')
t('mako','最後です。Internetの利用者から始まった要求が、AppGWからAPIMへprivate IP同士で進みます。公開例の範囲設定なら、IDPSではどの方向でしょう？',delivery='元の利用者と観測区間を区別する問いとして自然に提示する。')
t('manabu','候補BのInternalです。Firewallが見る区間の送信元と宛先を選ぶので、利用者がInternetにいるという情報だけではInboundにできません。',['b'])
t('ikumi','正解です。空配列は独自のprivate範囲を指定しない意味で、既定のRFC 1918を使います。その方向と、各署名の方向条件を組み合わせて検査範囲を確認します。',['range','b'])
t('mako','方向は観測する接続から考える。元の利用者からの一本の通信だと思い込まないようにします。',['b'])

group('summary','summary')
t('mako','今日の到達点を振り返ります。通信規則のAllowやDenyと、IDPSのAlertやDenyを、どのように説明すればよいでしょう？')
t('manabu','通信規則は接続条件の許可と拒否。IDPSは既知の攻撃の特徴を検知し、モードと署名の動作に応じて記録や遮断を行います。二つの判断を分けて説明します。',['rule'])
t('ikumi','検査範囲は、Firewallを通るか、暗号化内部が見えるか、方向や署名条件に合うかで確かめます。範囲設定とSNAT、署名の上書きと通信除外も別です。',['scope'])
t('manabu','そしてAllowや検知の一行だけで業務成功を断定せず、定義とログのAction、下流の結果を合わせます。本番とテスト、定義と実測の区別も忘れないようにしましょう。',['evidence'])
t('mako','次回はAPIMです。届いた要求を、どのサブスクリプションの要求として受け、Operationやモデルの契約をどう確かめるのでしょうか？',['next'])
t('ikumi','第11回では、入口の認証と入力検査、ポリシーが動く順番を追います。今日選んだ通常API要求が、その先で何を確かめられるのか、一緒に見ていきましょう。',['next'])

# Keep Latin product words intact at these reviewed subtitle boundaries.
line_overrides = {
 'lesson10-turn-057-ikumi': ['公開例のAppGWからAPIMは、private宛ての', 'Network規則なので、送信元AppGWのIPを保持します。'],
 'lesson10-turn-058-mako': ['DNS proxyやThreat Intelligenceも', '並んでいますが、IDPSのモードを変えると、'],
}
for turn in script['turns']:
 if turn['id'] in line_overrides:
  lines = line_overrides[turn['id']]
  assert ''.join(lines) == turn['subtitleChunks'][0]['caption']
  turn['subtitleChunks'][0]['lines'] = lines
  turn['subtitleLines'] = lines

# Final review of every subtitle chunk: never split a Latin word.
chunk_line_overrides = {
 ('lesson10-turn-014-ikumi', 2): ['入力検証でStandardのOffを取り除き、Bicepも', 'Premiumの場合だけIDPSのプロパティを構成します。'],
 ('lesson10-turn-061-mako', 1): ['通常APIで、WAFを通過して', 'AppGWからAPIMへ向かう区間を選びます。'],
}
for turn in script['turns']:
 for (turn_id, index), lines in chunk_line_overrides.items():
  if turn['id'] == turn_id:
   assert ''.join(lines) == turn['subtitleChunks'][index]['caption']
   turn['subtitleChunks'][index]['lines'] = lines
 if turn['id'] == 'lesson10-turn-056-ikumi':
  old = turn['subtitleChunks'][1]
  captions = ['IDPSのprivate範囲は方向分類、', 'snatPrivateRangesはNetwork規則の送信元変換を判断します。']
  assert ''.join(captions) == old['caption']
  replacements = [
   {'caption': captions[0], 'lines': [captions[0]], 'weight': len(captions[0])},
   {'caption': captions[1], 'lines': ['snatPrivateRangesはNetwork規則の', '送信元変換を判断します。'], 'weight': len(captions[1])},
  ]
  turn['subtitleChunks'][1:2] = replacements

for t0 in script['turns']:
 assert max(map(len,[l for c in t0['subtitleChunks'] for l in c['lines']]))<=34
 assert len(t0['subtitleLines'])<=2
 assert 'TODO' not in json.dumps(t0,ensure_ascii=False)
(P/'script.json').write_text(json.dumps(script,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'turns':len(script['turns']),'captionChars':sum(len(t['caption']) for t in script['turns']),'speechChars':sum(len(t['speech']) for t in script['turns']),'bodyViewIds':list(dict.fromkeys(t['visual']['viewId'] for t in script['turns'] if t['chapterId']!='opening'))},ensure_ascii=False,indent=2))
