# 第5回 送信する読み上げ原稿の確認

Gemini 3.8 Flash TTS（Google Cloud、egakumi-example / developer@example.com）へ送る80台詞。全員通常速度。下記は字幕用の表記です。実際の読み・話し方と直前台詞の文脈指示はgeneration.jsonにあります。

## 今回の問い

**lesson05-turn-001-mako / MAKO**

Firewallで許可したのに、APIがつながらない。そんなとき、許可の書き方だけを見直せばいいんでしょうか。

**lesson05-turn-002-manabu / MANABU**

まず、どこを通り、何の条件で許可されるかを分けましょう。必要な通信を通す理由も説明したいですね。

**lesson05-turn-003-ikumi / IKUMI**

今日はNetworkとApplicationを追います。Networkで許可されたら、FQDN照合へは進まない点が鍵です。

**lesson05-turn-004-mako / MAKO**

えっ、両方で検査するわけではないんですね。内部のAPI中継と、基盤を支える通信から確かめていきます。

## まず、Firewallを通るか

**lesson05-turn-005-mako / MAKO**

第4回ではUDRと往復の経路を見ました。今回は、その経路の途中にいるFirewallが、何を判断するのかがテーマですね。

**lesson05-turn-006-ikumi / IKUMI**

はい。Azure FirewallにはFirewallポリシーを関連付けます。サブネットへ直接ポリシーを付けるのではなく、Firewallへ届いた通信を判定します。

**lesson05-turn-007-manabu / MANABU**

ここを分けると、設定に許可が書いてあることと、実際にその許可を使ったことを、混同せずに済みます。障害のときにも大事な出発点です。

**lesson05-turn-008-mako / MAKO**

たとえばPrivate Endpointへ直接向かう通信なら、Firewallの許可一覧を見ても、その通信の検査内容は分からないんですね。

**lesson05-turn-009-ikumi / IKUMI**

そうです。標準構成のAPIMからFoundryのPEや、ACAからSQLのPEへは、具体的な直接経路を選びます。Firewallを経由する通信とは分けます。

**lesson05-turn-010-ikumi / IKUMI**

今日の例は、教材とリポジトリの公開設定例に基づきます。稼働中Azureの取得値や、実際の疎通試験結果とは区別します。

**lesson05-turn-011-mako / MAKO**

分かりました。経路を前提にしてから、判定する材料を見ましょう。まずNetworkルールには、どんな情報が渡るんですか。

## NetworkとApplicationの違い

**lesson05-turn-012-ikumi / IKUMI**

基本は、送信元のIP範囲、宛先のIPやサービス・タグ、TCPやUDPなどのプロトコル、宛先ポートです。通信の条件を組み合わせて照合します。

**lesson05-turn-013-mako / MAKO**

一方のApplicationルールは、アプリの名前を見ているんでしょうか。それとも、通信先のWebサイトを見ているんですか。

**lesson05-turn-014-ikumi / IKUMI**

送信元とWebの接続先を照合します。条件はHTTPやHTTPS、ポート、FQDN。HTTPはHost、HTTPSはSNIを使います。

**lesson05-turn-015-mako / MAKO**

HTTPSのホスト名が分かるなら、その先のURLパスや、送る本文も、Firewallで細かく検査しているんですか。

**lesson05-turn-016-ikumi / IKUMI**

標準ApplicationルールはterminateTLSがfalseです。TLSを復号しないため、HTTPSのURLパスや本文を復号して調べる設定ではありません。

**lesson05-turn-017-manabu / MANABU**

公開入口のWeb攻撃検査はWAF、APIの認証や利用許可はAPIMなどにも役割があります。宛先を通せることと、その要求を実行してよいことは別なんですね。

**lesson05-turn-018-mako / MAKO**

もう一つ。管理VMの時刻同期には、ntp.ubuntu.comという名前が出てきます。FQDNが書いてあるなら、こちらもApplicationですか。

**lesson05-turn-019-ikumi / IKUMI**

いいえ、Networkです。DNSで名前をIPへ解決し、その宛先IPとUDP 123を照合します。WebのHostやSNIを見る方式とは違います。

**lesson05-turn-020-mako / MAKO**

なるほど。IPとポートを軸にするNetwork、Webのホストを軸にするApplication。次は、両方のルールがあるときの順番ですね。

## 種類の順序と優先度

**lesson05-turn-021-ikumi / IKUMI**

今回の独自ルールはNetworkから評価します。Allowに一致すれば終了し、ApplicationのFQDN許可を重ねて要求しません。

**lesson05-turn-022-mako / MAKO**

IPの条件に合っても、ホスト名の条件に合わなければ止まる、という二段階の関門を想像していました。それとは違うんですね。

**lesson05-turn-023-manabu / MANABU**

はい。Networkの許可範囲を広げると、その範囲はApplicationで宛先名を絞る段階へ進まなくなります。別々の一覧として眺めるだけでは見落とします。

**lesson05-turn-024-ikumi / IKUMI**

階層は、ルールコレクショングループ、その中にコレクション、さらにルールです。AllowやDenyのActionはコレクションへ設定します。

**lesson05-turn-025-mako / MAKO**

NSGでは、個々の規則の優先順位を見ました。Firewallでも、個々のルールに同じような番号を付ける、と考えていいですか。

**lesson05-turn-026-ikumi / IKUMI**

構造が違います。同じ種類の中でグループ、次にコレクションの小さい優先度から照合します。個々のルールにはNSGのような優先度を付けません。

**lesson05-turn-027-mako / MAKO**

では、Applicationのグループが350、Networkのグループが400なら、350のApplicationが先に評価されるんでしょうか。

**lesson05-turn-028-ikumi / IKUMI**

種類が先なので、その例でもNetworkが先です。数値は全種類を混ぜて並べず、同じ種類の評価順を読むために使います。

**lesson05-turn-029-manabu / MANABU**

まず種別、その内側でグループとコレクション。なお、同じ360という優先度のグループ同士は、その数字だけで互いの前後を断定しないようにします。

**lesson05-turn-030-mako / MAKO**

許可に一致しない場合はどうなりますか。この基盤には、最後に置くdeny-allという名前のルールがあるんでしょうか。

**lesson05-turn-031-ikumi / IKUMI**

独自定義はすべてAllowで、DenyやDNATはありません。Network、Application、Azure組み込み基盤用許可も評価し、最後まで許可がなければ既定拒否です。

## 内部中継の許可を読む

**lesson05-turn-032-mako / MAKO**

ここから具体例です。公開入口のAppGWから、内部のAPIMへ要求を中継します。この通信は、どの条件で許可するんですか。

**lesson05-turn-033-ikumi / IKUMI**

送信元はAppGW区画、宛先はAPIMの実内部VIP、通常はTCP 80です。図のNetworkルールで許可し、FQDN照合へは進みません。

**lesson05-turn-034-mako / MAKO**

名前の末尾がhttpなので、443番は絶対に含まれない、という読み方でよさそうですか。

**lesson05-turn-035-ikumi / IKUMI**

名前だけでは決めません。gRPC有効時は同じルールへ内部TCP 443も追加します。無効時は含めず、他の許可にも一致しなければ拒否です。

**lesson05-turn-036-mako / MAKO**

Portalへの中継も見せてください。AppGWからACAへ進む場合、ルールの宛先とUDRの宛先は、同じ書き方ですか。

**lesson05-turn-037-ikumi / IKUMI**

区別します。図の許可先はACA区画のCIDRで、TCP 443。一方、AppGWのUDRはACA入口の実IPへ、スラッシュ32の経路を作ります。

**lesson05-turn-038-mako / MAKO**

要求が許可されたら、応答のために、APIMからAppGWへ逆向きのAllowも一つ追加する必要があるんでしょうか。

**lesson05-turn-039-ikumi / IKUMI**

許可済み接続の応答は、Firewallが状態を追跡して扱います。その応答だけの逆向きの新規接続Allowは不要です。ただし、復路も同じFirewallへ戻すUDRは必要です。

**lesson05-turn-040-manabu / MANABU**

状態を覚えることと、戻る経路を用意することは別ですね。逆向きの別の新規接続まで、自動的に許可されたと考えないことも大切です。

**lesson05-turn-041-ikumi / IKUMI**

APIMからACA内部IP、ACAからSQLやKey VaultのPEへの許可も定義されています。ただし標準UDRは直接経路なので、そのFirewallルールで検査するとは説明しません。

## APIMの基盤通信

**lesson05-turn-042-mako / MAKO**

次はAPIM自身が出す通信です。利用者のAPI要求以外にも、基盤として外へ接続する必要があるんですね。

**lesson05-turn-043-manabu / MANABU**

はい。保存先、認証、監視や監査などの依存先が必要です。業務APIだけでなく、基盤を動かし続ける通信も考えます。

**lesson05-turn-044-ikumi / IKUMI**

代表例は図のapim-storageです。APIM区画からStorageタグのIPへ、TCP 443の通信をNetworkで許可します。

**lesson05-turn-045-mako / MAKO**

このStorageという指定は、自分たちが作ったStorageアカウント一つを指しているんでしょうか。

**lesson05-turn-046-ikumi / IKUMI**

いいえ。サービス・タグは、そのサービスに関係するIPの集合です。自社のリソース一つだけを選ぶ指定とは区別します。

**lesson05-turn-047-mako / MAKO**

そこでNetworkのAllowに一致した場合、Applicationのapim-approved-servicesにも、そのホスト名があるかを確認するんですか。

**lesson05-turn-048-ikumi / IKUMI**

重ねて要求しません。Networkの一致で終了です。HTTPSの443番だから必ずApplicationへ進む、という読み方も当てはまりません。

**lesson05-turn-049-manabu / MANABU**

Web用FQDN一覧だけで許可先を判断すると、Networkのタグ許可を見落とします。両方を合わせて、実際の許可範囲を読みます。

**lesson05-turn-050-ikumi / IKUMI**

ほかにSqlタグのTCP 1433、Key Vault、Entra、監視・監査への許可などがあります。全ポートの暗記より、依存先の役割を結びましょう。

**lesson05-turn-051-mako / MAKO**

apim-sqlという名前を見ると、PortalやBrokerが業務データを保存するSQL接続と、同じものだと思いそうです。

**lesson05-turn-052-ikumi / IKUMI**

これはAPIM内部プラットフォームの依存通信です。ACAから業務SQLのPEへ向かう接続とは、送信元も目的も違います。

## 管理VM・ACA・DNSの通信

**lesson05-turn-053-mako / MAKO**

さっきの時刻同期へ戻りましょう。APIMにも、管理VMと同じNTPの許可を、そのまま共用しているんですか。

**lesson05-turn-054-ikumi / IKUMI**

APIM側は図のapim-platform-timeで、宛先はtime.windows.com、UDP 123です。管理VMの許可とは独立しています。

**lesson05-turn-055-mako / MAKO**

でも、APIMの時刻同期が入るコレクション名は、allow-vm-platformなんですね。名前だけ見ると管理VM専用に見えます。

**lesson05-turn-056-ikumi / IKUMI**

ええ、実際の送信元条件を確かめます。管理VM側はntp.ubuntu.com。どちらも、DNSで解決したIPに対するNetworkの判定です。

**lesson05-turn-057-manabu / MANABU**

管理VMには、配備や保守の作業をする役割もあります。そのため、Entraの認証やAzure Resource Manager、監視、エージェントの保存先への通信を用途別に残しています。

**lesson05-turn-058-ikumi / IKUMI**

ACA側も、図のNetworkルールで監視と認証を許可します。送信元はACA区画、宛先は監視とEntraのタグ、TCP 443です。

**lesson05-turn-059-mako / MAKO**

その通信もNetworkで一致したら、ApplicationのWeb許可へ進まない。名前にhttpsがあっても、ルール種別の確認が必要なんですね。

**lesson05-turn-060-ikumi / IKUMI**

そのとおりです。独自DNSを指定した場合にはcustom-dnsを作り、APIMと管理VM区画からDNSのIP群へ、TCPとUDPの53番を許可します。

**lesson05-turn-061-mako / MAKO**

独自DNSを使うなら、ACAからの問い合わせも、この同じルールで自動的に許可されるんでしょうか。

**lesson05-turn-062-ikumi / IKUMI**

ACA区画は含みません。また、公開例のDNS配列は空で、その場合はコレクションを作りません。定義される条件も確認します。

**lesson05-turn-063-manabu / MANABU**

さらに、同じVNet内のDNSへ直接向かう経路なら、その問い合わせはFirewallを通りません。DNSへの許可と、実際の名前解決経路も一緒に読むわけですね。

**lesson05-turn-064-mako / MAKO**

教材にはDNS proxyが有効ともあります。これを有効にすれば、VNet内の問い合わせ先が、自動的にFirewallへ切り替わるんですか。

**lesson05-turn-065-ikumi / IKUMI**

自動では切り替わりません。proxyはNetworkルールのFQDN解決などを支えますが、VNetのDNS設定は別です。上流DNSと問い合わせ経路も確認します。

## 3つの確認問題

**lesson05-turn-066-mako / MAKO**

ここで確認問題です。APIMからStorageタグのIPへTCP 443で向かい、NetworkのAllowに一致しました。ApplicationのFQDN一覧にも登録が必要でしょうか。

**lesson05-turn-067-ikumi / IKUMI**

追加で重ねて要求しない、が答えです。Network Allowで終了します。その許可範囲を広げる変更が、FQDN制御にも影響するわけです。

**lesson05-turn-068-mako / MAKO**

二問目です。管理VMからntp.ubuntu.comへ、UDP 123で時刻同期します。FQDNが書かれていても、これはApplicationの許可でしょうか。

**lesson05-turn-069-ikumi / IKUMI**

Networkです。DNSで解決したIPとUDP 123などを照合します。許可先以外のNTP通信を、ApplicationのWebルールで通すことはありません。

**lesson05-turn-070-mako / MAKO**

三問目です。標準UDRのACAからSQLのPEへ接続できません。FirewallにAllowを追加すれば直る、と判断してよいでしょうか。

**lesson05-turn-071-ikumi / IKUMI**

標準例は直接経路です。宛先IP、Private DNS、NSGや認証などを確認します。経由しないFirewallへ許可を足すだけで直るとは判断できません。

**lesson05-turn-072-mako / MAKO**

ではNetworkの一致がないWeb通信なら、Applicationへ進む。たとえば管理VMからarchive.ubuntu.comのHTTP 80ですね。

**lesson05-turn-073-ikumi / IKUMI**

はい、図のApplicationルールで許可する例です。未許可の外部ホストは、他の許可や組み込み許可にも一致しないことを確かめ、既定拒否と説明します。

## 保守の視点とまとめ

**lesson05-turn-074-mako / MAKO**

最後に、基盤管理者が設定を見直す場面を整理しましょう。新しい接続先を許可してほしい、と依頼されたら、何をそろえますか。

**lesson05-turn-075-ikumi / IKUMI**

実際の送信元IP、宛先IPやホスト名、プロトコルとポートです。同じ通信の経路、種別、グループ、コレクション、条件とActionを照合します。

**lesson05-turn-076-manabu / MANABU**

依頼の目的も合わせます。誰が何のために使う通信かを明確にし、必要な範囲を選ぶ。変更後は、新しい接続とログで意図した判定になったかを確認します。

**lesson05-turn-077-mako / MAKO**

本番の公開設定には、PremiumやIDPS Alertという言葉もあります。このAlertは、ルールでDenyするのと同じ意味ですか。

**lesson05-turn-078-ikumi / IKUMI**

別の設定です。本番公開例のIDPSとThreat IntelligenceはAlert。検出だけで自動遮断するモードとは説明しません。詳しくは第10回で扱います。

**lesson05-turn-079-manabu / MANABU**

今日の要点は三つです。まずFirewallを通るか。次にNetworkを先に照合し、一致すれば終了。そして送信元と宛先、ポートを、許可する目的に結び付けることです。

**lesson05-turn-080-mako / MAKO**

これで、何を材料に、どの順番で判断するかがつながりました。次回は外向き通信と出口IPです。Webの依存先と、接続先から見える送信元IPを追っていきます。
