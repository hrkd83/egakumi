"""Episode 05 authored dialogue and visual plan. Local, deterministic, no API."""
import copy
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent

def cards(heading, *items):
    return {'layout': 'cards', 'heading': heading, 'cards': [
        {'id': str(i), 'title': title, 'body': body.split('|')}
        for i, (title, body) in enumerate(items)]}

def flow(heading, items, labels=None):
    xs = [30, 435, 840]
    return {'layout': 'diagram', 'heading': heading,
        'nodes': [{'id': str(i), 'label': a, 'detail': b, 'x': xs[i], 'y': 185,
                   'width': 330, 'height': 115} for i, (a, b) in enumerate(items)],
        'edges': [{'from': str(i), 'to': str(i+1), 'label': (labels or ['', ''])[i],
                   'points': [[xs[i]+165,300],[xs[i]+165,355+i*65],
                              [xs[i+1]+165,355+i*65],[xs[i+1]+165,300]]}
                  for i in range(len(items)-1)]}

V = {
'opening': {'layout':'hologram', 'heading':'Firewallは何を判断するか'},
'path': flow('経路が先、Firewallの判定はその後', [('送信元','IP・宛先・プロトコルを確認'),('Azure Firewall','経路上へ届いた通信を判定'),('接続先','NSG・サービス認証も関係')], ['UDR等で到達','許可後に中継']),
'direct': cards('定義されている許可と、実際に通る経路', ('Firewall経由','AppGW → APIM|AppGW → ACA入口|往路・復路をそろえる'),('標準の直接経路','APIM → Foundry PE|ACA → SQL・Key Vault PE|FWルールの有無と分ける')),
'two': cards('2種類のルールは、照合する情報が違う', ('Network','送信元IP範囲|宛先IP・サービス タグ等|TCP・UDPと宛先ポート'),('Application','送信元IP範囲|HTTP・HTTPSとポート|FQDN：HTTP Host / HTTPS SNI')),
'ntp': flow('FQDNを指定するNTPもNetworkルール', [('管理VM','management-time'),('DNS解決したIP','ntp.ubuntu.com'),('Network照合','UDP 123を許可')], ['名前を解決','IPとポートで判定']),
'tls': cards('この基盤の標準Applicationルール', ('HTTPSの宛先','SNIのホスト名で照合|terminateTLS: false|URLパス・本文を復号しない'),('検査の分担','HTTP：Hostを照合|Web攻撃検査：入口WAF|API認証・認可：APIM等')),
'order': flow('Networkで一致すれば、そこで判定終了', [('01 Network','一致 → Allowで終了'),('02 Application','Network不一致のWeb通信'),('03 最終判定','組み込み許可も評価 → 既定拒否')], ['不一致','許可なし']),
'hierarchy': cards('優先度は、種別の内側で読む', ('階層','ルールコレクショングループ|　→ コレクション|　　→ 個々のルール'),('評価とAction','種別 → グループ → コレクション|小さい優先度から照合|Allow / Denyはコレクション')),
'priority': cards('優先度の仮の比較：種類が先', ('Network：仮の例','グループ優先度 400|数値が大きくてもNetworkが先|すべてのApplicationより先'),('Application：仮の例','グループ優先度 350|数値が小さくてもNetworkの後|Network不一致のWeb通信を評価')),
'default': cards('独自Denyがなくても、最終的に拒否される', ('この基盤の独自定義','独自コレクションはAllow|独自Deny・DNATなし|Internet全ポート許可なし'),('最終の判定','Network → Application|Azure組み込み基盤用許可も評価|許可に一致しなければ既定拒否')),
'internal': flow('代表例：AppGWからAPIMへの内部中継', [('AppGW区画','実際の送信元を限定'),('Azure Firewall','agw-to-apim-http'),('APIM実内部VIP','TCP 80 / gRPC有効時443も')], ['往路UDR','Network / Allow']),
'portal': flow('Portal入口も、IPとポートで内部中継', [('AppGW区画','Portalへの要求'),('Azure Firewall','agw-aca-https'),('ACA区画','TCP 443を許可')], ['入口IP /32へのUDR','許可宛先はACA CIDR']),
'state': cards('応答の状態追跡と、復路UDRを分ける', ('許可済み接続への応答','Firewallはステートフル|応答だけの逆向きAllowは不要|別の新規接続は別判定'),('戻る経路','復路も同じFirewallへ|状態追跡は経路を作らない|UDRとルールの両方を確認')),
'storage': flow('APIMからStorageへのTCP 443', [('APIM区画','送信元条件を満たす'),('Network / Allow','apim-storage'),('StorageタグのIP','TCP 443に一致')], ['FWへ到達','ここで許可判定終了']),
'platform': cards('業務API以外にも、基盤の依存通信がある', ('APIMの代表例','Storage：TCP 443|Sql：TCP 1433|AzureMonitor等：監視・監査'),('読むときの区別','送信元はAPIM区画|サービス タグはIPの集合|業務SQL接続とは別の目的')),
'time': cards('時刻同期は、送信元ごとに読む', ('APIM','apim-platform-time|time.windows.com / UDP 123|allow-vm-platform内に所属'),('管理VM','management-time|ntp.ubuntu.com / UDP 123|独立した時刻同期の許可')),
'management': cards('管理・監視・認証を区画と宛先で絞る', ('管理VM区画','Entra認証|Azure Resource Manager|監視・エージェント保存先'),('ACA区画','aca-monitor-https|AzureMonitor・Entraのタグ|TCP 443：Networkで判定')),
'dns': cards('独自DNSは、設定・経路・送信元を確認する', ('custom-dnsの定義','独自DNSを指定した場合だけ|APIM・管理VM → DNSのIP群|TCP / UDP 53'),('通過するかは別確認','公開例は空配列：作成しない|この送信元にACAは含まない|VNet内の直接経路もあり得る')),
'proxy': cards('DNS proxyの有効化だけで、VNet DNSは変わらない', ('Firewall側の設定','enableProxy: true|Network FQDNの名前解決を支える|上流DNSも設定に沿って決まる'),('クライアント側の設定','VNetのDNS設定は別に確認|自動でFirewall IPへ切り替わらない|名前解決の経路を追う')),
'web': flow('Network不一致なら、WebのFQDNを照合', [('管理VM','archive.ubuntu.com'),('Application','vm-certificate-and-linux-packages'),('HTTP 80','宛先と送信元の条件に一致')], ['Networkは不一致の例','Allow']),
'maintain': cards('変更前に、同じ通信について照合する', ('通信を具体化','実際の送信元IP|宛先IP・ホスト名|プロトコル・ポート'),('設定と実態を結ぶ','経路 → 種別 → 優先度 → 条件|設定の正本と生成結果を確認|変更後は新しい接続とログ')),
'threat': cards('Allow / Denyと、検知のAlertを区別する', ('通信規則','条件に一致するか|コレクションのAction|最終的な既定拒否'),('公開configの例','本番：Premium / IDPS Alert|Threat IntelligenceもAlert|検出しただけで自動遮断とはしない')),
'q1': cards('確認1：Storage通信は、二重の許可が必要？', ('通信条件','APIM → StorageタグのIP|TCP 443|Network Allowに一致'),('問い','ApplicationのFQDN一覧にも|登録が必要でしょうか？')),
'q2': cards('確認2：名前が書かれたらApplication？', ('通信条件','管理VM → ntp.ubuntu.com|UDP 123'),('問い','NetworkとApplication、|どちらで許可を判断しますか？')),
'q3': cards('確認3：SQL PEへ届かないとき', ('通信条件','標準UDRのACA → SQL PE|Firewallを経由しない直接経路'),('問い','FirewallへAllowを追加すれば|直ると判断できますか？')),
'summary': {'layout':'summary','heading':'今日の到達点：通信ごとに判定理由を説明する','bullets':['まず、経路上でFirewallへ届くかを確かめる','Networkが先。不一致のWeb通信はApplicationへ','送信元・宛先・ポートと、許可の目的を結び付ける']},
}

chapters = [
('opening','今回の問い','Firewallの許可と通信全体の成立を分けて問いを立てる。',['fw-roles']),
('path','まず、Firewallを通るか','経路とポリシーの関連付けを説明する。',['fw-roles','fw-decisions']),
('kinds','NetworkとApplicationの違い','IP・ポートとWebホストの照合、Network FQDNを区別する。',['fw-roles']),
('order','種類の順序と優先度','Networkの一致で終了することと既定拒否を説明する。',['fw-evaluation']),
('internal','内部中継の許可を読む','AppGWからAPIM・ACAの代表経路と往復を追う。',['fw-internal','fw-decisions']),
('platform','APIMの基盤通信','Storageタグの例から基盤依存通信の条件を説明する。',['fw-apim-network','fw-decisions']),
('operations','管理VM・ACA・DNSの通信','NTP、管理、独自DNSとDNS proxyの役割を区別する。',['fw-vm-aca-network','fw-maintenance']),
('check','3つの確認問題','具体的な通信の判定理由を言葉にする。',['fw-decisions']),
('summary','保守の視点とまとめ','経路・規則・設定を照合し、外向き通信の次回へつなぐ。',['fw-maintenance']),
]

# chapter | speaker | visual | caption. Captions retain display names and products.
DATA = r'''
opening|mako|opening|Firewallで許可したのに、APIがつながらない。そんなとき、許可の書き方だけを見直せばいいんでしょうか。
opening|manabu|opening|まず、どこを通り、何の条件で許可されるかを分けましょう。必要な通信を通す理由も説明したいですね。
opening|ikumi|opening|今日はNetworkとApplicationを追います。Networkで許可されたら、FQDN照合へは進まない点が鍵です。
opening|mako|opening|えっ、両方で検査するわけではないんですね。内部のAPI中継と、基盤を支える通信から確かめていきます。
path|mako|path|第4回ではUDRと往復の経路を見ました。今回は、その経路の途中にいるFirewallが、何を判断するのかがテーマですね。
path|ikumi|path|はい。Azure FirewallにはFirewallポリシーを関連付けます。サブネットへ直接ポリシーを付けるのではなく、Firewallへ届いた通信を判定します。
path|manabu|path|ここを分けると、設定に許可が書いてあることと、実際にその許可を使ったことを、混同せずに済みます。障害のときにも大事な出発点です。
path|mako|direct|たとえばPrivate Endpointへ直接向かう通信なら、Firewallの許可一覧を見ても、その通信の検査内容は分からないんですね。
path|ikumi|direct|そうです。標準構成のAPIMからFoundryのPEや、ACAからSQLのPEへは、具体的な直接経路を選びます。Firewallを経由する通信とは分けます。
path|ikumi|path|今日の例は、教材とリポジトリの公開設定例に基づきます。稼働中Azureの取得値や、実際の疎通試験結果とは区別します。
path|mako|path|分かりました。経路を前提にしてから、判定する材料を見ましょう。まずNetworkルールには、どんな情報が渡るんですか。
kinds|ikumi|two|基本は、送信元のIP範囲、宛先のIPやサービス・タグ、TCPやUDPなどのプロトコル、宛先ポートです。通信の条件を組み合わせて照合します。
kinds|mako|two|一方のApplicationルールは、アプリの名前を見ているんでしょうか。それとも、通信先のWebサイトを見ているんですか。
kinds|ikumi|two|送信元とWebの接続先を照合します。条件はHTTPやHTTPS、ポート、FQDN。HTTPはHost、HTTPSはSNIを使います。
kinds|mako|tls|HTTPSのホスト名が分かるなら、その先のURLパスや、送る本文も、Firewallで細かく検査しているんですか。
kinds|ikumi|tls|標準ApplicationルールはterminateTLSがfalseです。TLSを復号しないため、HTTPSのURLパスや本文を復号して調べる設定ではありません。
kinds|manabu|tls|公開入口のWeb攻撃検査はWAF、APIの認証や利用許可はAPIMなどにも役割があります。宛先を通せることと、その要求を実行してよいことは別なんですね。
kinds|mako|ntp|もう一つ。管理VMの時刻同期には、ntp.ubuntu.comという名前が出てきます。FQDNが書いてあるなら、こちらもApplicationですか。
kinds|ikumi|ntp|いいえ、Networkです。DNSで名前をIPへ解決し、その宛先IPとUDP 123を照合します。WebのHostやSNIを見る方式とは違います。
kinds|mako|two|なるほど。IPとポートを軸にするNetwork、Webのホストを軸にするApplication。次は、両方のルールがあるときの順番ですね。
order|ikumi|order|今回の独自ルールはNetworkから評価します。Allowに一致すれば終了し、ApplicationのFQDN許可を重ねて要求しません。
order|mako|order|IPの条件に合っても、ホスト名の条件に合わなければ止まる、という二段階の関門を想像していました。それとは違うんですね。
order|manabu|order|はい。Networkの許可範囲を広げると、その範囲はApplicationで宛先名を絞る段階へ進まなくなります。別々の一覧として眺めるだけでは見落とします。
order|ikumi|hierarchy|階層は、ルールコレクショングループ、その中にコレクション、さらにルールです。AllowやDenyのActionはコレクションへ設定します。
order|mako|hierarchy|NSGでは、個々の規則の優先順位を見ました。Firewallでも、個々のルールに同じような番号を付ける、と考えていいですか。
order|ikumi|hierarchy|構造が違います。同じ種類の中でグループ、次にコレクションの小さい優先度から照合します。個々のルールにはNSGのような優先度を付けません。
order|mako|priority|では、Applicationのグループが350、Networkのグループが400なら、350のApplicationが先に評価されるんでしょうか。
order|ikumi|priority|種類が先なので、その例でもNetworkが先です。数値は全種類を混ぜて並べず、同じ種類の評価順を読むために使います。
order|manabu|priority|まず種別、その内側でグループとコレクション。なお、同じ360という優先度のグループ同士は、その数字だけで互いの前後を断定しないようにします。
order|mako|default|許可に一致しない場合はどうなりますか。この基盤には、最後に置くdeny-allという名前のルールがあるんでしょうか。
order|ikumi|default|独自定義はすべてAllowで、DenyやDNATはありません。Network、Application、Azure組み込み基盤用許可も評価し、最後まで許可がなければ既定拒否です。
internal|mako|internal|ここから具体例です。公開入口のAppGWから、内部のAPIMへ要求を中継します。この通信は、どの条件で許可するんですか。
internal|ikumi|internal|送信元はAppGW区画、宛先はAPIMの実内部VIP、通常はTCP 80です。図のNetworkルールで許可し、FQDN照合へは進みません。
internal|mako|internal|名前の末尾がhttpなので、443番は絶対に含まれない、という読み方でよさそうですか。
internal|ikumi|internal|名前だけでは決めません。gRPC有効時は同じルールへ内部TCP 443も追加します。無効時は含めず、他の許可にも一致しなければ拒否です。
internal|mako|portal|Portalへの中継も見せてください。AppGWからACAへ進む場合、ルールの宛先とUDRの宛先は、同じ書き方ですか。
internal|ikumi|portal|区別します。図の許可先はACA区画のCIDRで、TCP 443。一方、AppGWのUDRはACA入口の実IPへ、スラッシュ32の経路を作ります。
internal|mako|state|要求が許可されたら、応答のために、APIMからAppGWへ逆向きのAllowも一つ追加する必要があるんでしょうか。
internal|ikumi|state|許可済み接続の応答は、Firewallが状態を追跡して扱います。その応答だけの逆向きの新規接続Allowは不要です。ただし、復路も同じFirewallへ戻すUDRは必要です。
internal|manabu|state|状態を覚えることと、戻る経路を用意することは別ですね。逆向きの別の新規接続まで、自動的に許可されたと考えないことも大切です。
internal|ikumi|direct|APIMからACA内部IP、ACAからSQLやKey VaultのPEへの許可も定義されています。ただし標準UDRは直接経路なので、そのFirewallルールで検査するとは説明しません。
platform|mako|platform|次はAPIM自身が出す通信です。利用者のAPI要求以外にも、基盤として外へ接続する必要があるんですね。
platform|manabu|platform|はい。保存先、認証、監視や監査などの依存先が必要です。業務APIだけでなく、基盤を動かし続ける通信も考えます。
platform|ikumi|storage|代表例は図のapim-storageです。APIM区画からStorageタグのIPへ、TCP 443の通信をNetworkで許可します。
platform|mako|storage|このStorageという指定は、自分たちが作ったStorageアカウント一つを指しているんでしょうか。
platform|ikumi|storage|いいえ。サービス・タグは、そのサービスに関係するIPの集合です。自社のリソース一つだけを選ぶ指定とは区別します。
platform|mako|storage|そこでNetworkのAllowに一致した場合、Applicationのapim-approved-servicesにも、そのホスト名があるかを確認するんですか。
platform|ikumi|storage|重ねて要求しません。Networkの一致で終了です。HTTPSの443番だから必ずApplicationへ進む、という読み方も当てはまりません。
platform|manabu|storage|Web用FQDN一覧だけで許可先を判断すると、Networkのタグ許可を見落とします。両方を合わせて、実際の許可範囲を読みます。
platform|ikumi|platform|ほかにSqlタグのTCP 1433、Key Vault、Entra、監視・監査への許可などがあります。全ポートの暗記より、依存先の役割を結びましょう。
platform|mako|platform|apim-sqlという名前を見ると、PortalやBrokerが業務データを保存するSQL接続と、同じものだと思いそうです。
platform|ikumi|platform|これはAPIM内部プラットフォームの依存通信です。ACAから業務SQLのPEへ向かう接続とは、送信元も目的も違います。
operations|mako|time|さっきの時刻同期へ戻りましょう。APIMにも、管理VMと同じNTPの許可を、そのまま共用しているんですか。
operations|ikumi|time|APIM側は図のapim-platform-timeで、宛先はtime.windows.com、UDP 123です。管理VMの許可とは独立しています。
operations|mako|time|でも、APIMの時刻同期が入るコレクション名は、allow-vm-platformなんですね。名前だけ見ると管理VM専用に見えます。
operations|ikumi|time|ええ、実際の送信元条件を確かめます。管理VM側はntp.ubuntu.com。どちらも、DNSで解決したIPに対するNetworkの判定です。
operations|manabu|management|管理VMには、配備や保守の作業をする役割もあります。そのため、Entraの認証やAzure Resource Manager、監視、エージェントの保存先への通信を用途別に残しています。
operations|ikumi|management|ACA側も、図のNetworkルールで監視と認証を許可します。送信元はACA区画、宛先は監視とEntraのタグ、TCP 443です。
operations|mako|management|その通信もNetworkで一致したら、ApplicationのWeb許可へ進まない。名前にhttpsがあっても、ルール種別の確認が必要なんですね。
operations|ikumi|dns|そのとおりです。独自DNSを指定した場合にはcustom-dnsを作り、APIMと管理VM区画からDNSのIP群へ、TCPとUDPの53番を許可します。
operations|mako|dns|独自DNSを使うなら、ACAからの問い合わせも、この同じルールで自動的に許可されるんでしょうか。
operations|ikumi|dns|ACA区画は含みません。また、公開例のDNS配列は空で、その場合はコレクションを作りません。定義される条件も確認します。
operations|manabu|dns|さらに、同じVNet内のDNSへ直接向かう経路なら、その問い合わせはFirewallを通りません。DNSへの許可と、実際の名前解決経路も一緒に読むわけですね。
operations|mako|proxy|教材にはDNS proxyが有効ともあります。これを有効にすれば、VNet内の問い合わせ先が、自動的にFirewallへ切り替わるんですか。
operations|ikumi|proxy|自動では切り替わりません。proxyはNetworkルールのFQDN解決などを支えますが、VNetのDNS設定は別です。上流DNSと問い合わせ経路も確認します。
check|mako|q1|ここで確認問題です。APIMからStorageタグのIPへTCP 443で向かい、NetworkのAllowに一致しました。ApplicationのFQDN一覧にも登録が必要でしょうか。
check|ikumi|storage|追加で重ねて要求しない、が答えです。Network Allowで終了します。その許可範囲を広げる変更が、FQDN制御にも影響するわけです。
check|mako|q2|二問目です。管理VMからntp.ubuntu.comへ、UDP 123で時刻同期します。FQDNが書かれていても、これはApplicationの許可でしょうか。
check|ikumi|ntp|Networkです。DNSで解決したIPとUDP 123などを照合します。許可先以外のNTP通信を、ApplicationのWebルールで通すことはありません。
check|mako|q3|三問目です。標準UDRのACAからSQLのPEへ接続できません。FirewallにAllowを追加すれば直る、と判断してよいでしょうか。
check|ikumi|direct|標準例は直接経路です。宛先IP、Private DNS、NSGや認証などを確認します。経由しないFirewallへ許可を足すだけで直るとは判断できません。
check|mako|web|ではNetworkの一致がないWeb通信なら、Applicationへ進む。たとえば管理VMからarchive.ubuntu.comのHTTP 80ですね。
check|ikumi|web|はい、図のApplicationルールで許可する例です。未許可の外部ホストは、他の許可や組み込み許可にも一致しないことを確かめ、既定拒否と説明します。
summary|mako|maintain|最後に、基盤管理者が設定を見直す場面を整理しましょう。新しい接続先を許可してほしい、と依頼されたら、何をそろえますか。
summary|ikumi|maintain|実際の送信元IP、宛先IPやホスト名、プロトコルとポートです。同じ通信の経路、種別、グループ、コレクション、条件とActionを照合します。
summary|manabu|maintain|依頼の目的も合わせます。誰が何のために使う通信かを明確にし、必要な範囲を選ぶ。変更後は、新しい接続とログで意図した判定になったかを確認します。
summary|mako|threat|本番の公開設定には、PremiumやIDPS Alertという言葉もあります。このAlertは、ルールでDenyするのと同じ意味ですか。
summary|ikumi|threat|別の設定です。本番公開例のIDPSとThreat IntelligenceはAlert。検出だけで自動遮断するモードとは説明しません。詳しくは第10回で扱います。
summary|manabu|summary|今日の要点は三つです。まずFirewallを通るか。次にNetworkを先に照合し、一致すれば終了。そして送信元と宛先、ポートを、許可する目的に結び付けることです。
summary|mako|summary|これで、何を材料に、どの順番で判断するかがつながりました。次回は外向き通信と出口IPです。Webの依存先と、接続先から見える送信元IPを追っていきます。
'''

def units(s):
    return sum(.56 if ord(c)<128 else 1 for c in s)

def wrap(s, max_units=36):
    if units(s)<=max_units:
        return [s]
    options=[]
    for i in range(1,len(s)):
        if units(s[:i])>max_units or units(s[i:])>max_units:
            continue
        if s[i] in '、。！？ー・' or s[i].isspace():
            continue
        if re.match(r'[A-Za-z0-9./-]',s[i-1]) and re.match(r'[A-Za-z0-9./-]',s[i]):
            continue
        priority=100 if s[i-1] in '。！？' else 70 if s[i-1]=='、' else 0
        options.append((priority-abs(units(s[:i])-units(s[i:])),i))
    assert options, s
    cut=max(options)[1]
    return [s[:cut],s[cut:]]

def chunks(caption):
    sentences = re.findall(r'.+?[。！？]|.+$', caption)
    pieces=[]
    pending=''
    for sentence in sentences:
        if pending and units(pending+sentence)>68:
            pieces.append(pending)
            pending=''
        if units(sentence)>68:
            # Author-defined long explanations split at a clause boundary.
            clauses = re.findall(r'.+?[、。！？]|.+$', sentence)
            for clause in clauses:
                if pending and units(pending+clause)>68:
                    pieces.append(pending)
                    pending=''
                pending+=clause
        else:
            pending+=sentence
    if pending:
        pieces.append(pending)
    return [{'caption':p,'lines':wrap(p),'weight':len(p)} for p in pieces]

pronunciations = [
('vm-certificate-and-linux-packages','ブイエム・サーティフィケート・アンド・リナックス・パッケージズ'),
('apim-approved-services','エーピーアイエム・アプルーブド・サービシズ'),
('agw-to-apim-http','エージーダブリュー・トゥー・エーピーアイエム・エイチティーティーピー'),
('apim-platform-time','エーピーアイエム・プラットフォーム・タイム'),
('allow-vm-platform','アロー・ブイエム・プラットフォーム'),
('aca-monitor-https','エーシーエー・モニター・エイチティーティーピーエス'),
('management-time','マネジメント・タイム'),
('agw-aca-https','エージーダブリュー・エーシーエー・エイチティーティーピーエス'),
('apim-storage','エーピーアイエム・ストレージ'),('apim-sql','エーピーアイエム・エスキューエル'),
('custom-dns','カスタム・ディーエヌエス'),('network.bicep','ネットワーク・バイセップ'),
('ntp.ubuntu.com','エヌティーピー・ドット・ウブントゥ・ドット・コム'),
('time.windows.com','タイム・ドット・ウィンドウズ・ドット・コム'),
('archive.ubuntu.com','アーカイブ・ドット・ウブントゥ・ドット・コム'),
('AzureActiveDirectory','アジュール・アクティブ・ディレクトリー'),
('AzureMonitor','アジュール・モニター'),('Azure Resource Manager','アジュール・リソース・マネージャー'),
('Private Endpoint','プライベート・エンドポイント'),('Private DNS','プライベート・ディーエヌエス'),
('Key Vault','キー・ボールト'),('Threat Intelligence','スレット・インテリジェンス'),
('Application','アプリケーション'),('Network','ネットワーク'),('Firewall','ファイアウォール'),
('terminateTLS','ターミネート・ティーエルエス'),('false','フォールス'),('deny-all','デナイ・オール'),
('DNAT','ディーナット'),('IDPS','アイディーピーエス'),('Premium','プレミアム'),('Alert','アラート'),
('Allow','アロー'),('Deny','デナイ'),('Action','アクション'),('Internet','インターネット'),
('Storage','ストレージ'),('Sql','エスキューエル'),('SQL','エスキューエル'),('Entra','エントラ'),
('Foundry','ファウンドリー'),('Broker','ブローカー'),('Portal','ポータル'),('Host','ホスト'),
('HTTPS','エイチティーティーピーエス'),('HTTP','エイチティーティーピー'),('SNI','エスエヌアイ'),
('FQDN','エフキューディーエヌ'),('AppGW','アップ・ゲートウェイ'),('APIM','エーピーアイエム'),
('API','エーピーアイ'),('TCP','ティーシーピー'),('UDP','ユーディーピー'),('UDR','ユーディーアール'),
('CIDR','サイダー'),('gRPC','ジーアールピーシー'),('NTP','エヌティーピー'),('DNS','ディーエヌエス'),('proxy','プロキシー'),
('NSG','エヌエスジー'),('TLS','ティーエルエス'),('URL','ユーアールエル'),('VNet','ブイネット'),
('ACA','エーシーエー'),('VM','ブイエム'),('PE','ピーイー'),('IP','アイピー'),('WAF','ワフ'),
('Azure','アジュール'),('config','コンフィグ')]

turns=[]
for raw in DATA.strip().splitlines():
    chapter,speaker,visual,caption=raw.split('|',3)
    speech=caption
    for old,new in pronunciations:
        speech=speech.replace(old,new)
    cue_chunks=chunks(caption)
    assert ''.join(c['caption'] for c in cue_chunks)==caption
    assert all(len(c['lines'])<=2 for c in cue_chunks), (caption,cue_chunks)
    delivery={
      'mako':'視聴者の疑問や理解を確かめ、相手の返答へ自然につなぐ。問いの語尾だけを穏やかに上げる。',
      'manabu':'直前の発言を受け、必要な理由と管理者の判断を落ち着いて整理する。',
      'ikumi':'直前の問いに答え、技術用語の前後を少し区切る。数字・条件・例外を明瞭に、通常速度で説明する。'}[speaker]
    if chapter=='check' and speaker=='mako':
        delivery='確認問題を出す進行役として、問いを明瞭に読み、語尾に短い考える間を置く。'
    if visual=='q1' and speaker=='manabu' or visual=='q2' and speaker=='manabu' or visual=='q3' and speaker=='manabu':
        delivery='答えを急がず、考え方の手がかりを穏やかに渡す。通常速度を保つ。'
    turns.append({'id':f'lesson05-turn-{len(turns)+1:03}-{speaker}', 'speaker':speaker,
        'chapterId':chapter, 'speech':speech,'caption':caption,
        'subtitleLines':cue_chunks[0]['lines'],'subtitleChunks':cue_chunks,
        'gapAfterSeconds':.16,'delivery':delivery,'visual':copy.deepcopy(V[visual])})

anchors=['fw-roles','fw-evaluation','fw-internal','fw-apim-network','fw-vm-aca-network','fw-decisions','fw-maintenance']
source_ref=lambda anchor:{'file':'../01-network-control.html','anchor':anchor}
script={
  'schemaVersion':1, 'productionProfile':'study-series-v1', 'episodeKey':'05', 'episodeNumber':5,
  'episodeId':'05', 'title':'第5回 Firewallは何を判断するか', 'topic':'Firewallは何を判断するか',
  'description':'NetworkとApplicationの違い、評価順、内部中継・基盤依存通信の許可を3人の対話と図で追う。',
  'status':'ready-for-generation', 'cast':'podcast-cast-gemini.json','speed':1,'targetDurationSeconds':900,
  'durationPolicy':'通常速度の実音声で確定。尺合わせの加速や長い無音の追加は行わない。',
  'logoIntroSeconds':4,'logoClosingSeconds':8,'speechSettings':{'language':'ja-JP','speed':1,'enhancePronunciation':None},
  'scope':'2026-11-01時点の教材・リポジトリ定義・公開設定例に基づく。稼働中Azureの取得値・実測結果ではない。',
  'learningObjectives':['Network／Applicationルールの違いと評価順、内部中継・基盤通信の許可を説明できる。'],
  'sourceReferences':[source_ref(a) for a in anchors],
  'officialReferences':[
    {'url':'https://learn.microsoft.com/en-us/azure/firewall/rule-processing','purpose':'種別順、一致時の終了、組み込み許可と既定拒否'},
    {'url':'https://learn.microsoft.com/en-us/azure/firewall/domain-filtering-overview','purpose':'HTTP Host / HTTPS SNIの照合'},
    {'url':'https://learn.microsoft.com/en-us/azure/firewall/fqdn-filtering-network-rules','purpose':'Network FQDNのDNS解決による照合'}],
  'chapters':[{'id':i,'title':t,'goal':g,'sourceRefs':[source_ref(a) for a in refs]} for i,t,g,refs in chapters],
  'firstCut':{'omitTurnIds':[],'sourceRecordingsKept':True},'turns':turns}
(HERE/'script.json').write_text(json.dumps(script,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'turns':len(turns),'captionCharacters':sum(len(t['caption']) for t in turns),
                  'speechCharacters':sum(len(t['speech']) for t in turns),
                  'openingCharacters':sum(len(t['speech']) for t in turns if t['chapterId']=='opening')},ensure_ascii=False))
