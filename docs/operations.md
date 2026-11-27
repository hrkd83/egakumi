# 更新・撤去・途中失敗時の扱い

[README](../README.md) · [構築手順](setup/08-provision-core.md) · [開発ルール](../DEVELOPMENT.md)

本書は、現行Egakumi Coreの個別配備・途中再開・撤去の共通手順です。業務ごとの操作は[通常運用・改修の手順](operations/README.md)、受入条件と実施順は[運用テスト](testing/operations-tests.md)から確認します。処理の入口は [azure.yaml](../azure.yaml)、構築・撤去のhookは [lifecycle.py](../scripts/deployment/lifecycle.py)、撤去対象の照合は [cleanup.py](../scripts/deployment/helpers/cleanup.py)で管理します。実環境の試験状態・再開記録は環境別のGit管理外領域`.local/<environment>/`で管理します。この一般手順だけを理由に再構築や撤去を開始しません。

## 対象環境と記録を確認する

構築に使用したクローンで、同じparams・参照config・azd環境を使います。新しいターミナルでは次を設定し直します。

```sh
cd '<構築に使用したクローン先ディレクトリ>'
export DEPLOY_ENVIRONMENT='<構築に使用した環境名>'
export DEPLOY_LOCAL_DIRECTORY="$(pwd)/.local/${DEPLOY_ENVIRONMENT}"
export DEPLOY_PARAMS_FILE="$DEPLOY_LOCAL_DIRECTORY/params.local.json"
export WORKFORCE_CONFIG="$DEPLOY_PARAMS_FILE"
```

Azure CLI・azdの認証先、`.local/`内の環境名、paramsのTenant／Subscription・環境名、stateの所有記録と対象5RGを照合します。不一致のまま続行せず、別環境のstateや秘密を流用しません。同じcheckoutで構築・更新・撤去を同時実行しません。秘密値はコマンド引数・通常ログへ入力しません。

`services.apimInstanceId`は構築時の値を保持します。APIM名は`apim-<environment>-<product>-<apimInstanceId>`で、更新・途中再開・撤去では同じ完全な資源IDを照合します。

| 記録 | 用途 |
| --- | --- |
| paramsと参照config | 構築・更新・撤去で使う入力の正本 |
| `.azure/` | azd専用の環境と配備先の設定 |
| `.local/<environment>/params.local.json`・`secrets/` | 今回の環境に対応する導入先設定と秘密・SSH鍵 |
| `.local/<environment>/state/latest.json` | 共通基盤の実配備ID・outputs・所有資源・監査保護・ACA管理RGの記録 |
| `.local/<environment>/state/gateway-deployment.json`・`portal-deployment.json` | 各アプリの配備結果・イメージdigest・確認範囲 |
| `.local/<environment>/state/credential-broker-build.json`・`portal-build.json` | ビルドしたソースとイメージの対応、再利用・一時資源の回収結果 |
| `.local/<environment>/evidence/<run-id>/` | 試行ごとの実測・証跡。配備完了と業務動作の合格を分けて記録 |

Gitからクローンし直しても`.azure/`・`.local/`は復元されません。構築時の環境別領域とazd環境を、別端末への引継ぎや障害に備えて保護保管します。公開用の資材コピーや配布物へ混ぜません。既存の記録を削除して再試行したり、名前からResource IDを推測してstateを作ったりしません。

## 新しい公開入力へ移す前に確認する

公開入力71主項目の判断と新しい登録／定義形は[paramsの判断記録](setup/07-params.md#公開入力71主項目の判断記録)にまとめています。今回の一括改修は資材実装の段階です。Azure反映、全環境再構築E2Eと実受入は未実施であり、最終集約前のローカル試験を一式成功と扱いません。

既存の保護paramsとstateを保持して旧値を比較します。固定化した旧入力は空配列・空object・false・nullでも拒否し、共通値はconfigの正しい位置へ移します。標準Graphはparamsの`credentialProviders.graphDelegated`へClient IDと秘密参照、configの同slotへ表示名・scope・poolを分離します。追加providerは双方の`additional[]`を論理名で照合します。既存の論理／物理provider名、アプリID、秘密参照、scope文字列、pool.count=12とcapacity=8000を配置変更だけで変更しません。

非標準・非空の権限、既存WAF、IP例外、静的caller上書き、API置換、別tenant、Portal追加グループ／製品subset／独自購読IDは無断変換しません。意図した資源・利用範囲・所有者・復旧と対象限定の移行を確定してから進みます。今回のコード変更だけで認証方式やClient IDを新しいアプリへ切り替えません。

`services.apimInstanceId`は初回prepareが公開マーカーを6文字IDへ置換してparamsへ保存し、通常更新では保持します。未解決マーカー・欠落・不正な既存値を更新時に再生成しません。SSH鍵参照と秘密ファイルも同環境の保護領域で保持し、公開例の上書きコピーや別環境入力の自動流用を行いません。

## 同じ環境を更新する

| 変更対象 | 実行する入口 |
| --- | --- |
| 共通基盤の設定 | 変更対象と復旧方法を個別に確定し、対象限定の操作で更新する。[共通確認](operations/common.md)で資材と実設定を整合させる |
| Foundryのモデル | [モデル運用](operations/foundry.md)に従い、Foundryの画面で配備・容量・版を変更する |
| Gatewayのみ | `python3 scripts/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE"`で計画を確認し、`--execute`で反映 |
| Portalのみ | `python3 scripts/deploy_portal.py --config "$DEPLOY_PARAMS_FILE"`で計画を確認し、`--execute`で反映 |
| 両アプリを順に更新 | `python3 scripts/deploy_applications.py --config "$DEPLOY_PARAMS_FILE"`で計画を確認し、`--execute`でGateway→Portalを実行 |

初回構築ではAPI・Tools・CredentialのEntraアプリを登録してから、`azd up`でCoreを構築します。続けてAPIのOBO証明書登録とPortalアプリの作成・証明書登録を行い、GatewayとPortalを個別配備します。構築後の運用は上表の入口を使います。初期構築途中での再試行は[構築手順](setup/08-provision-core.md)で扱います。アプリの個別更新は、基盤の再構築と独立して実行できます。共通基盤・Gateway・Portalを並行して更新せず、各処理の終了を確認します。個別コマンドの通常の計画生成はローカル処理です。`deploy_gateway.py`・`deploy_portal.py`の`--deployment-name`を指定すると、計画時にも指定した成功済みCore配備のoutputsをAzureから読み取ります。`deploy_applications.py`はこのオプションに対応しません。これはアプリ配備用の選択肢であり、撤去用stateを復元するコマンドではありません。

Gatewayは、採用時のBrokerイメージ作成・DB／CM配備・SQL初期化・起動確認の後に、API・Product・ポリシー・WAFを反映します。Portalはイメージ作成・DB／権限・SQL初期化を経て同じContainer Appを更新し、公開`/api/health`を確認します。イメージは管理VMで作成し、Core内の非公開ACRへ登録します。確認済みのソースとdigestが一致する場合は既存のビルド結果を再利用します。

更新前に、対象revision、params・非秘密の設定hash、現在のアプリrevision・digest、必要なDBバックアップ、API／ポリシーの変更点を記録します。SQL初期化・migration失敗時はアプリや後続APIの公開へ進みません。復旧は原因を直して同じ入口を再実行し、自動ロールバックが完了したものとして扱いません。

基盤の更新は、所有確認済みのNSG規則・UDR・APIMホスト／証明書参照、追加済みのAPIM外向け許可先を読み取り、構築入力へ引き継ぎます。企業ネットワーク変更時は [ハブ接続手順](setup/10-hub-spoke.md)も確認します。変更後の公開API実行・Portalログイン・本人接続・ログ到達は [動作テスト](testing/integration-tests.md)で別途確認します。Gatewayの`status: deployed`は業務APIの`runtimeVerified`を合格へ変更するものではありません。

[5 NSGの構成と優先度](operations/network-security-groups.md)は共通採番JSONとBicepで管理します。新規構築で同じ定義を使用し、構築後は上表に従って対象に限定して更新します。

正式な設計参照は[通信要件](reference/network-communication-requirements.md)、[NSG全規則・判断記録](reference/network-security-design.md)、[Firewall全規則・判断記録](reference/firewall-policy-design.md)です。実経路とルールの対応、標準DNS/NTP、公開HTTPのリダイレクトとバックエンドHTTPS、初期不作成の境界を確認し、結果は[検証と実証結果の管理](reference/network-security-validation.md)に従って記録します。

### API・ポリシー・購読を変更する

資材で管理するAPIは [登録定義](../apps/apim/apis/)を変更し、Gatewayを再配備します。標準定義を常用で解決し、公開paramsによるAPI一覧の置換と`publishApis`切替は廃止しました。旧入力は空やfalseでも拒否します。解決したAPIや環境固有URLを元のparamsへ書き戻しません。

APIだけの公開処理はFoundryモデルを配備せず、GUI等で変更した現在のcapacity／TPM割当を保持します。モデルの配備ID・名前・版・形式・SKU名・`Succeeded`状態、APIMのRBACとFoundryのローカル認証無効化を照合し、capacityと初期設定値の一致は要求しません。初期基盤構築の`infra/modules/services.bicep`は`services.modelDeployments`のcapacityをモデル配備へ反映するため、構築後の容量維持を目的に基盤構築を再実行せず、Gatewayの公開処理と区別します。

Azure Portal等で個別に追加するAPIは、API／operation・Product所属・購読必須設定・購読キーヘッダーを揃え、`<base />`で共通認可を継承させます。下流認証・許可宛先・公開パス・監査も設定します。資材の管理対象と同じIDを手動編集すると競合するため、更新元を明確にします。登録対象外の独自APIを、定義から省いただけで自動削除する処理ではありません。

共通ポリシーは子資源の成功後にETag付きで統合・読戻し確認します。適用失敗や競合時は最新状態と差分を確認し、同じ対象で再試行します。全体ポリシーを削除して作り直したり、認可を解除して復旧したりしません。手動統合用の確認XMLは [render_apim_policy.py](../scripts/render_apim_policy.py)で未作成のファイルへオフライン出力できます。

Gatewayの公開処理は、未使用と判定できるAPIM初期サンプル（Echo API、Starter／Unlimitedとその購読）を整理します。業務へ転用したもの、範囲を確認できない購読、masterは保持し、`defaultSampleCleanup`へ結果を記録します。WebSocket採用時は既存の診断宛先・他カテゴリを保持して接続ログを有効にし、`webSocketDiagnostics`へ結果を記録します。

標準APIの展開処理は試験用キーを自動発行しません。Portalは`platformGroupId`の管理グループへ標準API正本の全製品を承認し、configの`portalApiKeys.verificationMaxSubscriptions`（初期5）を適用します。製品ごとの`platform-<productId>`検証購読と内部groupsを生成し、同一ID・同一製品の既存キーを保持します。公開`groups`・`verificationSubscriptions`入力やparams書き戻しは行いません。旧任意購読ID・製品subset等は個別移行を先に確認します。通常のAPIキーは利用者がPortalから所有グループを選んで発行し、発行先は`released-apis`です。認可の分担は [Gatewayの購読キー認証](reference/gateway-key-auth-plan.md)を参照してください。

## アプリ配備の途中で失敗した場合

初回は`azd up`でCoreを完了し、Entraに公開証明書を登録してからGatewayとPortalを順に個別配備します。両アプリを更新する場合、各アプリの終了結果を確認します。

1. 終了した工程・終了コード・配備記録と、現在のAzure上の状態を確認する。
2. 同じparamsと`.local/<environment>/state/latest.json`を保持して原因を修正する。SQLやイメージ構築の失敗を飛ばさない。
3. Gatewayで停止した場合はGatewayを再試行し、その成功後にPortalを実行する。Portalで停止した場合はPortalの専用コマンドから再開できる。
4. アプリの稼働・公開ヘルス・イメージdigestと、変更対象の業務動作を確認して記録する。

両アプリを続けて更新する場合は次を使います。既存の成功結果だけを理由に後続工程へ進まず、各子コマンドの今回の終了コードで判断します。

```sh
python3 scripts/deploy_applications.py --config "$DEPLOY_PARAMS_FILE" --execute
```

無人実行では保護入力に加え、Portalの`portalApiKeys.platformGroupId`と`groupNamePattern`を事前設定します。初回の対話入力もこの2項目だけを同じparamsへ保存します。表示名はGraph、製品は標準API正本、検証購読数上限はconfigから求めます。

## 構築後にClaudeをGUIで追加する

FoundryでClaudeモデルをデプロイし、Claude Codeから公開GatewayのAnthropic Messages APIを呼びます。初期構築後の作業です。ポータルの入力値と貼り付けるポリシーは[Claude Messagesの設定例](../apps/apim/policies/portal-examples/claude-messages.md)にあります。

1. **Microsoft Foundry → モデルカタログ → Claude → デプロイ**で、購入条件、リージョン、バージョン、使用枠とPrivate Link対応を確認し、デプロイ名とFoundryリソースのエンドポイントを控えます。 [FoundryでClaudeモデルを使う](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-claude)
2. **Foundryリソース → Access control (IAM)**で、監査中継のContainer AppのマネージドIDへ推論権限を割り当てます。**API Management → Backends**でGateway配備済みの `core-audit-relay` を確認します。中継からCoreのFoundryへの名前解決とHTTPS到達性を確認します。 [FoundryのEntra認証](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/configure-entra-id) · [監査中継の設定](../apps/apim/policies/portal-examples/claude-messages.md)
3. **API Management → APIs → Add API → HTTP**でID `manual-claude-messages`、サフィックス`models/claude`を設定し、購読を必須にします。 [HTTP APIの手動作成](https://learn.microsoft.com/en-us/azure/api-management/add-api-manually)
4. **API → All operations → Inbound processing → Code editor**に[Claudeの貼り付け用XML](../apps/apim/policies/portal-examples/claude-messages.xml)を保存してから、操作ID `messages`、`POST /v1/messages`を追加します。固定の下流パスは`/anthropic/v1/messages`です。`Calculate effective policy`で入口、認証、利用枠、監査、資格情報の除去、固定転送先を確認します。SSEではAPIMの`forward-request`を応答バッファなしに設定します。`count_tokens`は利用するモデルとクライアントで対応を確認した場合に追加します。 [Claudeで使えるAPI](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/claude-models)
5. **Products**で購読必須の`manual-model-users`を作成し、キーで提供するAPIを関連付けます。本人Bearerを提供する場合は、購読不要の`manual-entra-users`に対象APIを関連付けます。トークンはGateway APIの `Gateway.Invoke` 委任scopeを使います。期限切れ、scope不足、無効キーと有効Bearerの併送、許可外操作の拒否を確認します。公開状態は開発者ポータルへの表示設定です。実際のアクセス停止にはAPIとの関連付け解除や購読の停止を使います。 [APIMの購読とアクセス制御](https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions)
6. **Application Gateway → Rules／Path map**で、Gateway用リスナーが新しいパスをAPIMへHTTPSで転送し、有効なWAFがPreventionで動作することを確認します。共通WAFで開始し、正常要求の誤検知を確認した場合は、対象のパス・項目・ルールを絞って見直します。パス別WAFを追加する際は、そのパスで有効になる設定全体と入口ヘッダーの書換えを照合します。SSE用の応答バッファ設定も確認します。 [WAFの関連付け](https://learn.microsoft.com/en-us/azure/web-application-firewall/ag/policy-overview) · [Application GatewayのSSE設定](https://learn.microsoft.com/en-us/azure/application-gateway/use-server-sent-events)
7. **Claude Code**では`https://<GATEWAY_HOST>/models/claude`をGatewayのベースURLにし、購読キーまたはBearerとデプロイ名を設定します。MessagesのJSONとSSEを試し、`apim-request-id`から認証主体・操作・結果と、保存した入力・応答を照合します。Claudeの利用量はFoundry側の診断で確認します。 [Claude Codeの設定例](../apps/apim/policies/portal-examples/claude-messages.md) · [本文監査の対象](reference/request-body-audit.md) · [Claude CodeのGateway設定](https://code.claude.com/docs/en/llm-gateway)

本文監査を必須とする提供範囲では、Claude本文の採取と復元結果を受け入れてから公開します。手動追加の所有者と変更履歴を運用台帳に記録します。Coreの再配備前は、API・Product・WAF・経路の差分を確認します。手動追加したAPI／モデルも、所有APIM・Foundry・RGを削除する`azd down`では撤去対象です。

## DNS用マネージドIDと権限を更新する

Azure Public DNSは事前作成したUAMIを使います。IDを変更する場合は、新しいIDへDNS権限を付与し、paramsの`certificate.azureDnsManagedIdentityResourceId`と、必要なら`certificate.azureDnsSubscriptionId`を更新して同じ環境へ反映します。公開TLS Jobへの関連付け・実際の証明書更新を確認してから旧権限を回収します。外部で作成したDNS用ID・公開ゾーンはCoreの撤去対象に含めません。

IDや権限の変更前に[DNS事前準備](setup/05-public-dns.md)を確認してください。

## ログ・監視・本人接続を確認する

Coreの診断ログ・LAW export・監査Storageと、企業共通監査は所有者と信頼境界を分けて扱います。query非保持の既存環境更新は [ログ保存手順](testing/log-privacy.md)のprepare→export→routeの順で実施し、変換後ログのStorage到達を確認してから直接保存経路を切り替えます。設定のreadback成功だけで実到達を合格にしません。

APIキー管理・購読同期はPortalの管理画面を使います。内部の購読管理サービスが認可とAPIMの書込みを担当し、APIMは別のプロファイル照会サービスから制限設定を取得します。 [受入条件](reference/network-security-validation.md#購読管理とプロファイル照会の分離)[購読別の制限・観測](testing/subscription-limits.md)ではSQLの制限設定、APIMへの反映、欠測を分けて確認します。

## 管理VMから保守データを取得する

保守の責任者は、担当者本人の Microsoft Entra Object ID に、作業の開始前に必要な権限を手動で付与し、終了時に回収します。作業票に対象・操作・開始／終了時刻・付与担当・回収担当を記録します。手動のAzure RBAC割当は期限で自動削除されません。PIMが利用できる場合は任意で使えます。

初回構築では、監査用のPE・DNS、管理VM上のクライアントと対象一覧、担当者本人向けのACA保守ロール定義を準備します。Gateway・Portalの配備時には、実際に構築したApp・Job・DBを対象一覧へ追加します。[既存環境への限定適用](../scripts/operations/maintenance.py)もロール定義と対象一覧を構築します。旧資材で既に作ったVMの保守権限を回収する場合、対象MI・割当スコープ・DBユーザー・証明書更新の依存を別途照合し、権限管理者が個別に回収します。

### 保守の付与・回収

1. [管理VMへの本人接続](operations/management-vm-access.md)を参照し、対象VMへ一時的に `Virtual Machine User Login` を本人へ付与します。クライアントを使う場合、OS管理者は本人を一時的に `core-maintenance` へ追加します。
2. 次のうち作業に必要なロールだけを付与し、新規の割当IDを保護記録へ保存します。SQLの場合は [DB単位の付与・回収手順](operations/management-sql-access.md) を使います。
3. 本人で接続し、許可された対象と必要な操作を確認します。SQLの作業後はSQLセッションを閉じ、DBAが回収し、本人がVMにいる間に対象DBへの新規接続拒否を確かめます。
4. 残る保守ロールを回収して新規操作の拒否を確認し、SSH・ログ・execのセッションを終了します。ゲストの本人用CLIで `az logout` を実行し、OSグループとVMログイン割当を回収した後、新規SSH接続の拒否を確認します。直接・継承された権限と、セッション・トークンが残り得る時間を作業票に記録します。

| 操作 | ロールと割当スコープ | 権限の範囲 |
| --- | --- | --- |
| 監査Blob | `Storage Blob Data Reader` ／ 対象コンテナー | 指定Blobの読取 |
| 保存ログ | `Log Analytics Data Reader` ／ 対象Workspace | Workspaceのログ照会 |
| ACA Appログ | `appLogs` ／ 対象App | App・revision・replica、Appトークン、console・systemログ |
| ACA App exec | `appLogs` と `appExec` ／ 対象App | 対象コンテナーでのコマンド実行。参照できる秘密とAppのIDで利用できる接続先にも影響する |
| ACA環境ログ | `environmentLogs` ／ 対象ACA環境 | 環境情報とDev APIトークン。環境トークンはログ取得以外にも使える |
| ACA Jobログ | `jobLogs` ／ 対象Job | Jobと子資源の参照、Jobトークン、実行ログ |

ACAロール定義のIDはCore出力 `managementMaintenance.roleDefinitionIds`、対象のResource IDは配備出力から取得します。既存割当と継承を確認してから、権限管理者が手動割当を行います。以下のIDはプレースホルダーです。

```sh
az role assignment create --assignee-object-id '<OPERATOR_OBJECT_ID>' \
  --assignee-principal-type User --role '<ROLE_DEFINITION_ID>' \
  --scope '<EXACT_RESOURCE_ID>' --query id --output tsv

az role assignment delete --ids '<RECORDED_ASSIGNMENT_ID>'
```

空環境から構築した構成では、公開TLSのDNS更新・Key Vault操作とPortalの署名証明書を[専用ACA Job](operations/certificate-jobs.md)が担当します。Gateway・Portalのイメージビルド中は、管理VMのIDへ一時的に `AcrPush` が追加されるため、担当者のシェル保守時間をビルドと重ねません。SSH権限を回収した後、本人の既存セッションを終了し、ローカルの公開鍵でも再接続できないことを実環境で確認します。

### 保護入出力とSQLを含む8種類の操作

[管理クライアント](../scripts/management_client.py) と対象一覧は `/opt/core/maintenance/` にroot所有で配置されます。担当者はVM内で本人のAzure CLIセッションを作り、`sudo`を付けずに実行します。入出力先は本人所有の700ディレクトリ、入力ファイルは600にします。チェックアウト内ではルートの `.local/<environment>/` を使います。出力は未作成のファイルを指定し、本文を端末や公開ログへ転記しません。

ブラウザーを使えない管理VMでは、組織がデバイスコード認証を許可している場合に以下のように本人でサインインします。認証コードは保護された接続で扱います。本人のCLIプロファイルは共有せず、保守クライアントの実行中にアカウントを切り替えません。条件付きアクセスで制限される場合は、組織で認められた本人認証を確認します。クライアントはAzure CLIの現在のテナントと本人のMicrosoft Graph Object IDを確認するため、ゲストからMicrosoft Graphへの既存Entra経路と、Azure CLIが本人プロファイルを取得できることも受入で確かめます。 [Azure CLIで本人のObject IDを確認する手順](https://learn.microsoft.com/en-us/entra/identity/users/how-to-get-signed-in-identity)

```sh
az login --tenant '<AZURE_TENANT_ID>' --allow-no-subscriptions --use-device-code --output none
```

```sh
/opt/core/maintenance/venv/bin/python -I -B /opt/core/maintenance/management_client.py \
  --operator-object-id '<OPERATOR_OBJECT_ID>' \
  --output "$MAINTENANCE_OUTPUT_DIRECTORY/blob.bin" \
  blob-read --resource-id '<AUDIT_STORAGE_RESOURCE_ID>' \
  --container '<CONSTRUCTED_CONTAINER_NAME>' --blob '<BLOB_NAME>'
```

ほかの操作では、毎回新しい `--output` を指定し、サブコマンドと追加引数を選びます。

| 操作 | サブコマンドと追加引数 | 選択・確認事項 |
| --- | --- | --- |
| 監査Blobの取得 | `blob-read --resource-id '<AUDIT_STORAGE_RESOURCE_ID>' --container '<CONSTRUCTED_CONTAINER_NAME>' --blob '<BLOB_NAME>'` | inventory内のStorageとコンテナーだけ。Blob PE/DNS到達と読取権限を確認 |
| 保存ログの照会 | `workspace-query --resource-id '<WORKSPACE_RESOURCE_ID>' --query-file "$MAINTENANCE_INPUT_DIRECTORY/query.kql"` | KQLは保護ファイルで渡す。本文を引数へ直書きしない |
| App consoleログ | `app-logs --resource-id '<APP_RESOURCE_ID>' --type console --revision '<REVISION_NAME>' --replica '<REPLICA_NAME>' --container '<CONTAINER_NAME>' --tail 50` | revision・replica・containerをすべて明示する |
| App systemログ | `app-logs --resource-id '<APP_RESOURCE_ID>' --type system --tail 50` | console用のrevision・replica・containerは指定しない |
| ACA環境systemログ | `environment-logs --resource-id '<ENVIRONMENT_RESOURCE_ID>' --tail 50` | App単位のsystemログと区別する。環境tokenで取得 |
| Job実行ログ | `job-logs --resource-id '<JOB_RESOURCE_ID>' --execution '<EXECUTION_NAME>' --replica '<REPLICA_NAME>' --container '<CONTAINER_NAME>' --tail 50` | 過去の実行を明示し、Jobを起動せずに読む |
| Appへの限定exec | `app-exec --resource-id '<APP_RESOURCE_ID>' --revision '<REVISION_NAME>' --replica '<REPLICA_NAME>' --container '<CONTAINER_NAME>' --command-file "$MAINTENANCE_INPUT_DIRECTORY/command.txt" --seconds 60` | 明示したコンテナーへ、保護コマンドファイルの内容だけを送る |
| SQL直接保守 | `sql-query --database credentials --query-file "$MAINTENANCE_INPUT_DIRECTORY/maintenance.sql"` | `credentials`または`portal`の構築DBだけを選択。SQL本文は保護ファイルへ保存 |

SQLは担当者本人のEntra IDで認証し、構築サーバーのFQDNへTCP1433で接続します。固定ODBC Driver 18で暗号化とサーバー証明書検証を維持します。SQL本文・結果・失敗時の詳細は保護ファイルへ保存し、tokenは保存しません。更新SQLの実行と結果保存は別の段階です。commit後に結果保存が失敗した場合も、更新を未実行と決めつけて再送しません。

ログ取得は`follow=false`の有限tailで、`--tail`は0〜300です。JobログはPreview APIを使い、client内でmetadata用とreplica／token用のAPI版を固定しています。実環境での提供状況・認可・実行ログ保持を確認するまでは取得成功を保証しません。

execの`command.txt`には、目的が明確で短時間で終了するコマンドだけを保護保存します。最初の確認なら`pwd`などを選び、対話shell、長時間の待受け、無制限な追尾、環境変数や秘密の一括表示を避けます。`--seconds`は1〜600で、既定60秒です。この値が制限するのはclientの取得待機時間であり、切断時にリモートコマンドが必ず終了するという保証ではありません。protocol上、正常切断でもコマンドの終了コードは確認できません。保存出力と対象の事後状態を確認し、応答不明の更新コマンドを無条件で再送しません。

### 実環境の確認結果を記録する

今回の資材はBicepコンパイルと独立した合成入力によるローカル試験で確認しています。初期Azure配備、担当者本人の実トークン取得、SQL直接保守、Blob／Workspaceの実データ、App／環境／JobのDev API、WebSocket exec、Serialの実ログインは別の実環境試験です。未実施の操作を検証済みにしません。8操作それぞれについて、対象、権限scope、ネットワーク到達、取得内容、必要なら拒否条件を保護証跡へ記録します。

## Serial Consoleのローカル認証を準備する

初期構築は`management.adminUsername`へローカル認証用パスワードを設定し、SSHのパスワード認証無効と公開鍵を維持します。このパスワードはSerial専用の認可機能ではなく、VM内のローカル認証にも使えます。Serial Consoleへ接続する人間には、Azure側の接続権限・サービス条件と、VM内のユーザー名／パスワードの両方が必要です。[Linux Serial Consoleの公式条件](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/linux/serial-console-linux)も確認します。

生成秘密は端末の`.local/<environment>/secrets/management-serial-credential.json`に保持します。POSIXではディレクトリ700・ファイル600、Windowsでは実DACLを検査・保護し、既存の共有ACLを黙って書き換えません。パスワードをparams/config、通常ログ、チャットへ転載せず、接続時だけ保護ファイルから非公開で参照します。構築成功後の`boundVmId`と秘密世代はVM個体との対応確認用であり、ログイン成功を示す値ではありません。

接続時はWindows端末でAzure Portalへ認証し、Core stateと同じ管理VMの「Serial console（シリアルコンソール）」を開きます。ログインプロンプトを確認して、同じ秘密ファイルの`adminUsername`と`password`を入力します。まず`id`・`pwd`など状態を変えない操作でTTYを確認し、作業後はローカルセッションからログアウトします。既存VMで手動設定したパスワードを使っている場合は、その運用秘密を使い、未結び付けの初期生成ファイルから現在のパスワードを推測しません。

同じ未作成VMの初期再試行ではpendingの同じ秘密を使います。初期化完了後に同名VMを撤去し、新規構築するときは旧世代を保護archiveへ残して新しい秘密を作ります。元の初期作成証跡がない既存VMは秘密を読まず、手動設定済みのパスワードも上書きしません。部分失敗でVMだけ作成済みになった場合は、保護stateに保存した元404・Core入力世代と、所有Core／management子配備の同世代・VM UUID出力を先に照合します。生成ARM入力を回収済みでも元証跡が残っていれば照合できますが、証跡なしで現在のVMへ結び付け直しません。guestの初期化要求が記録された後の回復ではパスワードを再送せず、元の資格情報によるlogin PAM認証を最大1回に制限します。完了済みの世代は再設定せず、その後の手動変更を保持します。

Serialの接続・ローカルログイン・TTY操作と、SSH鍵認証が維持されていることを別々に実確認します。新しいManaged Run Commandのprotected入力、固定Ubuntuイメージ上のローカル認証・PAM、実Serialログインは、合成試験やBicepコンパイルだけで実環境検証済みとは扱いません。PAM確認は認証ログ・ロックアウト状態へ影響し、SerialのWebSocket／TTY接続とは別の確認です。既存利用者の有無も確認してから接続します。

## 本人接続のSQL台帳

Brokerの本人対応・セッションはAzure SQL、下流OAuth tokenはCMが管理します。Portalのログイン・キー台帳と、本人接続状態は別用途です。DB障害、照合ジョブ、SQL復元後の失効・再開は [SQL台帳とGateway配備](setup/11-deploy-gateway.md)と [接続セッションの契約](reference/credential-pool-design.md)に従います。ローカルSQLの試験状態をAzureの業務台帳へ同期しません。

## 撤去する

対象環境の利用を終了するときに実行します。実行中の音声セッション等を停止し、構築時のparams・config・`.azure/`・`.local/<environment>/state/latest.json`、監査保持とDB／必要データの保管を確認します。

`azd down`では、azd本体のRG削除確認より前に`predown` hookがモデル・APIM・アプリ・管理VMの削除を開始します。対象Tenant／Subscription・環境名・所有5RGは、コマンド実行前に照合します。

```sh
export WORKFORCE_CONFIG="$DEPLOY_PARAMS_FILE"
AZD_NON_INTERACTIVE=false azd down --environment "$DEPLOY_ENVIRONMENT"
```

上の指定はこのコマンドだけ対話を有効にします。表示された所有RGの削除確認には対象を照合して`Y`を入力します。`predown`はその確認より先に削除を始めるため、実行前の対象照合は省略しません。非対話の既定回答で停止した場合も、作成済みの先行回収記録を保持して同じ入力から再開します。

azd本体でKey Vault・Foundryの完全削除を確認された場合は`N`を選びます。続くCoreの`postdown`が対象IDと保護設定を照合します。

Coreの`postdown`は`--purge`の有無にかかわらず、対象のKey Vault・Foundry・APIMの論理削除記録を照合して、保護設定が許すものを完全削除します。Key Vaultのpurge protectionや監査WORMを解除する指定ではありません。

azd本体の撤去も`infra/main.bicepparam`を評価するため、`predown`は同じ環境の`generated/main.parameters.json`へ秘密なしの撤去用入力を再生成します。azd環境の`EGAKUMI_ARM_PARAMETERS_FILE`にはその相対パスだけを保持し、秘密値を`.env`へ追加しません。生成入力はazd本体の撤去が終わるまで保持し、`postdown`で回収します。再試行に必要な元のparams・secret・stateは消しません。

| 段階 | 処理・完了確認 |
| --- | --- |
| 変更前 | stateと現物のID・種類・命名・region・所有タグ・接続先、全監査保持を照合し、ACA管理RGの実IDを保存する |
| `predown` | モデル→CMの接続権限・接続・provider→APIM→Broker・全ジョブ→Portal CA→共有CAE→ACAサブネット解放→管理VM・残存する所有NICの順で回収する |
| azd本体 | 所有5RGを削除する。専用DB・ACR／イメージ・Vault秘密・専用IDも所有RGと共に回収する |
| `postdown` | 5RGと記録済みACA管理RGの404、対象3サービスの削除記録・保護による保持、API Centerの削除履歴を記録する |

Gateway／Portalの配備成功記録がなくても、固定IDと実資源の所有・接続先を照合します。全アプリ・ジョブの不存在を確認してからCAEを削除し、サブネットの解放を待ちます。ACAの`infrastructureResourceGroup`はAzure側の削除を待ち、名前で`ME-*`を探索・直接削除しません。保存した旧・新の管理RGも確認対象です。

Locked WORMがある場合は変更前に停止します。Unlockedでも構成の監査保持が有効なら解除しません。Key Vaultのpurge protectionは期限まで保持します。環境名だけで削除保護を切り替えません。外部DNSゾーン・DNS用ID・Entra登録・同意・企業ハブは撤去対象に広げません。

結果は`.local/<environment>/state/cleanup-predown.json`と`.local/<environment>/state/cleanup-postdown.json`に保存されます。成功・未完了・保護による保持・確認不能を区別し、保持・残存があるものを完全消去済みとは扱いません。

### 撤去後に新しく構築する

同じ製品名・環境名で新しく構築するときは、次の順でAPIM識別子とEntraのCM戻り先を更新します。構築途中の再試行や、稼働中の環境更新では識別子を保持します。

1. 同じ環境の`cleanup-postdown.json`で、旧APIMと関連する旧環境の稼働資源が撤去済みであることを確認する。保持資源・削除履歴・内部accountの未確認は、それぞれの記録で区別する。
2. 第1章で選んだ`DEPLOY_PROFILE`を設定し、次のコマンドを実行する。コマンドは対応する撤去記録を検査してから、paramsの`services.apimInstanceId`を更新する。旧stateは`.local/<environment>/history/apim/<旧識別子>/state/`へ保管し、通常の`state/`は新しい構築用に空にする。paramsの他の設定とsecretは保持する。

   ```sh
   python3 -I -B scripts/prepare_local_environment.py \
     --environment "$DEPLOY_ENVIRONMENT" --profile "$DEPLOY_PROFILE" \
     --new-apim-instance
   python3 -I -B scripts/prepare_registration.py --config "$DEPLOY_PARAMS_FILE"
   ```

3. [ToolsのWeb戻り先の確認](setup/03-entra-tools.md#cmのweb戻り先を確認する)に従い、その環境のCM用Web戻り先1件を新しいURIへ置き換え、保存後に確認する。Gateway・Portalの公開名を継続する場合は、その2件の戻り先、アプリの権限・同意、有効なsecretを保持する。
4. 同じparams・azd環境で[構築手順](setup/08-provision-core.md)を進める。撤去済みの識別子のまま、新規構築を開始しない。

APIMと用途別WAFポリシーが、新しい共通の識別子を使います。環境名、公開ホスト名、その他の資源名は維持します。SQLデータを保持した環境のAPIM差替えは、購読・本人接続の移行を伴うため、この新規構築手順と分けて計画します。

### CM内部アカウントの解放を確認できない場合

CMの正規の撤去経路は、接続権限→接続→provider→APIM本体→削除履歴のpurgeです。この経路はup/downのhookに実装しています。[provider削除](https://learn.microsoft.com/rest/api/apimanagement/authorization-provider/delete?view=rest-apimanagement-2024-05-01)と[APIMのpurge](https://learn.microsoft.com/rest/api/apimanagement/deleted-services/purge?view=rest-apimanagement-2024-05-01)は公開APIですが、CMが背後に作る[credential store](https://learn.microsoft.com/azure/api-management/credentials-process-flow#credential-provider)の内部accountを直接削除・解放確認する公開操作は確認できていません。

providerのDELETE成功・一覧不在・親APIMの404・削除履歴の404だけで、内部accountの解放や同名CMの再構築成功を判定しません。CM採用構成の`postdown`は、`status: resources-absent-with-unverified-credential-manager-release`を返します。公開資源・削除履歴の結果は`resourceCleanupStatus`に、内部accountの未確認は`credentialManager.internalAccountReleaseVerified: false`と`sameNameCredentialRecreationVerified: false`に分けます。終了コード0も、この内部解放の証明にはなりません。

同名APIMの再構築で「同じAPIMサービス名を別アカウントが使用中」というCM作成エラーが出た場合は、未解決として記録します。Azure公式OSSにも[同一症状の記録](https://github.com/Azure/azure-service-operator/blob/b9dcbe48feaf1173f48ab28dd5edc48b514d3f4d/v2/internal/testcommon/samples_tester.go#L120)がありますが、そこに解放手順・原因確定・解放期限は示されていません。soft-deleteの保持期間や一般的なpurge後待機を、このエラーの解決保証に読み替えません。

削除・配備の結果、エラーコード、発生時刻、相関IDをローカルの証跡から整理し、Microsoftサポートへサービス側の調査・解放方法を確認します。秘密値・トークンは共有せず、資材からの問い合わせ送信も行いません。未公開account IDを推測したDELETEや未定義の`deleteAll`指定は追加しません。同名再構築の実測は別の有料試験として扱い、`azd down`から自動的に環境を作り直しません。

### 削除待ち・再実行時に確認すること

- APIMやアプリが削除中なら要求を重ねず、同じIDの消失を待つ。待機期限を超えたら同じ入力・stateを保持して原因と現在状態を確認する。
- APIM本体の削除が`ServiceLocked`で拒否された場合は、所有情報を再確認し、約30分の待機枠内で再試行する。CMの削除処理は繰り返さず、APIMの削除受付後はGETで完了を確認する。他の409や412は自動再試行せず停止する。
- CMの個別削除と親APIMの削除を分ける。一覧消失を用いる限定条件でもprovider自身のGET404とは記録せず、`deletionVerified`・`listAbsenceVerified`・`parentDeletionVerified`を区別する。
- ACA管理RGだけが残る場合はAzureの削除完了を確認し、同じparamsで`python3 scripts/deployment/lifecycle.py postdown --config "$DEPLOY_PARAMS_FILE"`を再実行する。
- Key Vaultの完全削除が非同期で受け付けられた場合は、Azureが返す待機間隔に従って操作状態を読み、最後に対象の削除履歴の404も確認する。既定の待機枠は15分で、要求をその場で再送しない。上限・失敗で止まったら同じ入力と記録を保持し、操作と削除履歴の現在状態を確認してから再開する。この待機の改善はローカル検証済みで、実環境での再確認は別工程とする。
- Log Analyticsのforce完全削除やAPI Centerのpurgeは通常処理に含めない。`apiCenterRetention`が`unverified`の場合も確認不能の理由を保持する。
- モデル消失と利用枠・費用の反映は別に確認する。利用枠は次の`capacity`で再確認し、他環境の割当まで0にしない。

### 一部の所有RGが残る、またはResourceGroupNotFoundで止まる場合

部分構築の撤去でazd本体が空のRGを削除対象に含めない場合や、途中まで削除済みのRGがある場合は、`postdown`が`resource-groups-remain`で停止することがあります。先行回収の記録を確認し、以下の手順で残る所有RGだけを回収します。

1. `.local/<environment>/state/cleanup-predown.json`の対象が一致し、`result.status`が`ready-for-azd-down`であることを確認する。モデル・APIM・アプリ／ジョブ・CAE・サブネット・管理VMの先行回収と保持確認が済んだ記録を使う。
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

監視配備の成功前に停止すると、資源の一部が存在しても`.local/<environment>/state/latest.json`がない場合があります。通常の撤去hookは正確なstateなしでは続行しません。

1. 同じSubscriptionのAzure Portalで対象の配備・時刻・環境名・製品名・所有タグ・操作履歴を確認し、実際に作成された資源IDを特定する。処理中の操作を重複実行しない。
2. 成功済み配備の完全なoutputsやバックアップがある場合は、管理担当者が実資源・保護設定と照合して正確な記録を回復する。アプリ用`--deployment-name`を撤去用stateの代用にしない。
3. 成功済みoutputsがない場合はstateを捏造せず、管理担当者が確認できた資源だけをAzure Portalで限定回収する。存在するモデル・APIM・アプリ・ネットワークの依存順と監査保持を守る。
4. 実IDごとの残存・保持・費用を記録する。削除保護・WORM・purge protectionを回避しない。

生成された秘密入力だけを回収する場合は`python3 scripts/deployment/lifecycle.py clear-inputs --config "$DEPLOY_PARAMS_FILE"`を使います。対象は`.local/<environment>/generated/main.parameters.json`であり、元の保護入力・SSH鍵・配備記録を削除せず、Azure資源も撤去しません。azdが入力を使っている構築・撤去の実行中には、この回収を行いません。
