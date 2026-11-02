# 更新・撤去・途中失敗時の扱い

[README](../README.md) · [構築手順](setup/06-deploy.md) · [開発ルール](../DEVELOPMENT.md)

本書は、現行Egakumi Coreの共通基盤・Gateway・Portalを更新し、障害から再開し、撤去するための手順です。処理の入口は [azure.yaml](../azure.yaml)、構築・撤去のhookは [lifecycle.py](../scripts/deployment/lifecycle.py)、撤去対象の照合は [cleanup.py](../scripts/deployment/helpers/cleanup.py)で管理します。現在進行中の試験状態は [動作テスト仕様書](../INTEGRATION_TESTS.md)と[再開記録](testing/results/20261031-resume.md)を参照し、この一般手順だけを理由に再構築や撤去を開始しません。

## 対象環境と記録を確認する

構築に使用したクローンで、同じparams・参照config・azd環境を使います。新しいターミナルでは次を設定し直します。

```sh
cd '<構築に使用したクローン先ディレクトリ>'
export DEPLOY_PARAMS_FILE='<構築に使用したparamsのパス>'
export DEPLOY_ENVIRONMENT='<構築に使用した環境名>'
export WORKFORCE_CONFIG="$DEPLOY_PARAMS_FILE"
```

Azure CLI・azdの認証先、paramsのTenant／Subscription・環境名、対象5RGを照合します。秘密値はコマンド引数・通常ログへ入力しません。

| 記録 | 用途 |
| --- | --- |
| paramsと参照config | 構築・更新・撤去で使う入力の正本 |
| `.azure/` | azd環境と配備先の設定 |
| `.state/latest.json` | 共通基盤の実配備ID・outputs・所有資源・監査保護・ACA管理RGの記録 |
| `.state/gateway-deployment.json`・`portal-deployment.json` | 各アプリの配備結果・イメージdigest・確認範囲 |
| `.state/credential-broker-build.json`・`portal-build.json` | ビルドしたソースとイメージの対応、再利用・一時資源の回収結果 |
| `.state/acceptance/<run-id>/` | 試行ごとの実測・証跡。配備完了と業務動作の合格を分けて記録 |

Gitからクローンし直しても`.azure/`・`.state/`・保護入力は復元されません。別端末への引継ぎや障害に備えて安全に保管します。既存の記録を削除して再試行したり、名前からResource IDを推測してstateを作ったりしません。

## 同じ環境を更新する

| 変更対象 | 実行する入口 |
| --- | --- |
| 共通基盤からGateway・Portalまで | `azd up --environment "$DEPLOY_ENVIRONMENT"`。`publishApis:true`が必要 |
| 共通基盤のみ | `azd provision --environment "$DEPLOY_ENVIRONMENT"`。アプリ本体の配備は続けて実行しない |
| Gatewayのみ | `python3 scripts/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE"`で計画を確認し、`--execute`で反映 |
| Portalのみ | `python3 scripts/deploy_portal.py --config "$DEPLOY_PARAMS_FILE"`で計画を確認し、`--execute`で反映 |
| 両アプリを順に更新 | `python3 scripts/deploy_applications.py --config "$DEPLOY_PARAMS_FILE"`で計画を確認し、`--execute`でGateway→Portalを実行 |

アプリの個別更新では、基盤の`azd up`を内部で再実行しません。共通基盤・Gateway・Portalを並行して更新せず、各処理の終了を確認します。個別コマンドの通常の計画生成はローカル処理です。`deploy_gateway.py`・`deploy_portal.py`の`--deployment-name`を指定すると、計画時にも指定した成功済みCore配備のoutputsをAzureから読み取ります。`deploy_applications.py`はこのオプションに対応しません。これはアプリ配備用の選択肢であり、撤去用stateを復元するコマンドではありません。

Gatewayは、採用時のBrokerイメージ作成・DB／CM配備・SQL初期化・起動確認の後に、API・Product・ポリシー・WAFを反映します。Portalはイメージ作成・DB／権限・SQL初期化を経て同じContainer Appを更新し、公開`/api/health`を確認します。イメージは管理VMで作成し、Core内の非公開ACRへ登録します。確認済みのソースとdigestが一致する場合は既存のビルド結果を再利用します。

更新前に、対象revision、params・非秘密の設定hash、現在のアプリrevision・digest、必要なDBバックアップ、API／ポリシーの変更点を記録します。SQL初期化・migration失敗時はアプリや後続APIの公開へ進みません。復旧は原因を直して同じ入口を再実行し、自動ロールバックが完了したものとして扱いません。

基盤の更新は、所有確認済みのNSG規則・UDR・APIMホスト／証明書参照、追加済みのAPIM外向け許可先を読み取り、構築入力へ引き継ぎます。企業ネットワーク変更時は [ハブ接続手順](setup/07-hub-spoke.md)も確認します。変更後の公開API実行・Portalログイン・本人接続・ログ到達は [動作テスト](../INTEGRATION_TESTS.md)で別途確認します。Gatewayの`status: deployed`は業務APIの`runtimeVerified`を合格へ変更するものではありません。

### API・ポリシー・購読を変更する

資材で管理するAPIは [登録定義](../apps/gateway/apim/apis/)を変更し、Gatewayを再配備します。`apiDefinitions: []`は同ディレクトリの標準定義を解決し、明示した配列はその定義を対象にします。解決したAPIや環境固有URLを元のparamsへ書き戻しません。

Azure Portal等で個別に追加するAPIは、API／operation・Product所属・購読必須設定・購読キーヘッダーを揃え、`<base />`で共通認可を継承させます。下流認証・許可宛先・公開パス・監査も設定します。資材の管理対象と同じIDを手動編集すると競合するため、更新元を明確にします。登録対象外の独自APIを、定義から省いただけで自動削除する処理ではありません。

共通ポリシーは子資源の成功後にETag付きで統合・読戻し確認します。適用失敗や競合時は最新状態と差分を確認し、同じ対象で再試行します。全体ポリシーを削除して作り直したり、認可を解除して復旧したりしません。手動統合用の確認XMLは [render_apim_policy.py](../scripts/render_apim_policy.py)で未作成のファイルへオフライン出力できます。

Gatewayの公開処理は、未使用と判定できるAPIM初期サンプル（Echo API、Starter／Unlimitedとその購読）を整理します。業務へ転用したもの、範囲を確認できない購読、masterは保持し、`defaultSampleCleanup`へ結果を記録します。WebSocket採用時は既存の診断宛先・他カテゴリを保持して接続ログを有効にし、`webSocketDiagnostics`へ結果を記録します。

標準APIの展開処理は試験用キーを自動発行しません。Portalは設定された`portalApiKeys.verificationSubscriptions`に従う検証用購読を初期化します。空配列の場合も、基盤管理グループの承認製品から`platform-<productId>`を生成するため、検証購読なしの指定にはなりません。通常のAPIキーは利用者がPortalから所有グループを選んで発行し、発行先は`released-apis`です。明示APIの`keySubscriptions`はその設定に従います。認可の分担は [Gatewayの購読キー認証](reference/gateway-key-auth-plan.md)を参照してください。

## アプリ配備の途中で失敗した場合

`azd up`は共通基盤の`postprovision`成功後にGateway→Portalを実行します。Gatewayが失敗したらPortalへ進まず、Portalが失敗したらup全体が失敗します。基盤の作成結果だけを一式の完了としません。

1. 終了した工程・終了コード・配備記録と、現在のAzure上の状態を確認する。
2. 同じparamsと`.state/latest.json`を保持して原因を修正する。SQLやイメージ構築の失敗を飛ばさない。
3. Gatewayで停止した場合はGatewayを再試行し、その成功後にPortalを実行する。Portalで停止した場合はPortalの専用コマンドから再開できる。
4. アプリの稼働・公開ヘルス・イメージdigestと、変更対象の業務動作を確認して記録する。

両アプリを続けて更新する場合は次を使います。既存の成功結果だけを理由に後続工程へ進まず、各子コマンドの今回の終了コードで判断します。

```sh
python3 scripts/deploy_applications.py --config "$DEPLOY_PARAMS_FILE" --execute
```

無人実行では保護入力に加え、Portalの`portalApiKeys.groups`・`platformGroupId`・`groupNamePattern`を事前に設定します。初回の対話入力は同じparamsへ保存されます。

## 構築後にClaudeをGUIで追加する

現行の設定検証はClaudeを初期の`azd`モデル配備対象へ含める入力を拒否します。追加する場合は、構築後にAzure Portalからモデルを配備し、APIMのAPI・操作・下流認証・Product関連付けを手動で設定します。

対象リージョン・利用枠・購入条件・実配備名・接続先を確認し、公開Gatewayから正常応答／SSE、未認証・許可外操作の拒否、監査を確認します。FoundryのPE・DNS・Firewall経路も合わせます。共通購読認可と入口検証は維持します。

手動追加したAPI／モデルも、所有APIM・Foundry・RGを削除する`azd down`では撤去対象です。個別登録コマンドが更新しないことと、環境全体の撤去範囲を混同しません。

## DNS資格情報を更新する

Azure DNSは事前作成したユーザー割り当てマネージドIDを使います。変更する場合は新しいIDへDNS権限を付与し、paramsの`certificate.azureDnsManagedIdentityResourceId`と、必要なら`azureDnsSubscriptionId`を更新して、同じ環境へ反映します。管理VMへの関連付け・証明書更新を確認してから旧権限を回収します。外部で作成したDNS用IDはCoreの撤去対象に含めません。

Cloudflare／Route53等の資格情報ファイル方式では、同じ事業者の正常な保護ファイルを再利用します。`azd up`だけで新しい値の入力を必ず要求する仕様ではありません。

1. 必要なゾーン・権限範囲の新資格情報を発行し、旧資格情報を有効に保つ。
2. paramsの`certificate.pluginArgumentsFile`を、`secrets/`配下の未作成の新しい保護ファイルへの相対パスへ変更する。JSONを手入力で作成しない。
3. 同じparamsで`azd up`を実行し、同じDNS事業者を選んで新資格情報を非表示入力する。基盤・Gateway・Portalの通常更新も実行される。
4. Key Vaultの新version、DNS操作・証明書更新と公開証明書の反映を確認してから旧資格情報を失効させる。

事業者・証明書方式を切り替える場合は [DNS事前準備](setup/03-public-dns.md)と [configの証明書設定](setup/05-config.md)を確認します。保持した資格情報を先に削除して更新を開始しません。

## ログ・監視・本人接続を確認する

Coreの診断ログ・LAW export・監査Storageと、企業共通監査は所有者と信頼境界を分けて扱います。query非保持の既存環境更新は [ログ保存手順](testing/log-privacy.md)のprepare→export→routeの順で実施し、変換後ログのStorage到達を確認してから直接保存経路を切り替えます。設定のreadback成功だけで実到達を合格にしません。

APIキー管理・購読同期はPortalの管理画面を使います。[購読別の制限・観測](testing/subscription-limits.md)ではSQLの制限設定、APIMへの反映、欠測を分けて確認します。

## 本人接続のSQL台帳

Brokerの本人対応・セッションはAzure SQL、下流OAuth tokenはCMが管理します。Portalのログイン・キー台帳と、本人接続状態は別用途です。DB障害、照合ジョブ、SQL復元後の失効・再開は [SQL台帳とGateway配備](setup/credential-sql.md)と [接続セッションの契約](reference/credential-pool-design.md)に従います。ローカルSQLの試験状態をAzureの業務台帳へ同期しません。

## 撤去する

対象環境の利用を終了するときに実行します。実行中の音声セッション等を停止し、構築時のparams・config・`.azure/`・`.state/latest.json`、監査保持とDB／必要データの保管を確認します。

`azd down`では、azd本体のRG削除確認より前に`predown` hookがモデル・APIM・アプリ・管理VMの削除を開始します。対象Tenant／Subscription・環境名・所有5RGは、コマンド実行前に照合します。

```sh
export WORKFORCE_CONFIG="$DEPLOY_PARAMS_FILE"
azd down --environment "$DEPLOY_ENVIRONMENT"
```

Coreの`postdown`は`--purge`の有無にかかわらず、対象のKey Vault・Foundry・APIMの論理削除記録を照合して、保護設定が許すものを完全削除します。Key Vaultのpurge protectionや監査WORMを解除する指定ではありません。

| 段階 | 処理・完了確認 |
| --- | --- |
| 変更前 | stateと現物のID・種類・命名・region・所有タグ・接続先、全監査保持を照合し、ACA管理RGの実IDを保存する |
| `predown` | モデル→CMの接続権限・接続・provider→APIM→Broker・全ジョブ→Portal CA→共有CAE→ACAサブネット解放→管理VM・残存する所有NICの順で回収する |
| azd本体 | 所有5RGを削除する。専用DB・ACR／イメージ・Vault秘密・専用IDも所有RGと共に回収する |
| `postdown` | 5RGと記録済みACA管理RGの404、対象3サービスの削除記録・保護による保持、API Centerの削除履歴を記録する |

Gateway／Portalの配備成功記録がなくても、固定IDと実資源の所有・接続先を照合します。全アプリ・ジョブの不存在を確認してからCAEを削除し、サブネットの解放を待ちます。ACAの`infrastructureResourceGroup`はAzure側の削除を待ち、名前で`ME-*`を探索・直接削除しません。保存した旧・新の管理RGも確認対象です。

Locked WORMがある場合は変更前に停止します。Unlockedでも構成の監査保持が有効なら解除しません。Key Vaultのpurge protectionは期限まで保持します。環境名だけで削除保護を切り替えません。外部DNSゾーン・DNS用ID・Entra登録・同意・企業ハブは撤去対象に広げません。

結果は`.state/cleanup-predown.json`と`.state/cleanup-postdown.json`に保存されます。成功・未完了・保護による保持・確認不能を区別し、保持・残存があるものを完全消去済みとは扱いません。

### 削除待ち・再実行時に確認すること

- APIMやアプリが削除中なら要求を重ねず、同じIDの消失を待つ。待機期限を超えたら同じ入力・stateを保持して原因と現在状態を確認する。
- CMの個別削除と親APIMの削除を分ける。一覧消失を用いる限定条件でもprovider自身のGET404とは記録せず、`deletionVerified`・`listAbsenceVerified`・`parentDeletionVerified`を区別する。
- ACA管理RGだけが残る場合はAzureの削除完了を確認し、同じparamsで`python3 scripts/deployment/lifecycle.py postdown --config "$DEPLOY_PARAMS_FILE"`を再実行する。
- Log Analyticsのforce完全削除やAPI Centerのpurgeは通常処理に含めない。`apiCenterRetention`が`unverified`の場合も確認不能の理由を保持する。
- モデル消失と利用枠・費用の反映は別に確認する。利用枠は次の`capacity`で再確認し、他環境の割当まで0にしない。

### 一部のRGを削除した後、再実行がResourceGroupNotFoundで止まる場合

1. `.state/cleanup-predown.json`の対象が一致し、`result.status`が`ready-for-azd-down`であることを確認する。モデル・APIM・アプリ／ジョブ・CAE・サブネット・管理VMの先行回収と保持確認が済んだ記録を使う。
2. 同じSubscriptionで5RGの現存状態を読み取り、残るRGの所有と資源を照合する。削除中なら完了を待つ。
3. 残る所有RGだけを1件ずつ削除し、正常終了を確認する。削除済みRGを再作成せず、stateも書き換えない。

   ```sh
   az group delete --subscription '<SUBSCRIPTION_ID>' --name '<REMAINING_RESOURCE_GROUP>'
   ```

4. 5RGの不存在を確認してから、同じparamsで`postdown`を実行する。

   ```sh
   python3 scripts/deployment/lifecycle.py postdown --config "$DEPLOY_PARAMS_FILE"
   ```

5. 終了コードと新しい撤去記録を確認し、ACA管理RG・削除履歴・保護による保持・費用を記録する。

### 初回構築の途中で失敗し、撤去用stateがない場合

監視配備の成功前に停止すると、資源の一部が存在しても`.state/latest.json`がない場合があります。通常の撤去hookは正確なstateなしでは続行しません。

1. 同じSubscriptionのAzure Portalで対象の配備・時刻・環境名・製品名・所有タグ・操作履歴を確認し、実際に作成された資源IDを特定する。処理中の操作を重複実行しない。
2. 成功済み配備の完全なoutputsやバックアップがある場合は、管理担当者が実資源・保護設定と照合して正確な記録を回復する。アプリ用`--deployment-name`を撤去用stateの代用にしない。
3. 成功済みoutputsがない場合はstateを捏造せず、管理担当者が確認できた資源だけをAzure Portalで限定回収する。存在するモデル・APIM・アプリ・ネットワークの依存順と監査保持を守る。
4. 実IDごとの残存・保持・費用を記録する。削除保護・WORM・purge protectionを回避しない。

生成された秘密入力だけを回収する場合は`python3 scripts/deployment/lifecycle.py clear-inputs`を使います。対象は`infra/main.parameters.json`であり、元の保護入力・SSH鍵・配備記録を削除せず、Azure資源も撤去しません。
