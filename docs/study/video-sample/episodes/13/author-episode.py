#!/usr/bin/env python3
"""Episode 13 authored source, using only episode-local output."""
from pathlib import Path
import json,re,copy
HERE=Path(__file__).resolve().parent
s=json.loads((HERE/'script.json').read_text())
refs=lambda *anchors:[{'file':'../02-external-protection.html','anchor':a} for a in anchors]
s['status']='authored-review-pending'
s['description']='一つの文書API要求を手掛かりに、到達段階・相関キー・APIMの実行位置を照合し、原因に合う正本の変更、正常系と拒否系の検証、復元まで追います。'
s['learningObjectives']=[
'要求の到達段階・相関キー・実行位置を照合し、正本の変更、検証、復元へつなげられる。',
'Allow・accepted・匿名401を業務成功の証拠にせず、最終応答と適用設定を照合できる。',
'正当な拒否では呼出側を修正し、誤検知や契約変更では原因の正本と関連付けを最小範囲で直せる。']
s['sourceReferences']=refs('observability','logs','idps-logs','apim-observability','maintenance','sources','summary')
s['chapters']=[
 {'id':'opening','title':'今回の問い','goal':'失敗応答と通過の痕跡が共存する理由を問う。','sourceRefs':refs('summary')},
 {'id':'arrival','title':'到達段階と相関キー','goal':'層ごとのキーと通信情報で同じ要求をたどる。','sourceRefs':refs('observability')},
 {'id':'execution','title':'400とacceptedを読み解く','goal':'API共通処理とOperation固有検査の実行位置を区別する。','sourceRefs':refs('apim-observability','summary')},
 {'id':'change','title':'原因に合う正本を変える','goal':'正当拒否と誤検知を分け、正本・生成物・有効値をそろえる。','sourceRefs':refs('maintenance','sources')},
 {'id':'verify','title':'正常系・拒否系・復元','goal':'成功と保護の維持、復元条件を対応付ける。','sourceRefs':refs('maintenance')},
 {'id':'check','title':'三つの確認問題','goal':'到達、accepted、復元を自分の言葉で判断する。','sourceRefs':refs('summary','maintenance')},
 {'id':'summary','title':'一要求から変更と復元へ','goal':'確認順を振り返り、第14回のPortal管理へつなぐ。','sourceRefs':refs('summary')},
]
views={}
def view(key,title,section,rows,notice,kind='要求調査',cols=('確認する項目','値・読み方')):
 views[key]={'layout':'settings','viewId':'lesson13-'+key,'areaLabel':'調査と保守 / 教材用の設定メニュー','title':title,'kind':kind,'section':section,'menu':['到達段階','相関キー','実行位置','正本と適用','検証と復元'],'subtitle':'2026-11-01時点の資料・リポジトリ定義に基づく教材','notice':notice,'table':{'columns':[{'label':cols[0],'width':330},{'label':cols[1],'width':642}],'rows':[{'id':r[0],'cells':list(r[1:])} for r in rows]},'highlightRows':[]}
view('purpose','状態コードから、要求の処理結果へ','到達段階',[
 ('route','入口と経路','通常API・Portal・gRPCで経路を選ぶ'),
 ('arrival','到達段階','前段で止まれば、後段へ届かない'),
 ('decision','処理結果','Allow・acceptedと最終成功を分ける'),
 ('change','変更の判断','原因の正本 → 検証 → 復元')], '調べる単位は、一つの要求')
view('collect','問い合わせで揃える手掛かり','到達段階',[
 ('time','対象環境・UTC時刻','時差と検索時間幅を確認'),
 ('contract','要求の契約','API・Operation・パス・方式・形式・サイズ'),
 ('result','応答と追跡情報','状態コード・apim-request-idなど'),
 ('safe','記録する範囲','秘密や実業務本文を証跡へコピーしない')], '時刻・要求・応答を対応付ける')
view('waf','AppGW内の同じ要求を照合する','相関キー',[
 ('access','AGWAccessLogs','TransactionId・HttpStatus・ServerStatus'),
 ('waf','AGWFirewallLogs','同じTransactionId・PolicyId・RuleId・Action'),
 ('cause','原因規則','949110の前にある一致規則と対象項目'),
 ('block','到達判断','MatchedとBlocked、アクセス結果を照合')], '403だけでWAF遮断と決めない',kind='WAF / AppGW')
view('fw','Firewallの通信情報を照合する','相関キー',[
 ('key','照合する手掛かり','FWリソースID・UTC時刻・IP / port / protocol'),
 ('rule','Network / Application','規則のAllow / Denyを確認'),
 ('idps','AZFWIdpsSignature','SignatureId・Action・送受信情報'),
 ('proxy','AppGWの代理接続','元クライアントIPと同じとは限らない')], 'IDPSテーブルにTransactionId列はない',kind='Firewall')
view('apim-key','APIMの要求IDから処理結果をたどる','相関キー',[
 ('id','応答のapim-request-id','GatewayログのCorrelationIdと照合'),
 ('target','対象を絞る情報','APIMリソース・時刻・API・Operation'),
 ('response','最終応答と下流応答','ResponseCode / BackendResponseCode'),
 ('error','例外・Core trace','LastErrorと各phaseを実行位置に照合')], '各層に共通のTransactionIdがあるとは扱わない',kind='API Management')
view('phases','Core traceは、どこまで進んだ印か','実行位置',[
 ('accepted','accepted','API inbound末尾。Operation固有検査より前にも残る'),
 ('rejected','rejected','Coreの拒否を示す記録。すべての拒否を保証しない'),
 ('backend','backend-response','下流応答の記録。業務成功は最終結果と照合'),
 ('error','policy-error','ポリシー例外。LastErrorなどと照合')], '通過した段階と、未確認の段階を分ける',kind='API Management')
view('document','文書API：400でもacceptedが残る順序','実行位置',[
 ('base','1 / Operationの<base />','API共通処理へ進む'),
 ('common','2 / API共通処理','共通制約 → 利用枠 → 下流認証準備'),
 ('accepted','3 / accepted trace','API inbound末尾で記録'),
 ('guard','4 / 文書固有の形式検査','<base />から戻った後に検査し、400を返し得る'),
 ('business','5 / 下流の業務処理','acceptedだけでは到達・成功を判断しない')], '資料22節の説明例。稼働環境の実測ではない',kind='文書API / 実行順')
view('response','状態コードと、拒否した処理を分ける','実行位置',[
 ('auth','401 / 入口の403','サブスクリプション検証・入口条件など'),
 ('limits','429 / トークン枠の403','回数・TPM・quotaと下流応答を区別'),
 ('input','入力拒否 / 400など','形式・専用ガードと最終応答を照合'),
 ('fault','500 / 503など','構成不足・取得障害・下流失敗を切り分け')], 'コードは手掛かり。原因の確定はログと実装から',kind='API Management')
view('privacy','調査に残す情報と、検査する情報','相関キー',[
 ('waf','WAFログのscrubbing','記録する値をマスク。検査除外とは別'),
 ('query','Accessログのquery除去','Workspace変換で行う別の設定'),
 ('apim','APIM診断の記録範囲','要求・応答本文と認証ヘッダーを保存しない'),
 ('missing','ログが見つからない場合','未到達・SKU・診断設定・検索時間を確認')], '調査のために秘密や実業務本文を複製しない')
view('branch','設定を変える前に、正規の要求かを確かめる','正本と適用',[
 ('contract','正規契約との照合','パス・方式・形式・サイズ・有効設定'),
 ('valid-deny','契約外の入力を正当に拒否','呼出側のMIMEや本文を修正'),
 ('false-positive','正規データの誤検知・契約変更','原因に合う正本と関連付けを検討'),
 ('plan','変更前の保存','環境・対象経路・識別子・必要な設定を記録')], '通ればよい、だけでは保護の維持を説明できない')
view('waf-source','WAF：原因と範囲に合う正本へ戻す','正本と適用',[
 ('data','データ項目の除外','waf-data-exclusions.json'),
 ('raw','raw本文の補完','waf-raw-body-controls.json'),
 ('bind','経路への関連付け','infra/main.bicep・application-gateway.bicep'),
 ('scope','変更の範囲','完全パス・引数・原因ルールと共通保護')], '原因規則を確認してから、対象を絞る',kind='WAF / 正本')
view('apim-source','APIM：定義・生成物・有効値を照合する','正本と適用',[
 ('definition','API定義・モデルの契約','apps/gateway/apim/apis/*.jsonなど'),
 ('policy','XML・fragment','service / gateway / protocolと<base />'),
 ('connection','Named Value・プロファイル','接続有効状態・secret参照・30秒キャッシュ'),
 ('apply','共通XMLの統合・読み戻し','API登録後にETag付き。独自処理との整合を確認')], '基盤の配備と、API登録を別々に確認する',kind='API Management / 正本')
view('fw-source','FW：署名の扱いと経路をそろえる','正本と適用',[
 ('config','環境設定・企業固有入力','SKU・idpsMode・署名override・bypass'),
 ('range','変更の影響範囲','署名overrideと通信bypassを分ける'),
 ('route','経路・Policy・診断','network.bicepと関連付け・診断設定'),
 ('sku','実検知・遮断の試験','Premiumで確認。Standardでは確認できない')], '構成テストの合格を、実検知の成功と混同しない',kind='Firewall / 正本')
view('local','計画から有効設定の確認まで','検証と復元',[
 ('plan','ローカルの計画・差分','正本と生成XML、対象経路を確認'),
 ('local','ローカル検証の限界','Azureの有効値や実サービス成功とは別'),
 ('etag','適用する直前の照合','ETag・対象環境・独自処理との不一致'),
 ('readback','配備後の読み戻し','要求に実際に適用される設定と関連付け')], '秘密を含む全構成を、そのまま証跡へ出力しない')
view('tests','通ることと、拒否できることを両方確かめる','検証と復元',[
 ('success','正常系','正規入力を、認証付きで業務成功まで確認'),
 ('deny','拒否すべき入力','攻撃対照・契約外入力の拒否を確認'),
 ('boundary','境界と隣接範囲','サイズ境界・query / header・隣接パス'),
 ('count','観測する結果','最終応答・各層ログ・計数時点・遅延')], '変更した経路・プロトコル・署名版に試験を合わせる')
view('restore','復元も、要求への適用まで確認する','検証と復元',[
 ('condition','復元の判断条件','正常利用の失敗・保護の低下などを事前に決める'),
 ('source','戻す対象','正本・生成物・有効設定・関連付け'),
 ('waf','WAFの経路関連付け','profile削除だけでは既存関連付けが撤去されない'),
 ('again','復元後の確認','読み戻し、正常系と拒否系を再確認')], '戻したつもり、を有効設定と結果で確かめる')
view('quiz-arrival','確認1：WAFでBlocked、APIMログがない','到達段階',[
 ('question','いま分かっている事実','WAFでBlockedの記録。APIMログが見つからない'),
 ('a','選択肢A','直ちにAPIMの診断設定を障害と決める'),
 ('b','選択肢B','同じ要求の停止と、経路・時間・診断を照合'),
 ('scope','確認するキー','TransactionId・原因規則・適用PolicyId')], '質問中は選択肢の答えを強調しない')
view('quiz-accepted','確認2：acceptedと計数があるのに400','実行位置',[
 ('question','いま分かっている事実','文書APIの最終応答400。acceptedと計数の痕跡'),
 ('a','選択肢A','解析サービスの業務処理が成功した'),
 ('b','選択肢B','共通処理後に、文書固有検査で拒否し得る'),
 ('next','次に確かめること','Operation固有ガード・最終応答・入力契約')], 'acceptedの実行位置から判断する')
view('quiz-restore','確認3：正規入力が通ったら変更完了か','検証と復元',[
 ('question','いま分かっている事実','WAF変更後、認証付きの正規入力が成功した'),
 ('a','選択肢A','成功が一度確認できたので完了'),
 ('b','選択肢B','拒否系・境界・隣接範囲と有効設定も確認'),
 ('restore','復元の確認','正本と経路関連付けを戻し、再試験する')], '成功と、必要な保護の維持を別々に確かめる')
view('summary','一つの要求を、変更と復元まで説明する','検証と復元',[
 ('route','1 / 経路と到達段階','層ごとのIDと通信情報で照合'),
 ('position','2 / 判断と実行位置','Allow・acceptedと最終結果を区別'),
 ('change','3 / 正本と有効設定','原因に合う範囲を変更し、読み戻す'),
 ('verify','4 / 正常系・拒否系・復元','成功と保護、戻した後の適用を確認')], '次回：Portalでは誰が、何を管理できるのか')
# Each line is an authored conversational turn: speaker | caption | highlighted row IDs.
groups=[
('opening',None,'''
mako|エラーなのに、途中のログには「受け付けた」とあります。これって、どちらを信じればいいんでしょう？|
manabu|両方が正しいことがありますよ。大事なのは、要求がどこまで進んで、最後に何が起きたかです。|
ikumi|今日は文書APIの一つの要求を追って、ログの読み方から、安全な変更と復元までつなげましょう。|
mako|通った印と、成功した証拠は別。そこを見分けるんですね。|
'''),
('arrival','purpose','''
mako|前回まで、WAFやFirewall、APIMの検査を見てきました。調査するときも、その順番で全部のログを探せばよいですか？|
ikumi|まず、その要求の入口と経路を選びます。通常API、Portal、gRPCで、通る検査は同じではありません。経路が違えば、探すログも変わります。|route
manabu|調べる単位を、一つの要求に絞るんです。別の時刻や別の利用者の記録を混ぜると、通過と拒否の話がかみ合わなくなります。|arrival
ikumi|そして、前段で止まれば後段には届きません。各層が残した印と最終応答を順にそろえ、設定を変える必要があるのかを判断します。|decision,change
'''),
('arrival','collect','''
mako|では問い合わせを受けたら、最初に何を揃えますか。画面には「APIが失敗した」とだけ書かれていることもあります。|
ikumi|対象環境と時刻、APIとOperation、パス、方式、形式、サイズを確認します。時刻はUTCに合わせ、応答コードや要求IDも手掛かりにします。|time,contract
mako|日本時間の報告を、そのままUTCのログで探してしまうと、同じ要求が見つからないんですね。本文も丸ごと預かりますか？|time
manabu|必要なのは契約と結果を照合する情報です。認証値や実業務本文を、そのまま証跡へコピーしません。安全な合成入力で再現できると、検証もしやすくなります。|safe
'''),
('arrival','waf','''
mako|もし応答が403なら、入口のWAFで止まった、と言えますか？|
ikumi|コードだけでは言えません。403はAPIMやアプリからも返ります。AppGWのAccessログで要求を絞り、同じTransactionIdのFirewallログを照合します。|access,waf
mako|同じIDで、どのポリシーと規則が働いたかを見るんですね。規則の一致があれば、遮断したことになりますか？|waf
ikumi|MatchedとBlockedを分け、アクセス結果も確かめます。949110は異常スコアの集計結果です。その番号だけで除外せず、先に一致した原因規則と対象項目まで戻ります。|cause,block
manabu|要求を止めた事実と、何が原因だったかは別々に確認する。そこが曖昧なまま保護を緩めると、必要な検査まで外れてしまいます。|cause
'''),
('arrival','fw','''
mako|次のFirewallも、そのTransactionIdで検索すれば、一続きで見つかるんでしょうか？|
ikumi|IDPSテーブルにTransactionId列はありません。対象FWのリソースID、UTC時刻、送受信IPとポート、プロトコルで通信を絞り込みます。|key
mako|同じ要求でも、層が変わると照合する手掛かりが変わるんですね。送信元IPは利用者のIPを使えばよいですか？|key
ikumi|AppGWの代理接続では、FWから見える送信元は元クライアントと同じとは限りません。経路上の通信として見ます。また、通信規則のAllowと、IDPSのActionを分けます。|proxy,rule,idps
manabu|Denyモードの環境なら、規則のAllowだけで最終通過とは言えません。何を許可し、別の検査が何を判断したかを、同じ通信で照合するわけです。|rule,idps
'''),
('arrival','apim-key','''
mako|APIMまで届いた要求では、利用者が持っている情報と、どのログをつなげますか？|
ikumi|応答のapim-request-idと、GatewayログのCorrelationIdを照合します。対象APIMのリソース、時刻、API、Operationも合わせて、取り違えを防ぎます。|id,target
mako|そこで見つかった状態コードを読めば、下流が失敗したのかも分かりますか？|
ikumi|最終のResponseCodeとBackendResponseCodeを分け、LastErrorやCore traceの各phaseを実行位置に重ねます。WAFのIDやFWの通信情報と、共通のIDだとは扱いません。|response,error
'''),
('execution','phases','''
mako|そのCore traceのacceptedが、今回の疑問でした。「受け付けた」なら、入力検査も終わっているように見えます。|
ikumi|acceptedは、APIのinbound末尾で残る印です。Operation固有の検査より前にも記録されるので、専用検査の通過や業務成功を保証しません。|accepted
mako|では、rejectedやpolicy-errorを探せば、失敗した要求を全部つかまえられますか？|
ikumi|それも、実行位置を確認します。標準認証で先に拒否されると共通traceが残るとは限りませんし、例外ではない応答の返し方もあります。標準ログと合わせて読みます。|rejected,error
manabu|backend-responseも、下流応答を扱う段階の記録です。名前の印象だけで成功を決めず、どの段階の証拠かを言葉にすると整理できます。|backend
'''),
('execution','document','''
mako|資料の文書APIを追いましょう。最終応答は400。でもacceptedと計数の痕跡があります。ログ同士が矛盾しているように感じます。|
ikumi|図の番号一から追ってください。文書OperationのbaseでAPI共通処理へ進みます。そこで共通制約、利用枠、下流認証準備が実行されます。|base,common
mako|その共通処理の終わりでacceptedが残る。文書固有の形式は、まだ検査し終わっていないんですね。|accepted
ikumi|はい。baseから戻った後に文書固有の形式検査があります。ここで不正な形式なら、return-responseで400を返し得ます。先に計数やacceptedがあっても矛盾しません。|guard
mako|ということは、解析サービスが一度成功して、その結果だけ400に変わった、と考えるのも違いますね。|business
manabu|その通りです。下流認証の準備と、下流の業務実行も分けます。acceptedだけでは解析サービスへの到達や成功を言えない。最終応答と、拒否した検査の位置が必要です。|business
'''),
('execution','response','''
mako|この400なら、LastErrorやpolicy-errorが必ず残っているのでしょうか？|
ikumi|return-responseは、例外を起こす処理とは違います。on-errorやLastError、rejected traceが必ずあるとはしません。最終応答と、実装上の専用ガードを照合します。|input
mako|429や403、500や503も、番号だけで原因を一つに決めない、ということですね。|
ikumi|ええ。429ならAPIMの回数制限か、下流応答かを分けます。Retry-AfterやCoreの識別子、発生元も確認します。構成不足や取得障害も、下流の失敗と切り分けます。|limits,fault
'''),
('execution','privacy','''
mako|値がマスクされたログでは、原因の項目まで追えないから、検査も外されているのでしょうか？|
ikumi|WAFログのscrubbingは、記録する値を隠す設定です。検査除外ではありません。Accessログのquery除去は別のWorkspace変換なので、両方を確認します。|waf,query
mako|ログ自体が見つからないときも、未到達とすぐ決めないんですね。|
manabu|未到達のほか、SKU、診断設定、時間範囲を確認します。APIM診断では本文や認証ヘッダーを保存しない設定を維持し、調査のために秘密を複製しないようにします。|missing,apim
'''),
('change','branch','''
mako|原因が文書固有の形式検査なら、400をなくすために、その検査を緩めればよいですか？|
ikumi|まず要求が正規契約に合っているかを確認します。形式やMIME、本文が契約外なら、正当に拒否しています。この場合に直すのは呼出側の入力です。|contract,valid-deny
mako|設定変更が必要なのは、正規データの誤検知や、契約そのものを変更する場合なんですね。|false-positive
manabu|はい。そこからは別の変更判断です。例えば正規入力がWAFの原因規則に誤って一致するなら、環境と適用ポリシー、経路、変更前の識別子を残し、影響範囲を絞ります。|false-positive,plan
'''),
('change','waf-source','''
mako|そのWAFの誤検知を直す場合、Azure上で見つけた設定だけ変えるのでしょうか？|
ikumi|正本に戻します。データ項目の除外はwaf-data-exclusions、raw本文の補完はwaf-raw-body-controlsです。目的が違うので、該当する側の定義を選びます。|data,raw
mako|除外の項目だけ合っていればよいのではなく、どのパスに適用されるかも見るんですね。|bind
ikumi|完全パス、引数、原因ルールの範囲と、共通保護の合成まで確認します。企業管理の既存WAFは自動変更しません。ローカル定義、生成結果、有効設定と関連付けを対応させます。|scope,bind
'''),
('change','apim-source','''
mako|APIMの入力契約を変えるなら、どこから確認しますか？ XMLだけ直せばよいでしょうか。|
ikumi|公開パスや方式、model、profile、サイズなどはAPI定義を確認します。その定義と、service、gateway、protocolのXML、fragment、baseの位置を照合します。|definition,policy
mako|Named Valueやプロファイルの値も、処理に関係するんですね。|connection
ikumi|接続の有効状態とsecret参照、SQLの上限を合わせます。サブスクリプションプロファイルには30秒キャッシュがあり、取得障害は503も考慮します。変更直後の観測時刻も必要です。|connection
manabu|基盤の配備とAPI登録は別に確認します。API登録後に共通XMLをETag付きで統合し、読み戻す。既存の独自処理と不一致なら、そのまま上書きして進めません。|apply
'''),
('change','fw-source','''
mako|FirewallのIDPSで検知されたときも、対象通信を丸ごと外せば解決でしょうか？|
ikumi|署名overrideと通信bypassは影響範囲が違います。環境設定のSKUやidpsMode、署名と企業固有の入力を確認し、経路、Policy、診断設定をそろえます。|config,range,route
mako|テスト環境で構成テストが通っていれば、Denyへの変更も検証済みと言えますか？|
ikumi|Standard環境ではIDPSの実検知や遮断は確認できません。Denyへの変更はPremium環境で正規通信、無害な試験入力、ログのAction、遅延やtimeoutまで確認します。|sku
'''),
('verify','local','''
mako|ここまでの変更案は、適用する前にも確認できますか？|
ikumi|WAFの計画やprobeのdry-run、共通XMLのローカル出力で、正本と生成結果の差分を確認できます。ただし、有効設定の取得やAPIごとの完全な展開、実サービスの成功とは別です。|plan,local
mako|ローカルで整っていることと、稼働中にその設定が使われていることは、分けて確かめるんですね。|readback
manabu|適用直前に対象環境とETag、不一致を照合し、配備後に有効設定と経路への関連付けを読み戻します。記録は必要な範囲に絞り、秘密を含む全構成をそのまま保存しません。|etag,readback
'''),
('verify','tests','''
mako|配備後、正規入力が一度通ったら、修正は完了でしょうか？|
manabu|正常利用が回復したことと、必要な保護が維持されたことは、別々に確かめます。認証付きの正規入力で、業務の最終成功まで確認するのが一つ目です。|success
mako|匿名の要求が401を返すなら、入口を通過できた参考にはなる。でも、機能が成功した証拠にはならないんですね。|success
ikumi|次に、拒否すべき入力とサイズ境界、queryやheader、隣接パスを確認します。変更した経路、プロトコル、署名版に試験を合わせ、範囲外へ保護の穴を広げていないかを見ます。|deny,boundary
manabu|文書APIの例なら、計数の時点と最終応答も確認します。成功したかだけでなく、どこまで処理され、何が計上されたかが、変更前後で説明できるようにします。|count
'''),
('verify','restore','''
mako|もし正規通信に問題が出たとき、元の値を戻せば、それで復元できますか？|
ikumi|復元する条件と対象を、変更前に決めておきます。正本だけでなく、生成物、有効設定、経路関連付けまで戻す対象として残します。|condition,source
mako|設定ファイルからprofileを消せば、WAFのパスとの関連付けも自然に消えるように思いますが。|
ikumi|profileの削除だけでは、既存の経路関連付けは撤去されません。復元時はIaCで明示します。戻した後も読み戻し、正常系と拒否系を再確認して、要求への適用を確かめます。|waf,again
manabu|署名更新で新たな検知が現れることもあります。一度成功した記録を永続的な保証にせず、何を、どの条件で確認したかを残しておきましょう。|again
'''),
('check','quiz-arrival','''
mako|確認問題です。WAFにBlockedがあり、APIMログが見つかりません。直ちにAPIMの診断障害と決めますか。それとも要求の停止と検索条件を照合しますか？|
ikumi|後者です。同じ要求がWAFで止まったと確認できれば、後段へ業務転送されず、APIMログがないことと整合します。TransactionId、原因規則、適用PolicyIdを照合します。|b,scope
mako|403という数字だけで判断せず、同じ要求の遮断を確かめる。それでも、経路や時刻、診断設定を取り違えていないかも見るんですね。|scope
manabu|はい。記録がないことだけを原因の証拠にしない。どこまで届いたと確認できるかを、先に言えるようにするのがポイントです。|b
'''),
('check','quiz-accepted','''
mako|二問目です。文書APIが400を返し、acceptedと計数が残っています。解析サービスの業務成功、と結論してよいでしょうか？|
ikumi|結論できません。Operationのbaseで共通処理とacceptedが先に実行され、その後の文書固有検査で400を返し得ます。計数の痕跡とも矛盾しません。|b
mako|最終応答と専用ガードを確かめて、形式が契約外なら呼出側を直す。acceptedを消すために、保護を緩めるわけではないんですね。|next
manabu|その通りです。通過した段階、まだ確認できない段階、最後の結果を分ければ、次に直す場所も選びやすくなります。|b,next
'''),
('check','quiz-restore','''
mako|最後です。WAF変更後、認証付きの正規入力が成功しました。この一度の成功で変更完了としますか？|
ikumi|拒否系、境界、隣接範囲と有効設定も確認します。必要な保護を維持していることまで確認して初めて、意図した変更だったと判断できます。|b
mako|復元するときも、正本の値だけではなく、実際の関連付けと、戻した後の試験まで必要でしたね。|restore
manabu|ええ。変更の成功条件と復元条件を、同じ要求と対照要求で確かめる。結果を再現できる記録にしておくと、次の保守でも判断を引き継げます。|b,restore
'''),
('summary','summary','''
mako|今日の出発点は、エラーなのに「受け付けた」と残る理由でした。ログの言葉だけでなく、処理の位置を見ると納得できます。|position
ikumi|入口と経路を選び、層ごとのキーで到達を照合します。Allowやacceptedを最終成功にせず、最終応答、下流応答、専用検査の位置を合わせて読みます。|route,position
manabu|正当な拒否なら入力を直す。設定を変えるなら、原因の正本と適用範囲を揃え、正常系と拒否系、復元まで確認する。この流れで、安全に変更を説明できます。|change,verify
mako|次回はPortalです。ログインした人は誰でも、すべてのAPIキーを管理できるのでしょうか。誰が何を管理できるか、役割と状態を追います。|change
ikumi|今回の説明は、資料とリポジトリ定義に基づく教材です。稼働環境の実測結果とは分けて、対象環境の有効設定を確認するときの道筋として使ってください。|verify
'''),
]
# Keep each chunk complete and each line readable; punctuation boundaries are preferred.
def units(t):return sum(.58 if ord(c)<128 else 1 for c in t)
def split_lines(t,limit=39):
 if units(t)<=limit:return [t]
 target=units(t)/2;best=None
 for i in range(1,len(t)):
  if units(t[:i])<=limit and units(t[i:])<=limit:
   cost=abs(units(t[:i])-target)+(0 if t[i-1] in '、。？！' else 3)
   if best is None or cost<best[0]:best=(cost,i)
 if best:return [t[:best[1]],t[best[1]:]]
 raise ValueError('caption chunk too long: '+t)
def chunk_caption(t):
 # 2-4 sentence/phrase chunks for longer dialogue, preserving every character.
 parts=re.findall(r'.*?[。？！]|.+$',t)
 chunks=[];cur=''
 for p in parts:
  if units(p)>65:
   phrases=re.findall(r'.*?[、]|.+$',p)
  else:phrases=[p]
  for ph in phrases:
   if units(cur+ph)>62 and cur:chunks.append(cur);cur=''
   if units(ph)>75:
    while units(ph)>62:
     j=1
     while j<len(ph) and units(ph[:j+1])<=54:j+=1
     if cur:chunks.append(cur);cur=''
     chunks.append(ph[:j]);ph=ph[j:]
   cur+=ph
 if cur:chunks.append(cur)
 return [{'caption':c,'lines':split_lines(c),'weight':len(c)} for c in chunks]
# Pronunciations belong to speech, while captions retain the implementation spelling.
replacements=[
 ('waf-data-exclusions','ワフ・データ・エクスクルージョンズ'),('waf-raw-body-controls','ワフ・ロー・ボディー・コントロールズ'),
 ('backend-response','バックエンド・レスポンス'),('return-response','リターン・レスポンス'),('policy-error','ポリシー・エラー'),
 ('apim-request-id','エーピーアイエム・リクエスト・アイディー'),('BackendResponseCode','バックエンド・レスポンスコード'),('ResponseCode','レスポンスコード'),
 ('TransactionId','トランザクション・アイディー'),('CorrelationId','コリレーション・アイディー'),('SignatureId','シグネチャー・アイディー'),('PolicyId','ポリシー・アイディー'),
 ('LastError','ラストエラー'),('Retry-After','リトライ・アフター'),('on-error','オン・エラー'),('Named Value','ネームド・バリュー'),
 ('Firewall','ファイアウォール'),('AppGW','アップゲートウェイ'),('APIM','エーピーアイエム'),('IDPS','アイディーピーエス'),('WAF','ワフ'),('gRPC','ジーアールピーシー'),
 ('Operation','オペレーション'),('Portal','ポータル'),('API','エーピーアイ'),('SQL','エスキューエル'),('XML','エックスエムエル'),('UTC','ユーティーシー'),('ETag','イータグ'),('IaC','アイエーシー'),('SKU','エスケーユー'),('MIME','マイム'),('IP','アイピー'),
 ('Standard','スタンダード'),('Premium','プレミアム'),('Deny','デナイ'),('Allow','アラウ'),('Matched','マッチド'),('Blocked','ブロックド'),('Action','アクション'),
 ('accepted','アクセプテッド'),('rejected','リジェクテッド'),('inbound','インバウンド'),('phase','フェーズ'),('base','ベース'),('Core','コア'),('Access','アクセス'),
 ('scrubbing','スクラビング'),('Workspace','ワークスペース'),('query','クエリ'),('header','ヘッダー'),('raw','ロー'),('profile','プロファイル'),('model','モデル'),
 ('service','サービス'),('gateway','ゲートウェイ'),('protocol','プロトコル'),('fragment','フラグメント'),('secret','シークレット'),('override','オーバーライド'),('bypass','バイパス'),('idpsMode','アイディーピーエスモード'),('Policy','ポリシー'),('probe','プローブ'),('dry-run','ドライラン'),('timeout','タイムアウト'),('TPM','ティーピーエム'),('quota','クォータ'),
 ('Gateway','ゲートウェイ'),('Azure','アジュール'),('trace','トレース'),('FW','ファイアウォール'),('ID','アイディー'),
 ('949110','九四九一一〇'),('503','五〇三'),('500','五〇〇'),('429','四二九'),('403','四〇三'),('401','四〇一'),('400','四〇〇')]
def speech(t):
 for a,b in replacements:t=t.replace(a,b)
 return t
turns=[]
for ch,key,block in groups:
 for ln in block.strip().splitlines():
  person,caption,hi=ln.split('|')
  v={'layout':'hologram','heading':s['topic']} if key is None else copy.deepcopy(views[key])
  if key is not None:v['highlightRows']=[x for x in hi.split(',') if x]
  chunks=chunk_caption(caption)
  delivery={'mako':'直前の説明を受け、視聴者の疑問と確認を親しみのある声で伝える。','manabu':'相手への返答として、判断の理由と利用者への意味を穏やかに整理する。','ikumi':'相手の疑問に答え、処理の順序と確認点を落ち着いて明瞭に説明する。'}[person]
  if ch=='check' and person=='mako' and not hi:delivery+=' 確認問題の問い。選択肢を対等に読み、答えを強調せず短い考える間を残す。'
  t={'id':f'lesson13-turn-{len(turns)+1:03}-{person}','speaker':person,'chapterId':ch,'speech':speech(caption),'caption':caption,'subtitleLines':chunks[0]['lines'],'subtitleChunks':chunks,'gapAfterSeconds':.30 if ch=='check' and not hi else .14,'delivery':delivery,'visual':v}
  turns.append(t)
document_turns=[t for t in turns if t['visual'].get('viewId')=='lesson13-document']
for i,t in enumerate(document_turns):
 variant=['question','question','accepted','reject','reject','question'][i]
 t['visual']={'layout':'svg','viewId':'lesson13-document-order','svg':f'diagrams/document-order-{variant}.svg'}
s['turns']=turns
s['authoringNotes']={'centralExample':'文書API 400とaccepted・計数の共存。正当拒否なら呼出側形式を修正。設定変更は別の判断としてWAF正規データ誤検知を例示。','viewPolicy':'同一viewIdでは表・メニュー・構図を固定し、赤枠のみ移す。確認問題の最初の質問は強調なし。','claimScope':'資料と定義に基づく教材。Azureへの変更や実測は実施しない。'}
(HERE/'script.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'turns':len(turns),'captionCharacters':sum(len(t['caption']) for t in turns),'speechCharacters':sum(len(t['speech']) for t in turns),'views':len(views),'chapters':len(s['chapters']),'openingCharacters':sum(len(t['speech']) for t in turns if t['chapterId']=='opening')},ensure_ascii=False))
