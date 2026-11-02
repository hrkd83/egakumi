# egakumi-core 仕様勉強会

企業・組織でAIを利用するための基盤を、開発者・インフラエンジニア・データエンジニアなどの基盤管理者に向けて解説します。[YouTube動画の受講順と各回の対応資料](VIDEOS.md)から、動画と実装・設定の両方を参照できます。[リポジトリの概要](../../README.md)も確認してください。

スライドに近い紙面を縦スクロールで読む資料です。[学習ガイド](index.html)からカテゴリを選びます。

[動画のコンテンツ計画](CONTENT-PLAN.md)に、15分前後の必修25回（第1〜23回とハブ＆スポーク対応A1・A2）、Portal実装編3回（B1〜B3）、応用2回（A3・A4）の順番、到達点、元資料との対応をまとめています。第8回の後にハブ＆スポーク対応、第18回の後にER図・フォルダー構造・関数の実装を読むPortal実装編へ進みます。

新規セッションで動画を制作する場合は、この計画ファイルと回番号を指定します。[共通の制作手順](video-sample/PRODUCTION-GUIDE.md)に、採用音声・デザイン・素材、回別の入力・出力、並列制作、検証までをまとめています。

本編は採用済みの[教材用設定ビュー](video-sample/SETTINGS-VIEW.md)を使います。固定カメラで質問から回答まで同じ画面を保ち、赤枠と補助図を説明に合わせます。新規回のひな型・共有レイアウトにもこの方針を反映しています。

[動画制作ツールのREADME](video-sample/README.md)を入口に、準備、台本・図の執筆、音声生成、映像合成、検証、完成後の整理を進めます。音声はGemini 3.8 Flash TTS、MAKO=Aoede・MANABU=Charon・IKUMI=Orus、全員通常速度（1.0倍）です。共通素材・設定は[制作プロファイル](video-sample/production-profile.json)で固定します。

各回の完成状態・検証結果・再生先は、担当回の`video-sample/episodes/<key>/production.md`に記録します。完成動画・生成原音・再制作用データを保持し、[完成後のデータ整理](CONTENT-PLAN.md#完成後のデータ整理)は完成・検証済みの担当回だけを対象にします。

| カテゴリ | 状態 | 内容 |
| --- | --- | --- |
| [01 ネットワークの制御](01-network-control.html) | ネットワーク制御を掲載 | VNet・DNS・PE、NSG・UDR・Firewall、出口IP/SNAT・監視・保守接続、全体図、ハブ接続 |
| [02 外部攻撃への対策](02-external-protection.html) | WAF・IDPS・APIMを掲載 | WAFの設定・誤検知調整、IDPSの検知と検査範囲、APIMサブスクリプション認証・入力検証・利用制御、ログと保守 |
| [03 Portalのサービスと実装](03-subscription-management.html) | 基盤管理者向け教材 | サービスの目的、フォルダ設計、担当ファイル、SQL、APIM、配備 |
| [04 Credential BrokerとAPIM Credential Manager](04-credential-management.html) | 基盤管理者向け教材 | 本人接続、CMのOAuth管理、接続証明の更新・失効、Broker SQL、Container AppとJob |

## 01の構成

読者は、開発者・インフラエンジニア・データエンジニアなど、egakumiの基盤管理者です。サービスを安全に稼働させるためのネットワーク基盤として、まずVNetの構造を説明します。

1. 安全な稼働とネットワーク区画の関係。
2. VNet・アドレス空間・サブネットの包含関係と構造図。
3. 4つのアドレス空間と、予備範囲・CIDRの読み方。
4. 6つのサブネット名、CIDR、配置するリソースと役割。
5. 公開設定例・入力検査・Bicep定義との対応。
6. VNetとFirewallのDNS、4つのPE、6つのPrivate Linkゾーン、APIM・ACA内部DNS、変更時の確認。
7. 6サブネットと4NSGの関連付け、方向ごとの終端拒否。
8. 優先順位・応答通信・サービス・タグ・共通DNS。
9. AppGW・APIM・PE・管理VMの許可・拒否規則と目的。
10. 設定による切り替え、通信例、保守時の照合先。
11. 6サブネットと4ルートテーブルの関連付け。
12. 宛先による経路選択、標準8経路の転送先と目的、AppGWとの往復経路。
13. PEへの直接経路、APIM管理の例外、BGP伝搬、再配備時の既存経路保持。
14. FirewallのNetwork・Applicationルールの役割と評価順、グループとコレクション。
15. 内部中継・Azure基盤・NTP・DNS・Web依存・Portal・ビルドの許可規則と目的。
16. 固定・生成FQDN、追加ルール、既定拒否の例、DNS・TLS・脅威検出設定の区別。
17. 暗黙の外向き接続の無効化、公開IP、Network/ApplicationのSNAT、出口IPの期待値と実測の区別。
18. 通信ログと保持、許可・拒否の調査、障害・構成変更の監視と通知対象の範囲。
19. 管理VMのprivate接続、運用元NSG、公開鍵とEntra SSH、Managed Run Commandの分担。
20. 全体構成図、外向き・管理・DNSの補助図、経路ごとの制御対応。
21. 章末のハブ＆スポーク対応：接続範囲、ピアリング、区画別UDR、中央DNSと8ゾーンの管理、NSG・FW・SNAT、Gateway経路伝搬、切替・検証・再配備で変更する点と維持する点。

名前とIP範囲は、2026-11-01に参照した公開設定例と現行リポジトリ定義に基づきます。稼働中Azureの取得値ではありません。VNet構造図は配置とアドレス範囲を示します。DNSは6つのPrivate LinkゾーンとAPIM・ACAの2ゾーン、PEのzone group、VNetリンク、内部Aレコードを分けて説明します。NSGは基盤の静的規則に加え、実IP確定後の規則、ACR・SQL・Key Vault・認証窓口の追加規則も扱います。AppGWとPEの送信には独自の終端拒否を置いていない点を区別します。UDRは標準8経路の転送先と目的、往復の対称性、既定経路より具体的な経路の選択を扱います。UDR往復図に加え、全体図と外向き・管理・DNSの補助図を置きます。緑の実線はFirewall経由、青の破線は直接経路で、D1〜D6を対応表へ結びます。Firewallは独自のAllow規則と既定拒否を説明します。Network一致後にApplicationを重ねて評価しないこと、組み込み基盤用許可、API公開で追加・保持される宛先、PE直接経路とFirewall経由の違いを区別します。出口IP/SNAT、ログ収集と自動通知、SSHと管理面の実行を分けて説明します。FW非経由の通信をFWログで観測済みとは扱わず、出口IPの期待値を自動実測済みとも扱いません。IDPSによる攻撃検出の詳細は02章で扱います。

## 02の構成

読者は、開発者・インフラエンジニア・データエンジニアなど、egakumiの基盤管理者です。一つの要求について、どの経路を通り、各機能が何を判断し、どこまで到達したかを説明できる構成にしています。

1. 3つの配置と4つの判断：WAF、FW通信規則、並列に動くIDPS、APIM。
2. 通常API・Portal・gRPCの転送経路とHTTP / TLS境界、通常要求の到達段階。
3. WAF：管理ルール、サイズ、URI別ポリシーの置換、限定した除外とraw本文の調整。
4. FW IDPS：本番Premium / AlertとテストStandard、署名による検知・遮断、方向分類と例外。
5. APIM：継承と実行順、APIMサブスクリプションキー認証、API契約、専用入力ガード、回数・トークン制限、下流認証の準備。
6. 共通の調査と保守：ログの相関キー、層別KQL、正本の変更・計画・読み戻し・復元、担当ファイル。
7. 文書APIの400とacceptedの痕跡、WAF拒否とAPIM未到達の例で全体を確認。

全22節です。役割比較は導入、KQLは共通調査の層別詳細欄、変更手順・担当ファイルは章末へまとめています。旧セクションのIDは統合先に残しています。図と本文は、物理的な転送経路、FW内部の並列検知、APIMのスコープ展開を分けて説明します。

値は2026-11-01時点の作業ツリーのconfigと公開params例に基づき、稼働中Azureから取得した値ではありません。WAFは2000 KiBの共通本文枠と4 MiBのファイル部分上限、IDPSは本番Alert、APIMの共通回数制限はconfigで無効です。APIMサブスクリプションプロファイル接続が有効な場合のSQL値は取得していません。

APIMの通常OperationはbaseでAPI inboundを先に実行するため、利用枠と下流認証準備の後に固有入力検査へ戻ります。固有検査で拒否しても計数・内部照会やaccepted記録が先行する場合があります。SSML宣言ガードはbase前、文書解析はinbound内で業務呼出しを行う例外を明示します。WAFとAPIMで同じ4 MiBでも、ファイル部分と要求本文全体で対象が異なる点も説明します。

詳細欄は見出しを選ぶと展開します。印刷時は必要な詳細を開いてからプレビューを確認してください。

## 03の構成

読者は、Portalの開発・運用に関わる基盤管理者です。最初にPortalとJobの実行構成を示し、以降は機能ごとに説明を完結させます。

1. PortalとJobの全体像、担当リソース、フォルダ構造。
2. 認証機能：利用者を識別する目的、Better Auth、認証4表のER図、所属グループを通じたAPIキーとの対応。
3. 会社情報の登録：受益者負担に沿う費用帰属のための会社・キーの対応、データ構造、登録・変更・選択停止。
4. APIキーの払い出し：会社・所有グループ・属性の登録、必要な5表、SQL予約からAPIMサブスクリプションの作成・SQL確定まで。
5. 仕様確認、API試用、発行後の管理、Graph本人接続、制限、組織観測、同期、配備をそれぞれ独立した機能として説明。
6. 最後に全19表のER図をまとめ、全体の参照関係を確認。

各機能には、目的、必要なデータとその範囲のER図、操作、担当ファイル、処理後の状態を置きます。章内リンクでデータ・操作・ファイルへ移動でき、別の機能をすべて通読しなくても参照できます。

認証・会社・制限には機能用のER図を用意しています。全体ER図は末尾に集めます。どの図も表名の下へ保存目的を記載します。

資料は1本です。別の実装リファレンスへ分けません。ソースコードやSQL全文は転載せず、ファイルの役割を示します。既存URLを継続するため、ファイル名は03-subscription-management.htmlを使います。

## 04の構成

読者は、Gatewayと本人接続の開発・運用に関わる基盤管理者です。最初にCMが提供するトークン管理、Brokerを加える理由、APIM・呼出アプリとの役割分担を説明します。その後に実行構成とフォルダ設計を示し、機能ごとに目的、データ、操作、担当ファイルを説明します。

1. CMの機能、本人・APIキー・CM接続の対応をBrokerで管理する理由、役割分担。APIキー・接続証明・アクセストークンの発行・保存・検証を図で示す。
2. 全体構成、Portalログイン・APIキー・外部APIの本人接続の役割。
3. CM provider・connection・access policyと、SQLのプール・本人接続割当。
4. 本人接続の開始：BrokerのEntra認証、CM同意、本人一致、Portal側の証明保存。初回のシーケンス図は、認証案内・本人確認とCM同意・完了確認の3段階に分ける。
5. API利用：APIMサブスクリプションと証明の照合、CMトークン取得、本人確認、業務利用の確定。接続済みのシーケンス図で2回のGraph呼び出しと資格情報の経路を示す。
6. 接続証明の期限と更新：候補を先に保存してからcommitする処理。
7. 単一セッションの終了、本人全体の終了、CM接続解除の違い。
8. 毎時17分のrecon JobによるCM照合、削除回収、期限済みデータの整理。
9. schema Job、配備順序、MI・SQL・CMの権限、適用済みSQLを再実行しない処理。
10. 公開APIから内部処理への対応と保守時の確認箇所。
11. 末尾にBroker全7表のER図と、CM・Broker SQL・Portal SQLの保存範囲。

CMのOAuthトークンとBrokerの接続証明を区別します。全体ER図と機能別ER図には各表の保存目的を記載し、物理FKとアプリの照合を描き分けています。ソースコードやSQL全文は転載しません。

MicrosoftのCM・ポリシー・REST API仕様へのリンクを、対応する説明の近くに置いています。実装説明は2026-11-01に参照したリポジトリに基づき、稼働中Azureの設定取得や実機認証の実行結果ではありません。

## 閲覧

index.htmlまたは各HTMLをブラウザーで開きます。ビルドは不要です。本文・図・CSS・JavaScriptはローカルで表示できます。狭い画面では「目次」から移動し、表・図は必要に応じて横スクロールできます。

HTTPで確認する場合はリポジトリのルートで起動します。

    python3 -m http.server 8000 --bind 127.0.0.1

http://localhost:8000/docs/study/ を開きます。終了はCtrl+Cです。

「印刷 / PDF」はA4横を基本にします。長いセクションは複数枚へ続くため、印刷プレビューを確認してください。

## 編集方針

- 読者は、開発者・インフラエンジニア・データエンジニアなどを含む「基盤管理者」と表記します。保守作業・保守接続など、作業そのものを指す語とは区別します。
- APIMの利用許可は「APIMサブスクリプション」、そのキーとIDは「APIMサブスクリプションキー」「APIMサブスクリプションID」と表記します。Azureリソースの課金・管理単位であるAzureサブスクリプションとは区別します。実装上の識別子・ヘッダー名・URLは変更しません。
- 最初に実行構成の全体像とフォルダ構造を示します。以降は機能単位で、目的 → データ構造・ER図 → 操作 → 担当ファイルまで完結させます。複数機能を一続きの説明にしません。
- libなどのフォルダについて、何を置く場所か、他のフォルダとどう分担するかを説明します。
- ファイル単位で処理の目的を示します。個々の関数やソースコードの逐行説明は扱いません。
- 見出しは具体的な機能名・処理名にし、教材側の宣言や大げさな表現を避けます。
- 使用している構成要素とロジックを説明します。未使用技術の説明を増やしません。
- 機能内のER図は、その機能に必要な範囲を表示します。全体ER図は最後にまとめます。各表の目的を本文と図内に記載し、物理FKとGraph等による論理的な対応を区別します。
- 対象実装は5ae9c99（2026-11-01参照）です。変更時はファイルの説明・SQL構造・Azure設定の対応を一緒に更新します。
- 会社は費用帰属の分類、Entraグループは所有と操作権限です。キー実値はAPIM、台帳・監査はSQLに保存します。

## Azureポータルの画面を追加する

本文の「Azureポータル画面の追加位置」に、実際の画面を挿入します。03の対象はAPIM製品・APIMサブスクリプション・IAM、利用制限用のNamed valuesとポリシーです。04にはCM provider・connection・access policy、Graphの有効なAPIMポリシー、Broker Container Appと2つのJobの追加位置があります。必要に応じてSQLの説明にも追加します。

画像はassets/screenshots/などへ保存し、対応するfigure内へimgを置きます。キャプションには、画面の設定項目、対応するファイル、確認する値の意味を記載します。実画面を取得していない段階では、設定済み・検証済みと記載しません。

## ファイル

- index.html：学習ガイド。
- 01-network-control.html：ネットワーク制御の教材。VNet・DNS・PE、NSG・UDR・Firewall、出口IP/SNAT・監視・保守接続、全体図、ハブ接続時の変更点を掲載。
- 02-external-protection.html：外部攻撃対策の教材。WAF・Firewall IDPS・APIMのAPIポリシーを掲載。
- 03-subscription-management.html：統合したPortalの基盤管理者向け教材。
- 04-credential-management.html：Credential BrokerとAPIM Credential Managerの基盤管理者向け教材。
- assets/study.css、study.js：見た目・目次・ページ番号。
- assets/network.css、network-vnet-structure.svg、network-udr-paths.svg：01章の表・VNet構造図・UDR往復経路図。
- assets/network-overview.svg、network-egress-management.svg：01章の全体図と外向き・管理・DNSの補助図。
- assets/external-protection.css、external-ingress.svg、firewall-idps-flow.svg、apim-policy-flow.svg：02章の設定表、入口構成図、IDPSの処理図、APIMの通常HTTP処理図。
- assets/subscription-er.svg：主要5表。
- assets/subscription-supporting-er.svg：補助8表と参照先。
- assets/portal-related-er.svg：認証・本人接続・版管理の6表。
- assets/portal-authentication-er.svg：認証機能内の4表。
- assets/portal-company-er.svg：会社登録機能内の会社・会社監査・キー。
- assets/portal-limit-er.svg：利用制限機能内のキー・制限・制限変更監査。
- assets/subscription-runtime.svg、subscription-image-flow.svg：実行構成とイメージ配布。
- assets/credential.css、credential-runtime.svg：04章の表示調整と実行構成。
- assets/credential-roles.svg：APIキー・接続証明・アクセストークンの発行元・保存先・検証先と、要求で渡す情報。
- assets/credential-initial-start-sequence.svg、credential-initial-login-sequence.svg、credential-initial-complete-sequence.svg：初回本人接続の3段階のシーケンス図。
- assets/credential-connected-sequence.svg：接続済みAPI呼び出しと、必要時のCMトークン更新。
- assets/credential-pool-data.svg、credential-login-data.svg、credential-session-data.svg：04章の機能別ER図。
- assets/credential-broker-er.svg：Broker SQL全7表のER図。

ER図は外部CDNを使わないSVGです。テーブルの定義と目的、関係線を本文と合わせて更新します。目次・ページ番号はJavaScriptが更新しますが、静的なHTMLの目次と番号も一致させます。
