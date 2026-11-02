# egakumi勉強会動画のコンテンツ計画

2026-11-01（JST）に合意した構成を、2026-11-02にハブ＆スポーク対応とPortal実装編を追加して更新しました。同日に採用した第1回の[設定メニュー版の採用仕様](video-sample/SETTINGS-VIEW.md)を、第2回以降の画面・編集方針の基準にします。ネットワークから始め、**通信経路 → ハブ＆スポーク対応 → 各層の防御 → Portalでの管理 → Portalのデータ設計・実装 → 本人接続**の順に学びます。必修25回（第1〜23回とA1・A2）にPortal実装編3回（B1〜B3）を加え、各回15分前後に分けます。必要に応じて制作する応用2回（A3・A4）を含め、計30回です。

推奨する制作・受講順は **第1〜8回 → A1 → A2 → 第9〜18回 → B1 → B2 → B3 → 第19〜23回** です。並列制作の参照IDを維持するため既存の回番号は変えず、A1・A2も必修に含めます。A3・A4は必要に応じて追加します。

対象は、開発者・インフラエンジニア・データエンジニアなどを含む基盤管理者です。各回のゴールは、資料を見ながら一つの通信や操作について「どこを通り、何が判断され、どこへ状態が残るか」を説明できることです。

15分にはロゴ、今回の問い、確認問題、まとめ、クロージングを含めます。勉強会当日の質疑は別枠です。台本の文字数だけで尺を固定せず、通常速度で生成した音声の長さを測り、内容と間を調整します。

## 新規セッションでの制作

**このファイルとコンテンツ番号を指定すれば、担当セッションは[制作手順](video-sample/PRODUCTION-GUIDE.md)に従い、完成動画まで制作します。** 例は「`docs/study/CONTENT-PLAN.md`の第2回を制作してください」です。題材・到達点・参照範囲は下表から取り、共通仕様は[制作プロファイル](video-sample/production-profile.json)、声と役割は[現行キャスト](video-sample/podcast-cast-gemini.json)を使います。

**本編の採用基準は`teaching-settings-v2`です。** [設定ビューの作り方](video-sample/SETTINGS-VIEW.md)に従い、教材用の設定メニューを説明の入口にし、配置・経路・状態の関係は必要な補助図で示します。質問から回答まで同じ`viewId`・構図・縮尺を保ち、説明する行や項目を赤枠で示します。本編は固定カメラを標準とし、台詞・話者・字幕chunkごとのズームやリセット、繰り返すpulse、模擬ポインターを入れません。Azureのブランドバーや検索欄を部分的に模倣せず、教材専用の設定ビューとして表示します。

開始済みの回も、担当回の`production.md`でこの基準との違いを確認し、担当範囲内で画面を更新します。音声の本文・声・styleが同じなら、[既存音声の再利用手順](video-sample/PRODUCTION-GUIDE.md#進行中の回を新しい画面基準へ合わせる)を使います。別タスクの原稿や共通ファイルは変更しません。オープニングのホログラムと話者へ移るカメラ、共通ロゴ、BGM、声と通常速度は従来の採用仕様を維持します。

複数セッションでは別々の番号を担当し、入力を`video-sample/episodes/02/`、出力を`video-sample/output/lesson-02-gemini/`のように分けます。各回の制作記録へ進捗と完成状態を書き、共有計画・設定・素材は変更しません。この制作一式を含むコミットから作成した別worktreeでも、[初回準備](video-sample/PRODUCTION-GUIDE.md#worktreeの初回準備)を行って同じ手順を使えます。生成済み動画や他のworktreeの`node_modules`は引き継ぎません。

### 完成後のデータ整理

完成動画の書き出し・検証と連続会話プレビューの確認を終えたら、担当回の再制作に必要なデータを残し、不要になった確認用画像と合成済みWAVを整理します。並列制作中も、対象は完成した担当回だけです。他の回の容量を空けるために制作途中のデータを削除しません。

1. **完成状態を確認する。** `production.md`、採用版のMP4・書き出し記録・検証結果を照合し、生成・修正・再書き出し・検証が進行中または再開待ちでないことを確認します。実行ロックがある回、失敗・中断中の回、状態が不明な回は対象外です。ロックがないことだけで完成と判断せず、使用中のロックを削除して整理を続けません。
2. **削除候補と依存関係を確認する。** 対象回の出力フォルダー内に限定してファイルを列挙し、下表の保持対象と、別版・別の回・再制作・比較検証から参照されるデータを除外します。フォルダー全体や全回に一致する指定で一括削除しません。再作成できるか不明なファイルは残します。
3. **記録してから対象ファイルだけを削除する。** 削除一覧・容量と、保持する完成動画・生成原音・再制作データのパスとSHA-256を、対象回の整理記録として保存します。実行直前に対象ファイルが変更されていないことと、制作処理が始まっていないことを再確認します。
4. **保持と空き容量を確認する。** 削除後に保持対象の存在とハッシュ一致、確保できた容量を確認します。`production.md`へ整理日時・削除内容と容量・保持確認・復元手順を追記し、完成状態と整理済みであることを区別して記録します。整理前の検証結果も残します。

| データ | 整理方針 |
| --- | --- |
| 確認用画像 | 出力内の`previews/`やQA用フォルダーにある、再レンダリングまたは完成MP4から再抽出できるPNG・JPEG・WebPだけを削除候補にします。ポスター、元画像、人物・ロゴ素材、SVGは残します。QAのJSONや一覧・検証記録も残し、必要時に画像を再作成します。 |
| 合成済みWAV | 対象回の出力直下にある`lesson-narration.wav`と`lesson-mix.wav`は、保持した原音・設定・素材から再合成でき、別版などが元データとして必要としていない場合に削除できます。音声単独再生が利用できなくなることと復元方法を制作記録に明記します。 |
| 完成動画・再生用データ | 採用済みMP4、共通クロージング、ポスター、再生ページ、字幕、台本、タイムライン、書き出し記録、検証結果を保持します。 |
| 生成原音・生成記録 | `*-raw.wav`と対応する`*-raw.json`、生成設定・結果、`requests-sent/`、`gemini-attempts.json`、`audio-cache.json`など、原音を追加API送信なしで再利用するためのデータと証跡を保持します。 |
| 制作ソース・共通素材 | `episodes/<key>/`の台本・設定・図・専用ツール・制作記録、共通の制作ツール・設定・キャスト・ロゴ・人物画像・BGMを保持します。 |
| 元版・旧版の依存データ | 継続する制作・比較検証が実際に参照する元MP4・元ミックス・JSONなどを保持します。完成動画の検証に履歴JSONだけで足りる場合は、そのJSONと必要な原音を完成版のフォルダーへ統合し、不要な旧動画や重複音声を残すためだけの別出力フォルダーは作りません。`versions/`・`revisions/`・`history/`も依存関係を確認して整理します。 |

合成済みWAVを整理した後は、`--reuse-audio true`やWAVを必要とする完全検証をそのまま実行できません。再編集・再検証する際は、担当回の制作記録にあるビルダーで、保持した原音からWAVを再合成してから使います。通常ビルドでは`--reuse-audio true`を付けず、音声APIへ再送して原音を作り直すことはしません。

## 第1〜8回：ネットワーク構成

元資料は[01 ネットワークの制御](01-network-control.html)の全54節です。後半の[全体図](01-network-control.html#network-overview)、[外向き・管理・DNSの補助図](01-network-control.html#network-egress-management)、[経路ごとの制御](01-network-control.html#network-control-map)を初回から共通の地図として使います。

| 回 | テーマ | 到達点 | 対応する資料 |
| --- | --- | --- | --- |
| 1 | egakumiのネットワーク全体像 | VNet・6サブネットの役割を指し、通常API要求の入口からバックエンドまでを追える。 | [1〜5節：構造と配置](01-network-control.html#purpose)、[44節：全体図](01-network-control.html#network-overview)、45〜46節の代表経路 |
| 2 | DNSとPrivate Endpoint | 公開名・内部名・PEのprivate IPの関係と、送信元ごとに名前解決を確認する理由を説明できる。 | [6〜10節](01-network-control.html#dns-vnet) |
| 3 | NSGによる通信制御 | 優先順位、送受信、状態追跡を使って、代表的な通信の許可・拒否を判断できる。 | [11〜19節](01-network-control.html#nsg-associations) |
| 4 | UDRと往復経路 | 既定経路と具体的な経路、Firewall経由とPE直接経路、往復をそろえる理由を説明できる。 | [20〜25節](01-network-control.html#udr-associations)、[45節](01-network-control.html#network-egress-management) |
| 5 | Firewallは何を判断するか | Network／Applicationルールの違いと評価順、内部中継・基盤通信の許可を説明できる。 | [26〜30節](01-network-control.html#fw-roles)、[34〜35節](01-network-control.html#fw-decisions) |
| 6 | 外向き通信と出口IP | Web依存先の許可、生成されるFQDN、SNAT、出口IPの期待値と実測の違いを説明できる。 | [31〜33節](01-network-control.html#fw-base-applications)、[36〜37節](01-network-control.html#egress-explicit) |
| 7 | つながらないときの調べ方 | DNS・実効経路・NSG・Firewallログを同じ通信について照合し、ログ記録と通知を区別できる。 | [38〜40節](01-network-control.html#observability-logs)、[46節](01-network-control.html#network-control-map) |
| 8 | 基盤管理者の接続と作業実行 | 管理VMへのprivate接続、NSG、SSH認証、Managed Run Commandの分担を説明できる。 | [41〜43節](01-network-control.html#maintenance-network) |

NSG・Firewallの全規則、全CIDR、担当ファイルの一覧は参照資料に残し、動画では原理と代表的な通信に絞ります。全体図を独立した総集編として繰り返すより、各回で同じ図の対象箇所を強調します。

## 第8回の後：ハブ＆スポーク対応（必修A1・A2）

第1〜8回で学ぶ独立VNetの構成を、企業ハブへスポークとして接続する際の変更と維持に結び付けます。元資料は[01の47〜54節：ハブ＆スポークへの対応](01-network-control.html#hub-spoke)です。現行IaCがハブ・ピアリング・Gateway・中央DNS Resolverを自動構築するわけではないことを前提に、企業側とCore側の設計・管理範囲を整理します。

| 回 | テーマ | 到達点 | 対応する資料 |
| --- | --- | --- | --- |
| A1 | ハブ＆スポーク接続と往復経路 | private接続・出口集約・Gateway共有の採用範囲を選び、双方向ピアリング、区画別UDR、復路、Gateway経路伝搬を説明できる。 | [47節：接続範囲](01-network-control.html#hub-spoke)、[48節：ピアリング](01-network-control.html#hub-peering)、[49節：往復経路](01-network-control.html#hub-udr)、[53節：経路伝搬](01-network-control.html#hub-propagation) |
| A2 | ハブ接続後のDNS・通信制御と切替 | 中央DNS・Private DNS・NSG／Firewallの許可・出口IPを整合させ、段階切替、新規接続での検証、切戻し、IaCへの反映と再配備の管理を説明できる。 | [50節：中央DNS](01-network-control.html#hub-dns)、[51節：Private DNS](01-network-control.html#hub-private-dns)、[52節：通信許可・送信元IP](01-network-control.html#hub-policy)、[54節：切替・再配備](01-network-control.html#hub-rollout) |

**A1の主例**は、APIMの公開宛て通信をハブFWへ集約するときの往復です。社内・他スポークへの接続、出口の集約、中央DNS、VPN／ExpressRoute Gatewayの共有は個別に選びます。双方向ピアリングと転送許可、必要な場合のGateway transit、社内側の復路、BGP経路伝搬を図で追います。出口を集約しても、AppGW ↔ APIM・ACAのローカルFW経由とPEへの直接経路を維持する設計、AppGWの既定経路を一律にハブFWへ向けない点を確認します。

**A2の主例**は、中央DNSへの切替から公開宛て通信の検証・再配備までです。VNetクライアントとFW自身のDNS設定、Private Linkの6ゾーンとAPIM・ACAの2ゾーン、転送・参照・管理元を整理します。NSG／FWの許可、実際の送信元・SNAT・出口IPは経路変更と一緒に確認し、現状保存 → 事前準備 → 段階切替 → 新規接続での検証 → 切戻し → IaC・再配備の管理、という流れを扱います。

各回15分前後で原理と代表的な通信に絞り、全規則・全パラメーター・環境ごとの実操作は[07 ハブ＆スポーク対応](../setup/07-hub-spoke.md)などの参照資料へ残します。接続後の構成を図示しますが、稼働環境へ変更を適用・検証した結果として説明しません。

## 第9〜13回：外部攻撃への対策

元資料は[02 外部攻撃への対策](02-external-protection.html)の全22節です。ネットワークで学んだ経路に沿って、各層が何を検査し、どこで処理を止めるかを扱います。

| 回 | テーマ | 到達点 | 対応する資料 |
| --- | --- | --- | --- |
| 9 | WAFの検査と誤検知への対応 | 攻撃パターン・本文上限・適用ポリシーを区別し、正規データの除外範囲を絞る理由を説明できる。 | [1〜3節の復習](02-external-protection.html#purpose)、[4〜8節](02-external-protection.html#policy) |
| 10 | Firewallの許可とIDPSの検知 | 通信規則のAllow／DenyとIDPS Alert／Deny、経路・TLS・方向による検査範囲を区別できる。 | [9〜11節](02-external-protection.html#idps-settings) |
| 11 | APIMの認証と入力検査 | APIMサブスクリプションキー、Operation・モデル・profileの契約、ポリシーの実行順を説明できる。 | [12〜15節](02-external-protection.html#apim-scope) |
| 12 | 利用制限と下流認証 | 回数・トークン量・1要求の生成上限の違いと、入口キーと下流認証の分担を説明できる。 | [16〜18節](02-external-protection.html#apim-rate) |
| 13 | エラーを追い、安全に設定を変える | 要求の到達段階・相関キー・実行位置を照合し、正本の変更、検証、復元へつなげられる。 | [19〜22節](02-external-protection.html#observability) |

第7回は通信の到達性、第13回は要求の検査・処理結果を中心にします。KQL全文、規則ID・サイズ上限・API別例外の全一覧は参照資料に残し、動画では一つの要求を追います。

## 第14〜18回：Portalのサービスと管理

元資料は[03 Portalのサービスと実装](03-subscription-management.html)の全14節です。利用者の操作を入口に、目的、必要なデータ、処理、確認先を結び付けます。

| 回 | テーマ | 到達点 | 対応する資料 |
| --- | --- | --- | --- |
| 14 | 誰が何を管理できるのか | Portal・SQL・APIMの分担、本人認証、所有グループ、会社と費用帰属を区別できる。 | [全体像](03-subscription-management.html#purpose)、[認証](03-subscription-management.html#signin)、[会社](03-subscription-management.html#company) |
| 15 | APIキー発行の裏側 | 入力・所属・承認確認からSQL予約、APIMサブスクリプション作成、SQL確定・監査までを追える。 | [APIキーの払い出し](03-subscription-management.html#key-create) |
| 16 | 仕様を調べ、APIを試す | カタログと試用の経路、台帳ID・APIMサブスクリプションID・キーの違いを説明できる。 | [仕様確認](03-subscription-management.html#catalog)、[API試用](03-subscription-management.html#execution) |
| 17 | 発行後のキー管理 | 表示・停止・再開・移管・棚卸し・削除と、利用制限の設定が利用へ与える効果を説明できる。 | [キー管理](03-subscription-management.html#key-operations)、[利用制限](03-subscription-management.html#limits) |
| 18 | 組織・SQL・APIMの状態を整える | 所有グループの観測と承認、SQL台帳とAPIM実体を区別し、不一致・取得不能時の確認先を選べる。 | [健全性確認](03-subscription-management.html#group-observation)、[同期](03-subscription-management.html#subscription-sync)、[変更時の確認](03-subscription-management.html#maintenance) |

第12回では制限の判定、第17回では設定・保存・反映を説明します。全19表の[全体ER図](03-subscription-management.html#all-data)は参照用とし、各回ではその操作に必要な表だけを見せます。Graph本人接続の詳細シーケンスは第19〜23回で扱います。

## 第18回の後：Portalのデータ設計と実装（B1〜B3）

第14〜18回で理解した機能・操作・状態を、SQLの設計と実際のソースコードへ結び付けます。各回15分前後とし、B1でデータの関係、B2でファイルの責務、B3で関数の呼び出しと状態の変化を追います。第19回以降の番号と題材は変更しません。「この計画のB2を制作してください」のように、既存回と同じ手順で指定できます。

| 回 | テーマ | 到達点 | 対応する資料 |
| --- | --- | --- | --- |
| B1 | ER図で読むPortalのデータ設計 | 全19表を主要データ・設定と履歴・認証と接続に分け、PK/FK、テナント境界、本人と所有グループ、会社と費用帰属、台帳IDとAPIMサブスクリプションIDの役割を説明できる。 | [全体ER図](03-subscription-management.html#all-data)、[主要データ](03-subscription-management.html#data-core)、[設定と履歴](03-subscription-management.html#data-settings)、[認証と接続](03-subscription-management.html#data-auth)、[発行時のデータ](03-subscription-management.html#key-create-data) |
| B2 | Portalのフォルダー構造と処理の分担 | URLの入口から画面・HTTP受付・業務判断・SQL／外部API接続までを担当フォルダーへ対応付け、一つの変更で確認するファイルとテストを探せる。 | [フォルダー構造](03-subscription-management.html#folders)、[キー発行の担当ファイル](03-subscription-management.html#key-create-files)、[変更時の確認](03-subscription-management.html#maintenance) |
| B3 | APIキー発行を関数単位で追う | 発行ボタンからSQL予約、APIM確認・作成、SQL確定・監査までを実際の関数で追い、入力検証・認可・排他・冪等性・結果不明時の再開がどこに実装されるか説明できる。 | [APIキーの払い出し](03-subscription-management.html#key-create)、[発行の担当ファイル](03-subscription-management.html#key-create-files)、[変更時の確認](03-subscription-management.html#maintenance) |

**B1の主例**は、APIキーを中心とする主要5表と、認証・会社・設定・履歴の関係です。全19表を均等に読み上げず、最初に3群の全体地図を示してから関連表を拡大します。SQLの正本は[PortalのSQL](../../apps/portal/sql/)にある`001_initial.sql`〜`010_connection_sessions.sql`です。後続変更を含む現行構造を確認し、007で明示列になったAPIMサブスクリプションID、008で追加された会社との関係も扱います。本人とキー所有グループを直接のFKとして描かず、Graphによる所属確認を通る関係と区別します。キー実値・OAuthトークンの保存先も、SQLの台帳情報と分けて説明します。

**B2の主例**は、APIキー画面から一つの要求が各層を通る経路です。`app/`・`components/`・`lib/`・`sql/`・`scripts/`・`deploy/`・`tests/`の役割をフォルダーツリーで示し、[画面の入口](../../apps/portal/app/[lang]/api-keys/page.tsx)、[HTTPルート](../../apps/portal/app/api/api-keys/route.ts)、[画面側の処理](../../apps/portal/components/api-keys/)、[業務・接続処理](../../apps/portal/lib/api-keys/)を行き来して読みます。`contracts.ts`・`http.ts`・`service.ts`・`repository.ts`・`graph.ts`・`apim.ts`の分担、`service.ts`の`getApiKeyService()`による依存の組み立てを扱います。全ファイル一覧は参照に残し、変更の入口と責務を探す読み方に絞ります。

**B3の主例**は、正常な新規発行と、結果不明になった同じ依頼の再開です。[フォーム](../../apps/portal/components/api-keys/create-key-dialog.tsx)の`submit()`と[入力準備](../../apps/portal/components/api-keys/create-key-form.ts)の`prepareApiKeyIssueAttempt()` → [クライアント](../../apps/portal/components/api-keys/client.ts)の`issueApiKey()` → HTTPルートの`POST()` → [HTTP受付](../../apps/portal/lib/api-keys/http.ts)の`handleApiKeys()` → [サービス](../../apps/portal/lib/api-keys/service.ts)の`ApiKeyService.create()` → [SQL接続](../../apps/portal/lib/api-keys/repository.ts)の`reserve()`・`withLockedKey()` → [APIM接続](../../apps/portal/lib/api-keys/apim.ts)の`get()`・`create()` → `updateState("active")`・`audit("create")`を短い抜粋と呼び出し図で追います。SQL予約が先に確定すること、SQLとAPIMが一つの分散トランザクションにはならないこと、同じ冪等キーとAPIM IDで再開する理由をコードと結び付けます。[関連テスト](../../apps/portal/tests/)は、HTTP・サービス・発行フォーム・SQLリポジトリーの期待動作を確認する箇所に絞ります。

B1はER図、B2はフォルダーツリーとファイル、B3は関数の短い実コードを主画面にします。B2・B3では`layout:"svg"`などの専用図へ読める大きさのコード抜粋と呼び出し図を併記し、説明中の行・関数・関係を赤枠で示します。共通の外枠・登場人物・音声・BGM・冒頭と終了・固定字幕は維持し、一つの問いと回答では構図を保持します。ソース全文・全関数の逐行解説へ広げず、参照したコミット・パス・関数名を担当回の`production.md`へ記録します。稼働SQLのデータや実環境を操作した説明として扱いません。

第14〜18回は機能と操作の理解、B1〜B3は設計・実装の読み方、応用A3はSQL準備・配備Job・実行権限を主題として分けます。B1〜B3の制作のためにアプリやデータベースを変更したり、実SQL試験・配備を実行したりする必要はありません。

## 第19〜23回：Credential Brokerと本人接続

元資料は[04 Credential BrokerとCM](04-credential-management.html)の全11節です。Portalログイン、Gatewayの利用許可、外部APIの本人接続を区別して進めます。

| 回 | テーマ | 到達点 | 対応する資料 |
| --- | --- | --- | --- |
| 19 | APIキーだけでは本人を区別できない理由 | APIキー・接続証明・OAuthトークン、CM・Broker・APIM・呼出アプリの分担を説明できる。 | [目的と役割](04-credential-management.html#purpose)、[実行構成](04-credential-management.html#runtime)、[CM設定の概念](04-credential-management.html#cm-settings) |
| 20 | 初めての本人接続 | 認証案内、Entra本人確認とCM同意、完了確認の3段階を追える。 | [本人接続の開始](04-credential-management.html#connect) |
| 21 | 接続済みのAPI要求を追う | 接続証明の照合、CMトークン取得、Graph本人確認、業務利用の確定とAPI実行を追える。 | [API呼び出し](04-credential-management.html#api-use)、[Portal側の本人接続](03-subscription-management.html#graph-connection) |
| 22 | 接続証明を安全に更新する | 更新候補の先行保存、commit、世代更新、失敗時の回収を説明できる。 | [接続証明の更新](04-credential-management.html#rotation)、[Portal側の保存・再開](03-subscription-management.html#graph-connection) |
| 23 | 利用を止め、残った処理を回収する | logout／logout-all／disconnectの範囲と、削除未完了・期限切れ・CM不一致の回収を区別できる。 | [終了と解除](04-credential-management.html#disconnect)、[照合と整理](04-credential-management.html#reconciliation) |

接続証明の更新は単独の回にします。CM設定の容量・割当・専用APIの詳細と、全7表の[全体ER図](04-credential-management.html#all-data)は参照資料または応用編で扱います。

## 必要に応じて追加する応用2回

| 回 | テーマ | 対応する資料 |
| --- | --- | --- |
| A3 | PortalのSQL準備と配備Job | 03 [Jobと配備](03-subscription-management.html#jobs)、[変更時の確認](03-subscription-management.html#maintenance)、[フォルダー構造](03-subscription-management.html#folders) |
| A4 | BrokerのCM設定・権限・Jobと調査 | 04 [CM設定](04-credential-management.html#cm-settings)、[配備](04-credential-management.html#deployment)、[確認箇所](04-credential-management.html#maintenance) |

A1・A2のハブ＆スポーク対応は、上記の必修ブロックで制作します。A3・A4は配備・設定・実装の詳細まで追う必要がある場合に追加します。

## 第1回の構成と制作範囲

テーマは**「egakumiのネットワーク全体像」**です。MAKOの「egakumiに送った要求って、どこを通るんですか？」から始めます。

| 時間の目安 | 内容 | 主に使う設定ビュー・補助図 |
| --- | --- | --- |
| 0:00〜0:40 | 共通ロゴ、今回の問いを使った3人の掛け合い | egakumiロゴ、3人のホログラム |
| 0:40〜3:00 | MANABUが区画を分ける目的を説明し、IKUMIが配置と制御を示す | 配置・NSG・UDR・FWを同じ設定ビューで確認 |
| 3:00〜7:00 | VNet・4アドレス空間・6サブネットの配置と包含関係 | 包含構造図、アドレス空間表、各リソースの設定、VM・ACAの左右比較 |
| 7:00〜11:30 | 通常API要求を入口からAPIM・FoundryのPEまで追う | 設定項目と経路図を併記し、往路・復路の説明中は同じ構図を保持 |
| 11:30〜14:00 | 「区画を分ければ遮断される？」「全部Firewallを通る？」を確認 | 接続先別の経路と設定。質問と回答で同じ画面を保持 |
| 14:00〜14:52 | 配置・主経路・直接経路を振り返り、次回のDNSへつなぐ | 設定表と全体図。赤枠で説明する対象を示す |
| 14:52〜15:00 | 共通ロゴのクロージング | ロゴとブランド曲の終盤8秒 |

実際の字幕・チャプター時刻は、生成音声から確定します。IP値の暗記や全ポート一覧の説明へ広げず、配置・経路・通信制御の違いを押さえます。通常HTTPS要求を主例とし、PortalやgRPCの経路は違いがあることを示す範囲にとどめます。

上表は約15分へ整理するための時間配分です。採用済み第1回は15分58秒で、アドレス空間の質問から回答までを連続した52.25秒で確認できます。新しい回も、40〜70秒程度の連続した会話を確認用プレビューにします。数秒ずつの別場面をつないだ抜粋だけでは、画面保持や会話と強調箇所の対応を確認したことにしません。[第1回の完成記録](video-sample/LESSON-01.md)で採用内容・原音・検証結果を確認し、画面方針は設定メニュー版を優先します。

## 動画の共通仕様

制作の詳細は[制作手順](video-sample/PRODUCTION-GUIDE.md)と[Podcast形式](video-sample/PODCAST-FORMAT.md)、配役は[現行キャスト設定](video-sample/podcast-cast-gemini.json)で管理します。

| 人物 | 役割として表示する肩書き | 勉強会での分担 |
| --- | --- | --- |
| MAKO | Moderator | 進行・質問 |
| MANABU | Product Manager | 目的・提供価値 |
| IKUMI | Solution Architect | 技術設計・実装 |

- 共通ロゴは冒頭4秒。その後の30〜40秒程度は、その回の話題に合わせた掛け合いにします。
- 画面に表示する役割は上表の英語の肩書きに統一し、日本語の勉強会内の分担と分けて管理します。表示名は**MAKO / MANABU / IKUMI**、音声の読みはマコ／マナブ／イクミです。
- 声は**MAKO=Aoede、MANABU=Charon、IKUMI=Orus**、モデルは**Gemini 3.8 Flash TTS**。全員通常速度（1.0倍）で、Google Cloudの既存プロジェクトから直接生成します。話し方・反応・息継ぎ・笑いは場面に合わせた自然言語のstyleで指定し、本文や字幕へ演技指示を出しません。生成後の速度・ピッチ変換は行いません。
- オープニングは暗い未来的な2.5Dホログラム空間。最初に3人の顔・名前・役割を示し、その後は話者が中央に来るよう滑らかにカメラを移動します。話者交代ごとの急な位置入れ替えを避け、空間内での位置関係を保ちます。
- 本編は教材用の設定メニューを主役とし、必要な配置図・経路図を併記します。未来的な共通外枠、話者表示、固定字幕領域は維持します。同じ問いと回答では画面を保ち、赤枠だけを説明対象に合わせます。図や枠を指す台詞には、その図や枠を見せます。
- 本編の自動ズーム・パン・画面リセットは使いません。将来、ユーザーが具体的な動きを指定した場合は、その話題の連続した会話の中で動きと保持時間を設計し、プレビューで内容との対応を確認します。オープニングのホログラムカメラには、この本編の固定方針を適用しません。
- 本編BGMは既存Podcastの`main-bgm`を使い、継ぎ目をクロスフェードして繰り返します。会話中は音楽を下げ、声を明瞭に保ちます。
- 最後は共通のegakumiロゴ映像8秒で終了します。BGMはブランド曲`brand-theme`の**63.8〜71.8秒**を使います。各回のまとめ・次回案内は、その直前の掛け合いで行います。

## 資料と実環境の区別

本計画が参照する構成・設定は、2026-11-01時点の教材とリポジトリ定義・公開設定例に基づきます。稼働中Azureから取得・検証した値として説明しません。ハブ接続は接続時の設計指針として扱います。

資料の「基盤管理者」表記と、APIMサブスクリプションキー・APIMサブスクリプションIDの用語を引き継ぎます。設定値や実装が変わった場合は、元資料・台本・図の対応をそろえて更新します。
