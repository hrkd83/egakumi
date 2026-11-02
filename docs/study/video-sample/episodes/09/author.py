#!/usr/bin/env python3
"""Author episode 09 only; no generation or shared-file writes."""
from pathlib import Path
import copy, json, re

HERE=Path(__file__).resolve().parent
def ref(anchor): return dict(file='../02-external-protection.html',anchor=anchor)
def table(key,title,section,rows,columns=('確認する対象','指定・意味'),widths=(300,672),notice=None):
    v=dict(layout='settings',viewId='waf09-'+key,areaLabel='WAF / 教材用設定メニュー',title=title,kind='Application Gateway WAF',section=section,menu=['入口と役割','管理ルール','本文と上限','適用ポリシー','限定した除外','特殊形式','確認問題'],subtitle='リポジトリ定義と公開設定例を読み解く',table=dict(columns=[dict(label=c,width=w) for c,w in zip(columns,widths)],rows=[dict(id=r[0],cells=list(r[1:])) for r in rows]),highlightRows=[])
    if notice:v['notice']=notice
    return v
V={}
V['opening']=dict(layout='hologram',heading='WAFの検査と誤検知への対応')
V['route']=dict(layout='svg',viewId='waf09-route',svg='diagrams/route-none.svg',heading='どこで止まった要求か')
V['routes']=table('routes','入口ごとに、WAFの適用範囲を確かめる','入口と役割',[
('api','通常HTTP API','AppGW WAF → FW → 内部APIM'),('portal','Portalの入口','AppGWからPortalへ。APIMを経由しない'),('grpc','gRPCのTCP入口','HTTP WAFの対象外'),('separate','PortalからのAPI呼出し','別の要求として、Core API側の検査を受ける')])
V['mode']=table('mode','HTTPの攻撃パターンと、APIの利用許可を分ける','管理ルール',[
('waf','WAFが確認','SQLi・XSS・パストラバーサル等'),('apim','APIMが確認','APIMサブスクリプションキー・入力契約等'),('mode','test / production','Enabled / Prevention'),('detect','Detectionの意味','検知・記録する。モード自体は遮断しない')])
V['parts']=table('parts','WAFポリシーを構成する設定','管理ルール',[
('managed','管理ルール','OWASP CRS 3.2の攻撃パターン'),('custom','カスタムルール','管理ルールより先に追加条件を判定'),('exclude','除外','対象項目と原因ルールを限定して調整'),('scrub','ログ保護','機微な値をログ上でマスク。除外とは別')])
V['score']=table('score','MatchedとBlockedを、同じ要求で区別する','管理ルール',[
('warning','例：Warningの一致','3点。これだけなら合計5点未満'),('critical','例：Criticalの一致','5点。1件でも閾値に達し得る'),('sum','CRSのスコア集計','合計5以上 → Preventionで遮断'),('other','別の拒否条件','本文上限・カスタムBlockは各条件で判断')])
V['logs']=table('logs','遮断の結果から、原因の検知へ戻る','管理ルール',[
('request','同じ要求をそろえる','時刻・URI・TransactionIdを照合'),('matched','Matched','攻撃パターンに一致した個別ルール'),('blocked','Blocked / 949110','異常スコアを集計した遮断結果'),('cause','調整する対象','原因となった個別ルールと入力項目')])
V['limits']=table('limits','本文の受入上限と、検査範囲は別の設定','本文と上限',[
('check','本文検査','requestBodyCheck：true'),('accept','本文の受入上限','2000 KiB：ファイル部分を除く本文'),('inspect','本文の検査範囲','2000 KiB：本文を検査する長さ'),('enforce','上限の強制','本文・ファイルともtrue')],notice='test / productionの共通指定値。稼働環境の取得値ではない')
V['defaults']=table('defaults','設定ファイルの指定と、省略時既定値を分ける','本文と上限',[
('config','configの共通指定','本文2000 KiB / 検査2000 KiB / ファイル4 MiB'),('bicep','Bicepの省略時既定','本文128 KiB / 検査128 KiB / ファイル100 MiB'),('effective','確認する値','配備に渡した値と、要求に適用したポリシー')])
V['file']=table('file','4 MiBのファイル上限は、raw本文の上限ではない','本文と上限',[
('multi','multipartのファイル部分','filenameを持つ部分：ファイル上限4 MiB'),('raw','raw音声・raw PDF','通常の本文上限が適用される'),('boundary','APIMの操作上限','WAFと別の契約。測る対象・単位も確認')])
V['scope']=dict(layout='svg',viewId='waf09-scope',svg='diagrams/scope-none.svg',heading='実際に適用される1つのポリシー')
V['compose']=table('compose','専用ポリシーには、保護設定を明示的に合成する','適用ポリシー',[
('inherit','Azureの適用','URI別 → リスナー別 → Gateway全体の順で優先'),('shared','この実装','sharedWafPropertiesから専用ポリシーを作る'),('keep','合成して維持','本文検査・上限・ログマスク・共通の追加規則'),('empty','wafExclusions：[]','共通追加指定が空。用途別の除外は別途合成')])
V['existing']=table('existing','企業管理の既存ポリシーを使う構成','適用ポリシー',[
('id','existingWafPolicyId','企業管理の既存ポリシーを再利用'),('replace','自動生成との関係','Coreの専用ポリシーへ自動置換しない'),('owner','調整の担当','適用設定を確認し、企業側管理者と対応')])
V['false']=table('false','正規データも、攻撃に似た文字列を含む','限定した除外',[
('prompt','モデルへの入力','説明用のSQLやHTMLなど'),('code','認可コード','Microsoftが発行する不透明な値'),('evidence','判断の材料','正規の入力か、どのルール・項目が一致したか'),('minimum','除外の単位','要求先の完全パス × 項目の値 × 原因ルール')])
V['callback']=table('callback','Portalの認証callback：code値だけを限定除外','限定した除外',[
('path','公開パス','/api/auth/callback/microsoft'),('match','対象項目','RequestArgValues / Equals / code'),('rules','原因ルール','SQLi：942430・942440だけ'),('keep','維持する検査','state・他の引数・他の管理ルール・本文と上限')])
V['boundary']=table('boundary','限定した除外にも、広がる境界がある','限定した除外',[
('case','URLパス照合','大文字小文字を区別しない'),('method','HTTP方式','パスの関連付けでは方式を絞れない'),('query','同名のquery引数','同じURIの同名queryにも除外が適用'),('guard','残す確認','callbackはPortal側、API入力はAPIM側で制約を維持')])
V['othercallback']=table('othercallback','同じcodeでも、別パスの設定をそのまま流用しない','限定した除外',[
('portal','Portalのcallback','code値：942430・942440を除外'),('gateway','Gatewayの本人確認callback','code値：942430・942440・942450を除外'),('path','Gateway側の完全パス','/auth/connections/browser/identity-callback'),('manifest','通常データ欄の正本','waf-data-exclusions.jsonをBicepとPythonで共用')])
V['allow']=table('allow','誤検知を解消するときも、残す検査を決める','限定した除外',[
('allow','広いカスタムAllow','一致すると、後続の管理ルール検査を止める'),('aggregate','集計規則949110','原因の攻撃パターンではない。調整対象にしない'),('mask','ログマスク','値の表示を保護する。検査の除外にはならない'),('retain','限定した除外','他項目・他ルールの検査を保つ')])
V['raw']=table('raw','SSML・WAVは、対応する完全パスで調整する','特殊形式',[
('mime','正規のSSML・WAV','Content-Type制約の920420を無効化'),('traversal','正本Speechのraw本文','正常な ../ の誤一致へ930110を調整'),('keep','維持する保護','他の管理ルール・本文検査・上限'),('apim','APIMの操作制約','HTTP方式・許可するMIMEを確認')])
V['complement']=dict(layout='svg',viewId='waf09-raw-complement',svg='diagrams/complement-none.svg',heading='raw本文の許容と、非本文の保護を分ける')
V['document']=table('document','文書API：WAFの上限とAPI契約をそろえて確認','特殊形式',[
('path','対象の公開パス','/tools/documents/v1/analyze'),('waf','WAF側の指定','受入上限1025 KiB / 検査範囲1025 KiB'),('apim','APIM側の公開契約','文書の上限1 MiBを強制'),('meaning','WAFの検査範囲','PDF・音声をデコードした意味内容の検査ではない')])
V['verify']=table('verify','正規データと、攻撃の対照を組み合わせて確認する','限定した除外',[
('normal','正規データ','対象パスの対象項目が通る'),('other','残した検査','他項目・他ルールの対照が拒否される'),('edge','境界の確認','query・method違い、別パスも確認'),('result','到達先を照合','WAFの結果と、後段で処理された範囲を確認')])
V['q1']=table('quiz-score','確認①：Matchedが1件あれば、必ず遮断？','確認問題',[
('condition','条件','Warningが1件、3点。他の一致や上限違反なし'),('yes','選択肢A','Matchedなので、必ず遮断される'),('no','選択肢B','合計とモード、他の拒否条件を確認する')])
V['q2']=table('quiz-policy','確認②：URI別と全体を、続けて検査する？','確認問題',[
('condition','条件','要求のパスに専用ポリシーが関連付いている'),('two','選択肢A','全体で検査してから、専用を追加する'),('one','選択肢B','具体的な専用ポリシーが優先して適用される')])
V['q3']=table('quiz-exclude','確認③：codeの誤検知を、どこまで除外する？','確認問題',[
('condition','条件','Portal callbackのcode値が2つのSQLi規則へ一致'),('all','選択肢A','callbackをカスタムAllowで丸ごと通す'),('narrow','選択肢B','完全パス・code値・原因の2規則へ絞る')])
V['summary']=table('summary','要求1つについて、検査・上限・適用・除外を説明する','管理ルール',[
('inspect','検査と結果','攻撃パターンの一致と、拒否の判断を分ける'),('size','本文とサイズ','受入・検査・ファイル・API契約を区別'),('policy','適用ポリシー','最も具体的な1つ。必要な保護を明示合成'),('exclude','誤検知の調整','完全パス × 項目の値 × 原因ルール')])
V['next']=table('next','次回：Firewallの許可とIDPSの検知','入口と役割',[
('today','今回の到達点','公開入口で、WAFが止めた理由を説明する'),('next','第10回の問い','FWで通信がAllowなら、IDPSの結果も同じ？'),('boundary','次に区別すること','通信規則のAllow / Denyと、IDPS Alert / Deny')])

# speaker | view | highlight target(s) | natural dialogue for this episode
ROWS={
'opening':'''mako|opening||正しいデータを送ったはずなのに、WAFで止まりました。該当のAPIを丸ごと通せば、解決でしょうか。
manabu|opening||使えるように直すことと、入口の保護を保つことを、一緒に考えたいですね。正規データまで止める理由を調べましょう。
ikumi|opening||今日は、攻撃パターンの検査、本文の上限、適用ポリシーを分けて読みます。そのうえで、除外する範囲を絞ります。
mako|opening||止めた機能と、外してよい検査を確かめるんですね。ひとつの要求を入口から追ってみましょう。''',
'route':'''mako|route||まず、403という応答だけで、WAFが拒否したと判断してよいですか。
ikumi|route|waf|応答の番号だけでは決まりません。通常APIは、AppGWのWAF、Firewall、内部APIMを通ります。APIMも別の理由で403を返せます。
manabu|route|waf|WAFで拒否した要求は、後段へ業務転送されません。その要求について、どこまで届いたかをそろえると、調べる場所を絞れます。
ikumi|route|apim|APIMのログがないことだけでも断定せず、時刻と要求先を合わせ、AppGW側のActionや原因規則を確認します。今回はWAFの判断に絞って見ます。
mako|routes||すべての入口で、この順番の検査を受けるわけではないんですね。
ikumi|routes|portal|Portalの入口はAPIMへ転送しません。PortalからCore APIを呼ぶ場合は、そのAPI呼出しが別の要求としてAPIM側の検査を受けます。
ikumi|routes|grpc|gRPCのTCP入口は、HTTP WAFの対象外です。HTTPの要求を検査する機能なので、入口とプロトコルを最初に確かめます。
manabu|routes|api|ここでの値や動作は、資料とリポジトリ定義に基づく教材です。稼働Azureから設定を取得した結果とは分けて、指定された保護を読み解きます。''',
'rules':'''mako|mode||WAFは、APIを呼ぶ権限まで判断してくれるのでしょうか。
ikumi|mode|waf|主にHTTPに含まれる攻撃パターンを調べます。SQLインジェクション、クロスサイトスクリプティング、パストラバーサルなどです。利用者の権限確認とは役割が違います。
manabu|mode|apim|正しいAPIキーを持つ人の入力にも、不審な文字列は含まれ得ます。入口の攻撃検査と、APIMサブスクリプションの利用許可は、両方必要なんですね。
ikumi|mode|mode|testとproductionは、ともにPreventionを指定しています。条件に応じて要求を拒否するモードです。Detectionは検知と記録をするモードで、同じ意味ではありません。
mako|parts||検査条件は、ひとつのポリシーにまとまっているのですか。
ikumi|parts|managed|はい。この実装はOWASP CRS 3.2の管理ルールを基礎にし、動作モード、カスタムルール、除外、ログ保護もポリシーへまとめます。
manabu|parts|scrub|値をログに残さないことと、その値を検査しないことも別ですね。ログマスクを設定しただけで、誤検知がなくなるわけではありません。
mako|score||管理ルールで一度検知されたら、その時点で必ず拒否されるのでしょうか。
ikumi|score|warning|CRSの標準動作では、重大度に応じた異常スコアを足します。たとえばWarningは3点です。これだけなら、合計5点の遮断しきい値には達しません。
ikumi|score|critical,sum|Criticalは5点で、一件でもしきい値に達し得ます。合計5以上ならPreventionで遮断します。一方、サイズ上限やカスタムBlockは、それぞれの条件で判断します。
manabu|logs|matched,blocked|個別ルールのMatchedと、集計結果のBlockedを区別するんですね。同じ要求のTransactionIdをそろえ、遮断の結果から原因の検知へ戻ります。
ikumi|logs|cause|949110は異常スコアの集計規則で、攻撃文字列そのものを見つけた規則ではありません。誤検知の調整では、同じ要求の原因ルールと対象項目を確かめます。''',
'limits':'''mako|limits||次は本文です。受け入れる長さと、検査する長さは、同じ設定ですか。
ikumi|limits|accept,inspect|独立した設定です。今回のtestとproductionは、受入上限も検査範囲も2000 KiBです。本文検査は有効で、本文とファイルの上限も強制します。
manabu|limits|inspect|受入だけ増やせば、検査する長さとの差ができますね。大きなデータを通したいときも、どこまで検査するかを一緒に決めます。
mako|defaults||Bicepに128 KiBと書かれていたら、こちらが実際の上限なのでしょうか。
ikumi|defaults|bicep,config|それは省略時の既定値です。設定ファイルでは別の値を指定しています。既定値、配備に渡した値、実際に適用されるポリシーを、同じものとして扱いません。
manabu|defaults|effective|資料を読むときも、ひとつの数字だけ拾わず、どこから渡した設定かを確認するわけですね。
mako|file||ファイル上限は4 MiBです。では、3 MiBのraw音声も、その範囲として送れますか。
ikumi|file|multi,raw|4 MiBは、multipartの中でfilenameを持つファイル部分の上限です。raw音声やraw PDFには通常の本文上限が適用されるため、同じ枠ではありません。
manabu|file|boundary|データがファイルらしく見えても、HTTPの送り方で測る対象が変わるんですね。さらにAPIMの操作上限もあるので、入口を通ることだけを成功条件にはしません。''',
'scope':'''mako|scope||専用ポリシーがあるパスでは、共通ポリシーの検査に、専用の検査を追加するのですか。
ikumi|scope|uri|続けて二重に検査する仕組みではありません。URI別、リスナー別、Gateway全体の順で、より具体的なポリシーが優先して適用されます。
manabu|scope|uri|専用ポリシーを置くと、そこで必要な保護を持っていないといけませんね。共通側にあるから大丈夫、とは考えられないわけです。
ikumi|compose|shared,keep|この実装はsharedWafPropertiesから、本文検査、上限、ログマスク、共通の追加規則を明示的に合成し、完成した専用ポリシーを作ります。Azureによる親の自動継承ではありません。
mako|compose||設定ファイルのwafExclusionsが空配列なら、除外は一件もないと考えてよいですか。
ikumi|compose|empty|空なのは、設定ファイルから共通で追加する指定です。用途別の除外や、manifestの設定は別に合成されます。要求先に適用される完成形を確認します。
mako|existing||企業の既存WAFポリシーを指定する場合も、同じ専用設定が自動で作られますか。
ikumi|existing|id,replace|existingWafPolicyIdを指定する構成では、企業管理の既存ポリシーを再利用します。自動生成する専用ポリシーで置き換えないため、必要な調整は企業側管理者と確認します。''',
'exclusions':'''mako|false||正規の入力なのに、攻撃パターンへ一致するのは、どんな場合でしょう。
manabu|false|prompt,code|モデルにSQLやHTMLを説明させたい場合があります。Microsoftの認可コードのような不透明な文字列もあります。利用目的が正当でも、文字列だけでは攻撃に似ることがあるんです。
ikumi|false|evidence,minimum|そこで、正規データと確認したうえで、要求先の完全パス、対象項目の値、原因ルールを特定します。この三つで除外を絞ると、残す検査を説明できます。
mako|callback||Portalのログインで、認可コードがSQLインジェクションの規則へ一致した例を見ましょう。画面では、何を除外していますか。
ikumi|callback|path,match|対象は、このcallbackの完全パスです。RequestArgValuesで、引数名codeをEqualsで選び、その値を対象にします。codeという名前自体を丸ごと無視する意味ではありません。
ikumi|callback|rules|除外するのは、原因となった942430と942440の二つだけです。同じcode値でも、他の管理ルールまで全部外す設定にはしていません。
manabu|callback|keep|stateや他の引数、本文検査とサイズ上限も維持します。ログインを通すための調整が、callback全体を素通しにする変更へ広がらないようにします。
mako|boundary||完全パスと項目を選べば、本文の、そのHTTP方式だけの例外にできますか。
ikumi|boundary|method,query|そこにも境界があります。パスの関連付けではHTTP方式を絞れず、同じURIの同名queryにも引数の除外が適用されます。本文だけの例外とは扱えません。
manabu|boundary|case,guard|パス照合は大文字小文字も区別しません。認証や入力の制約は、このcallbackではPortal側、API入力ではAPIM側で維持し、queryやmethod違いも試験します。
ikumi|othercallback|gateway,path|Gatewayの本人確認callbackは別の完全パスで、code値に対する除外も三つの規則です。似た名前でも、Portal側の設定をそのまま流用しません。
manabu|othercallback|manifest|通常のデータ欄の除外には、BicepとPythonで共用するmanifestがあります。変更する正本をそろえ、手元の一時調整と恒久設定を食い違わせないようにします。
mako|allow||それなら、該当パスをカスタムAllowにすれば、原因規則を調べる手間は減りませんか。
ikumi|allow|allow|カスタムAllowは、一致すると後続の管理ルール検査を止めます。ひとつの値の誤検知を直すために、他の攻撃パターンまで検査しなくなるので、範囲が広すぎます。
manabu|allow|aggregate,retain|集計規則949110は無効化できません。原因の値と規則に絞って調整し、どの保護を維持できたかを確かめましょう。''',
'raw':'''mako|raw||JSONの項目を選べないraw音声や、SSMLでは、どう調整するのでしょうか。
ikumi|raw|mime|対応する完全パスで、正規のContent-Typeが許可外と判定される920420を無効化します。他の管理ルールと本文検査は残し、HTTP方式と許可MIMEをAPIMで確認します。
ikumi|raw|traversal|正本SpeechのSSMLとWAVでは、正常なraw本文中の相対パスが930110へ誤一致するため、この規則も調整します。この調整を無関係なAPIへ広げません。
mako|complement||本文の相対パスを許すと、URIやヘッダーにある不審なパスも通ってしまうのですか。
ikumi|complement|block|非本文の四か所、URI、query、ヘッダー、Cookieには、パストラバーサルを拒否するカスタムBlockを追加します。930100など、他の管理ルールも維持します。
manabu|complement|limit|ただし、この四本がCRSのすべての検査と正規化を再現するわけではありません。raw本文を許容する限定した判断として、残る検査範囲を把握します。
mako|document||文書APIの1025 KiBというWAF設定と、契約上限の1 MiBは、同じ意味でしょうか。
ikumi|document|waf,apim|WAFでは受入上限と検査範囲を、ともに1025 KiBへ設定します。公開APIの文書上限1 MiBはAPIM側で強制します。測る場所と対象を分けて、両方を確認します。
manabu|document|meaning|WAFを通ったから、PDFや音声をデコードした意味内容まで安全性を検査済み、とは言えません。専用入力ガードと操作の上限を維持する理由も、そこにあります。
mako|verify||除外の変更後は、正規データが一度通れば完了ですか。
ikumi|verify|normal,other,edge|正規データに加え、残した項目や規則で拒否すべき対照も試します。別パス、queryやmethod違いも確認し、通す範囲と拒否する範囲が意図どおりかを確かめます。''',
'check':'''mako|q1||三つ、確認します。Warningが一件、3点で、他の一致や上限違反はありません。Matchedのログがあるので、必ず遮断されるでしょうか。
ikumi|q1|no|合計とモード、他の拒否条件を確認します。この例の3点だけでは、CRSの合計5点に届きません。個別の一致と遮断結果を、同じ要求で分けて追います。
manabu|q1|no|検知されたことは大事な手掛かりです。ただ、それを拒否と読み替えると、後段へ届いた要求まで見落とすわけですね。
mako|q2||二つ目です。要求のパスに専用ポリシーがあります。共通ポリシーを通過してから、追加の専用検査を受けるのでしょうか。
ikumi|q2|one|具体的な専用ポリシーが優先して適用されます。共通設定は自動継承されないため、この実装では必要な保護を明示的に合成して専用ポリシーを作っています。
manabu|q2|one|ポリシーの名前だけでなく、その要求へ適用された設定の完成形まで確認する、と整理できますね。
mako|q3||最後です。Portalのcallbackのcode値が、二つのSQLi規則へ誤一致しました。callback全体をカスタムAllowで通すのが適切ですか。
ikumi|q3|narrow|完全パス、codeの値、原因の二規則へ絞ります。カスタムAllowで後続検査を止めず、stateや他の規則、本文検査と上限を維持します。
manabu|q3|narrow|使えるようになった結果と、残した防御の両方を説明します。queryやmethodの境界も確認して、調整の範囲を確定するんですね。''',
'summary':'''mako|summary||今日の整理です。まず検知した攻撃パターンと、拒否の判断を分ける。次に本文の受入上限、検査範囲、ファイルやAPIの上限を区別する、ですね。
ikumi|summary|policy|そして要求先から、実際に適用されるポリシーを確認します。専用ポリシーで維持する保護を読み、原因の入力項目と規則を特定します。
manabu|summary|exclude|正規データの誤検知は、完全パス、項目の値、原因ルールへ絞って調整します。要求を通すことと、検査を残すことを一緒に判断できれば、今回の到達点です。
mako|next|next|次回は、Firewallの許可とIDPSの検知です。通信規則がAllowなら、攻撃の検知結果も同じ意味なのでしょうか。二つの判断を分けて追います。'''
}

CHAPTERS=[('opening','正規データがWAFで止まったら','今回の問いを共有する',['purpose','policy']),('route','要求の入口と、止まった層','通常APIの経路とWAFの範囲を区別する',['purpose','ingress','request-order']),('rules','攻撃パターンと遮断の判断','一致・異常スコア・上限による拒否を分ける',['policy']),('limits','本文の受入と検査範囲','本文・ファイル・API上限を区別する',['settings']),('scope','どのポリシーが適用されるか','優先順位と明示的な合成を説明する',['scope']),('exclusions','正規データの除外を絞る','完全パス・項目値・原因規則を対応付ける',['exclusions']),('raw','音声と文書の専用調整','raw本文と非本文の保護、専用上限を区別する',['payloads']),('check','三つの問いで確かめる','検知・適用・除外の混同を訂正する',['policy','scope','exclusions']),('summary','まとめと第10回の問い','WAFの判断を説明し、FWとIDPSへつなぐ',['policy','settings','scope','exclusions','payloads'])]

def chunks(text):
    # Keep ASCII words / identifiers / six-digit rule IDs atomic.  The same
    # 32-fullwidth-character line budget is applied with ASCII at .58 width,
    # matching the common renderer's width estimate. Prefer clauses near the
    # right edge, then particles; never strand closing punctuation at line start.
    width=lambda s:sum(.58 if ord(c)<128 else 1 for c in s)
    atom=re.compile(r'[A-Za-z0-9_./:-]+|.',re.DOTALL)
    particle=re.compile(r'(?:として|による|なので|ために|なら|から|ので|では|には|とは|まで|だけ|こと|もの|を|が|は|も|に|へ|で|と)$')
    close='、。！？）」』】〉》ーゃゅょぁぃぅぇぉっ'
    def lines(p):
        out=[]
        while width(p)>32:
            ends=[m.end() for m in atom.finditer(p)]
            safe=[i for i in ends if width(p[:i])<=32 and i<len(p) and p[i] not in close]
            if not safe:raise ValueError('Unbreakable subtitle token exceeds the line budget: '+p)
            near=[i for i in safe if width(p[:i])>=22]
            if not near:near=safe
            def quality(i):
                prefix=p[:i]
                # A short remainder is preferable to a split inside a clause.
                clause=70 if prefix[-1] in '、。！？' else 25 if particle.search(prefix) else 0
                return clause+width(prefix)
            cut=max(near,key=quality)
            out.append(p[:cut]);p=p[cut:]
        if p:out.append(p)
        return out
    pieces=[]
    for phrase in re.findall(r'.+?[。！？]|.+$',text):
        wrapped=lines(phrase)
        for i in range(0,len(wrapped),2):
            segment=wrapped[i:i+2]
            if len(segment)==1 and pieces and len(pieces[-1])==1:
                pieces[-1]+=segment
            else:pieces.append(segment)
    return [dict(caption=''.join(part),lines=part,weight=len(''.join(part))) for part in pieces]

REPLACE=[('SQLインジェクション','エスキューエル・インジェクション'),('SQLi','エスキューエル・アイ'),('TransactionId','トランザクション・アイディー'),('RequestArgValues','リクエスト・アーグ・バリューズ'),('sharedWafProperties','共通のワフ設定'),('existingWafPolicyId','既存ワフポリシーのアイディー'),('wafExclusions','共通除外の設定'),('Content-Type','コンテントタイプ'),('パストラバーサル','パストラバーサル'),('Prevention','プリベンション'),('Detection','ディテクション'),('OWASP','オワスプ'),('Critical','クリティカル'),('Warning','ワーニング'),('Matched','マッチド'),('Blocked','ブロックド'),('manifest','マニフェスト'),('callback','コールバック'),('filename','ファイルネーム'),('Equals','イコールズ'),('method','メソッド'),('query','クエリー'),('state','ステート'),('code','コード'),('raw','ロー'),('Block','ブロック'),('Allow','アロー'),('Azure','アジュール'),('AppGW','アプリケーション・ゲートウェイ'),('APIM','エーピーアイ・マネジメント'),('Firewall','ファイアウォール'),('Portal','ポータル'),('Core','コア'),('WAF','ワフ'),('HTTP','エイチティーティーピー'),('gRPC','ジーアールピーシー'),('TCP','ティーシーピー'),('HTML','エイチティーエムエル'),('SQL','エスキューエル'),('JSON','ジェイソン'),('PDF','ピーディーエフ'),('SSML','エスエスエムエル'),('WAV','ウェーブ'),('CRS','シーアールエス'),('URI','ユーアールアイ'),('API','エーピーアイ'),('MIME','マイム'),('IDPS','アイディーピーエス'),('Bicep','バイセップ'),('KiB','キビバイト'),('MiB','メビバイト'),('../','ドットドットスラッシュ'),('config','コンフィグ'),('test','テスト'),('production','プロダクション')]
turns=[]
for chapter,*_ in CHAPTERS:
    for row in ROWS[chapter].splitlines():
        speaker,key,highlight,caption=row.split('|',3)
        visual=copy.deepcopy(V[key]);targets=highlight.split(',') if highlight else []
        if visual['layout']=='settings':visual['highlightRows']=targets
        elif visual['layout']=='svg':visual['svg']=f'diagrams/{dict(route="route",scope="scope",complement="complement")[key]}-{highlight or "none"}.svg'
        speech=caption
        for a,b in REPLACE:speech=speech.replace(a,b)
        cue=chunks(caption)
        turns.append(dict(id=f'lesson09-turn-{len(turns)+1:03}-{speaker}',speaker=speaker,chapterId=chapter,speech=speech,caption=caption,subtitleLines=cue[0]['lines'],subtitleChunks=cue,gapAfterSeconds=.16,delivery={'mako':'視聴者の疑問を一緒に考える自然な質問と短い納得。通常速度で、問いの末尾にわずかな間を置く。','manabu':'利用する人の目的と、運用する人の判断を同僚へ落ち着いて話す。通常速度で、相手の説明を受けた自然な返答。','ikumi':'仕組みを知る同僚として、明瞭で穏やかに答える。用語と規則番号を落ち着いて区切り、通常速度で話す。'}[speaker],visual=visual))
        if re.search(r'(?<!\d)\d{6}(?!\d)',caption):
            turns[-1]['delivery']+='6桁の規則番号は桁ごとに区切って読む。例：949110はキュウ・ヨン・キュウ・イチ・イチ・ゼロ。数量として万単位にしない。'

S=dict(schemaVersion=1,productionProfile='study-series-v1',visualStyle='teaching-settings-v2',episodeKey='09',episodeNumber=9,episodeId='09',title='第9回 WAFの検査と誤検知への対応',topic='WAFの検査と誤検知への対応',status='ready-for-generation',cast='podcast-cast-gemini.json',speed=1,targetDurationSeconds=900,durationPolicy='通常速度の実音声で確定。尺合わせの加速や無音の追加は行わない。',logoIntroSeconds=4,logoClosingSeconds=8,speechSettings=dict(language='ja-JP',speed=1,enhancePronunciation=None),scope='教材02の1〜8節、現行リポジトリ定義・公開設定例に基づく教材。稼働Azureの取得値・実測結果ではない。',learningObjectives=['攻撃パターンの一致と、異常スコア・上限・カスタムBlockによる拒否を区別する。','本文の受入上限、検査範囲、multipartファイル上限とAPI契約を区別する。','適用ポリシーを特定し、正規データの除外を完全パス・項目値・原因ルールへ絞る理由を説明する。'],sourceReferences=[ref(a) for a in ['purpose','ingress','request-order','policy','settings','scope','exclusions','payloads']]+[dict(file=p) for p in ['../../../infra/modules/application-gateway.bicep','../../../config/test.json','../../../config/production.json','../../../apps/gateway/waf-data-exclusions.json','../../../apps/gateway/waf-raw-body-controls.json','../../../scripts/waf_policy.py']],chapters=[dict(id=c,title=t,goal=g,sourceRefs=[ref(a) for a in anchors]) for c,t,g,anchors in CHAPTERS],firstCut=dict(omitTurnIds=[],sourceRecordingsKept=True),turns=turns)
(HERE/'script.json').write_text(json.dumps(S,ensure_ascii=False,indent=2)+'\n')
(HERE/'brief.md').write_text('''# 第9回 WAFの検査と誤検知への対応\n\n到達点：攻撃パターン・本文上限・適用ポリシーを区別し、正規データの除外範囲を絞る理由を説明できる。\n\n参照：02-external-protection.htmlの1〜3節を復習し、4〜8節を扱う。通常HTTP APIを主例に、Portal callbackのcode値を狭く除外する例、raw音声と文書の専用調整を結び付ける。第10回で扱うFW/IDPS、第11〜12回のAPIM詳細へ広げない。稼働環境を取得・変更した結果として説明しない。\n\n採用画面：teaching-settings-v2。質問から回答まで同一viewId・構図・縮尺。説明対象のみ赤枠を変更し、確認問題の問いでは正解を強調しない。入口の経路・適用優先順位・raw本文の補完は同じ設定メニュー内へ補助図を併記する。\n\n音声：Gemini 3.8 Flash TTS、MAKO=Aoede / MANABU=Charon / IKUMI=Orus、全員通常速度1.0。共通ロゴ・既存肖像・BGM・8秒closingを使う。\n''')
print(json.dumps(dict(turns=len(turns),captionCharacters=sum(len(t['caption']) for t in turns),speechCharacters=sum(len(t['speech']) for t in turns),openingCharacters=sum(len(t['speech']) for t in turns if t['chapterId']=='opening'),views=len({t['visual'].get('viewId') for t in turns if t['chapterId']!='opening'})),ensure_ascii=False,indent=2))
