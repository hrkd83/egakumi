# Egakumi

> **WIP / Work in Progress（開発中）**
>
> 本リポジトリは、設計・実装・検証を継続している開発中のプロジェクトです。仕様・API・構成・導入手順には未完成・未検証の部分があり、互換性を保たない変更を行う場合があります。公開資料や勉強会動画の存在は、本番利用に向けた検証の完了を意味しません。本番利用前に、導入先でセキュリティ・権限・動作・構築／撤去・費用を評価・検証してください。

Egakumiは、AIを企業・組織の「働き手」として迎え、既存の業務環境・権限・運用につなぐための構想と共通基盤です。人がAIに相談する体験から、AIが働きかけるバディ、専門AIチームへの業務委託へと発展する体験を目指します。

このリポジトリでは、その土台となる **Egakumi Core** を公開しています。企業でAIを利用するための接続、APIの保護と認証、利用制限、APIキー管理、監視・監査を、Azure上で構築・運用するための資材です。将来の体験構想と、このCoreが提供する機能の範囲は区別して説明します。

[勉強会動画](docs/videos.md) · [導入ガイド](docs/setup/01-prerequisites.md) · [Apache 2.0](LICENSE) · [素材・商標の扱い](MEDIA-LICENSES.md)

変更に着手する前に、[リポジトリ共通ルール（AGENTS.md）](AGENTS.md)と[開発ルール](DEVELOPMENT.md)を確認してください。**企業・実環境固有の情報は、Git管理する資材・公開文書・画像・ログ・コミット履歴へ含めません。** 資材はこの1つのcheckoutを正本として使い、実設定・秘密・記録はルートの`.local/<environment>/`、azd専用情報は`.azure/`へ保護保存します。この2領域はGit・公開用の配布物へ含めません。

## Egakumi Coreの構成

Application Gateway・Azure Firewall・API Management・Private Endpointで保護したFoundryを中心に、AIモデル・PII・文書解析とCMによる本人OAuth接続の正式APIを公開するAzure基盤です。Bicepと`azd up/down`で構築・撤去し、外部サービス向けの汎用protocol中継も備えます。

`azd up`は共有基盤・モデル・Gateway・Portal ACA初期サンプルと、KV内のPortal認証鍵・公開証明書を準備します。公開証明書を取得して[専用Portal Entraアプリを手動登録](docs/setup/entra-portal.md)した後、Portal本体を独立してビルド・配備します。

初期構築は、前提・paramsを準備して`azd up`を実行し、公開証明書取得→手動Entra登録→Portal本体配備へ進みます。標準API正本を常用で解決し、初回Portalでは管理グループObject IDと表示名正規表現の2項目をPortal本体の個別配備コマンドで対話入力できます。GitHub ActionsやPC上のDockerは必要ありません。GatewayとPortalの[個別配備コマンド](docs/operations.md#同じ環境を更新する)も、更新・再試行に引き続き使えます。

APIM名は`apim-<environment>-<product>-<apimInstanceId>`です。公開例の`<AUTO_GENERATED_APIM_INSTANCE_ID>`は初回自動生成のマーカーです。第1章の準備コマンドが6文字の英小文字・数字へ置換してローカルparamsへ保存し、第2章で同じ値からEntraのCM戻り先を確定します。構築・更新・途中再開では識別子を保持し、撤去後の新規構築は[新しいAPIM識別子の準備](docs/operations.md#撤去後に新しく構築する)から進めます。

共通基盤は`infra/`、管理VM内で動く証明書・OS設定・保守clientの資材は[infra/guest/](infra/guest/)にあります。Gatewayの配備資材は[apps/gateway/](apps/gateway/)にまとめ、API定義・ポリシーは`apps/gateway/apim/`、認証窓口のアプリ・SQLは`apps/gateway/credential-broker/`、配備用Bicepとイメージ構築スクリプトは`apps/gateway/deploy/`に分けています。

業務APIの入口は、購読キーを提示した場合はAPIM購読キー、キーなしのBearer要求はEntraユーザートークンにより認可します。不正なキーをBearerで救済しません。Graph等の本人OAuth接続を使うAPIは、入口認可に続いて[Credential Managerの共通接続機能](docs/setup/credential-manager-login.md)を利用します。公開APIと配備モデルの正本は [API定義](apps/gateway/apim/apis/)と参照configです。[Gatewayの購読キー認証](docs/reference/gateway-key-auth-plan.md)を参照してください。

HTTP・WebSocket・MCP・Realtime・CMを含む業務APIへ、同じURLのEntra Bearer認可を追加する公開資材を実装しています。対象はAPI正本・生成MCP・設定済みCM保護入口から自動生成し、ブラウザーの一時取引認証は専用経路に保持します。Entra設定を省略した旧構成はキー方式を維持します。この全API対応の変更はAzureへ再配備しておらず、実接続の受入は対象環境ごとに確認します。[認可設定と確認範囲](docs/setup/04-params.md)を参照してください。

公開入力71主項目の配置整理も資材に実装しています。Graph登録はparamsの`credentialProviders.graphDelegated`、表示名・scope・poolとログイン共通設定はconfigへ分けます。Portalは管理グループと全標準製品から内部承認・検証購読を生成し、configの`portalApiKeys.verificationMaxSubscriptions`（初期5）を適用します。旧入力・既存非標準値を無断変換せず、現在のアプリID・secret・認証方式とfallbackを保持します。この一括改修のAzure反映・全環境再構築E2Eは未実施です。[判断記録と移行条件](docs/setup/04-params.md#公開入力71主項目の判断記録)を確認してください。

## 新規クローンから構築する

1〜5で前提・設定を準備し、6の`azd up`で基盤・GatewayとPortal初期サンプルを構築し、Entra登録後に9でPortal本体を配備して、10の受入確認へ進みます。7は企業ネットワークを使う場合の構成手順、8・9はアプリの個別更新・再試行の入口です。企業接続を後から整える必要がある場合は、先に`azd provision`で基盤だけを作り、7の完了後に8・9を順に実行できます。Azure Public DNSの公開ゾーン保持やドメイン購入の費用は、Coreとは別に確認します。

| 順番 | 手順 | この段階で用意するもの |
| --- | --- | --- |
| 1 | [前提要件の準備](docs/setup/01-prerequisites.md) | GitHubからのクローン、管理端末のツール、Azure権限、命名・公開名・IP計画 |
| 2 | [Entraアプリの準備](docs/setup/02-entra.md) · [APIアプリ](docs/setup/02a-entra-api.md) · [Codexクライアント](docs/setup/02b-entra-codex.md) | 現行Portal・本人接続の共用構成と、APIトークン認可向けの追加登録 |
| 3 | [公開DNSの準備](docs/setup/03-public-dns.md) | Azure Public DNSの公開ゾーン・NS委任・DNS用マネージドID |
| 4 | [paramsの準備・リファレンス](docs/setup/04-params.md) | 導入先のID・公開名・IP・アプリ登録を記入した保護ローカルparams |
| 5 | [configの調整・リファレンス](docs/setup/05-config.md) | テスト／本番の標準構成、SKU・モデル・保護機能の選択 |
| 6 | [azd upでの構築](docs/setup/06-deploy.md) | 事前確認・基盤・公開DNS・Gateway・Portal初期サンプルと認証証明書の準備 |
| 7 | [ハブ＆スポーク対応（任意）](docs/setup/07-hub-spoke.md) | ピアリング、中央DNS・Private DNS、出口・ルート伝搬の調整と再確認 |
| 8 | [Gatewayの個別配備](#8-gatewayを配備する) | API・ポリシー・製品・購読、CM、credentials DB、認証窓口・SQL初期化・照合ジョブ |
| 9 | [Portalの個別配備](#9-portalを配備する) | Next.jsイメージ、Portal専用DB・SQL初期化、アプリ、ヘルス監視・認証callback用WAF設定 |
| 10 | [疎通・受入確認](docs/testing/integration-tests.md) | 公開APIの正規キー・本人接続、PortalのEntraサインイン・サインアウト |

### 8. Gatewayを配備する

`azd up`のGateway工程と同じ入口です。Gatewayだけを更新・再試行するときは、第4章で設定した`DEPLOY_PARAMS_FILE`と、同じクローンの`.local/<environment>/state/latest.json`を使います。最初のコマンドで計画を確認し、次のコマンドで反映します。GatewayとPortalを同時に配備せず、各処理の完了を待ちます。

```sh
python3 scripts/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE"
python3 scripts/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE" --execute
```

管理VMで認証窓口のイメージを作成・ACRへ登録し、credentials DB・CM・実行用IDを配備します。SQL初期化と認証窓口の正常起動を確認してから、API・バックエンド・ポリシー・製品・購読を登録します。本人接続を使わない設定では認証窓口の工程を省略します。[1 API 1ファイルで管理する標準19 API正本](apps/gateway/apim/apis/)を常用で解決します。

`.local/<environment>/state/gateway-deployment.json`の`status: deployed`を確認します。計画と反映は個別コマンドの`--execute`で区別します。公開`apiDefinitions`・`publishApis`入力は廃止し、旧項目は空配列やfalseでも拒否します。詳細と必要権限は[Gateway配備手順](docs/setup/credential-sql.md#4-gatewayを配備する)を参照してください。

### 9. Portalを配備する

`azd up`のPortal工程と同じ入口です。Portalだけを更新・再試行するときや、`azd provision`後の個別構築では、Gatewayの完了を確認してから同じparamsで実行します。

```sh
python3 scripts/deploy_portal.py --config "$DEPLOY_PARAMS_FILE"
python3 scripts/deploy_portal.py --config "$DEPLOY_PARAMS_FILE" --execute
```

管理VMでNext.jsのイメージを作成・ACRへ登録し、共通SQLサーバー内のPortal専用DBを準備します。SQL初期化の成功後にPortalアプリを更新し、Application Gatewayのヘルス監視と認証callbackのWAF設定を反映して、公開ヘルスAPIを確認します。完了結果は`.local/<environment>/state/portal-deployment.json`です。[Portalの配備手順](docs/setup/portal.md)も参照してください。

購読別の任意の利用制限も、このGateway→Portalの順で配備します。既定は全項目無制限で、Portalの保存はSQL内のプロファイル更新だけです。APIMは固定ポリシーで設定を参照します。[利用量の観測と上限の確認](docs/testing/subscription-limits.md)を、運用開始後の確認に使ってください。

`azd up`が成功すれば8・9の追加実行は不要です。途中失敗時はその工程で停止し、保存したstateと各アプリの配備記録を保持します。原因を修正して該当コマンドを再実行できます。基盤だけを構築・更新する場合は`azd provision`を使います。配備成功の後、公開GatewayのAPI呼び出しとPortalのEntraサインインを別途確認して利用を開始します。

撤去は、構築時と同じparams・azd環境で `azd down` を実行します。`deploy_gateway.py`・`deploy_portal.py` で追加したアプリ、ジョブ、DB、イメージ、専用IDも回収対象です。配備が途中で失敗した場合も現在の所有資源を照合し、共有CAEを解放してから所有RGを削除します。[撤去の範囲・順序・再実行](docs/operations.md#撤去する)を確認してください。

`<AZURE_TENANT_ID>`・`<ENTRA_TENANT_ID>`などの`<...>`は、自社環境の値へ置き換える欄です。製品名・環境名・ドメイン・リージョン・クローン先は導入者が決めます。画面の撮影例や公開JSONのサンプル値を、自社の確定値として転記しないでください。JSONの項目名とリポジトリ内のファイル名は、資材の契約としてそのまま使います。

Azure構築先は`azureTenantId`、CM等の認証テナントの既定値は`entraTenantId`で指定します。同一・別テナントの両方に対応し、`entraTenantId`省略時はAzure側と同じ値を使います。標準GraphのOAuth tenantは`entraTenantId`から導出します。追加providerの別tenantは`params.credentialProviders.additional[].oauthTenantId`で指定できます。詳細は[Entraの準備](docs/setup/02-entra.md#azureとentraのテナントを決める)を確認してください。

購読キーと本人接続のセッションはアプリのサーバー側で保持します。ブラウザはBFF等を経由し、承認済みSaaSは秘密設定へキーを登録します。購読IDを基本の利用主体として計数し、本人向けAPIだけ追加の本人接続を照合します。

新規クローンに秘密情報や過去の構築状態は含まれません。Entra／DNSの資格情報は担当者が準備し、`azd up`が用途・保存先を説明してから非表示で入力を受け付けます。利用者が秘密JSONや`.local/<environment>/secrets/`を手作業で作る必要はありません。

## 作成される構成

| 用途 | 通信経路・配置 |
| --- | --- |
| APIのHTTPS入口 | 利用端末またはPortalサーバー → AppGW:443 → Firewall → Internal APIM → Foundry PE／登録済みbackend |
| PortalのHTTPS入口 | 利用端末 → AppGW:443 → Core内Portal。API実行はPortalサーバーがGatewayを呼び出す |
| 汎用外部中継（個別連携時） | APIM → 許可した外向き経路 → 外部REST／MCP／A2A。サンプル入口はCore標準APIに含めない |
| WebSocket音声 | 制御・音声をAppGW → Firewall → APIMで中継 |
| WebRTC音声 | 接続準備・制御はGateway。音声本体は端末とAzureの間 |
| 運用 | Linux管理VM、Key Vault、Log Analytics／Application Insights、監査Storage、API Center |

gRPCは基盤の提供要件から廃止しました。専用TCP入口と新規gRPC APIは作成せず、以前の設備・生成元は所有照合と撤去に限って扱います。

| 作成するRG名の形式 | 用途 |
| --- | --- |
| `rg-<environment>-<product>-id` | AppGW・Gateway・Portal・SQL初期化等のマネージドID |
| `rg-<environment>-<product>-nw` | VNet、NSG、ルート、Firewall、AppGW、PE、Private DNS |
| `rg-<environment>-<product>-monitor` | 監視・監査 |
| `rg-<environment>-<product>-svc` | APIM、Foundry・Project・モデル、API Center、Key Vault、共用SQL、ACA環境・Gateway／Portal・ジョブ、ACR |
| `rg-<environment>-<product>-maint` | Ubuntu管理VM、証明書の取得・更新 |

RGと共通基盤用マネージドIDは`azd up`が作成します。[管理ポータル](docs/setup/portal.md)用のACA環境・初期CA・非公開ACRと、`portal.<domain>`のApp Gateway入口も共通基盤に含みます。Portal本体・専用DB・SQL初期化・ヘルス監視・WAF調整は`deploy_portal.py`で配備します。認証窓口の実行用ID・credentials DB・CM・アプリは`deploy_gateway.py`が担当し、`azd up`の最後に両コマンドを順に呼び出します。

Coreの[標準API一覧](docs/reference/api-design-proposal.md)は正式19 API（HTTP 13 API・19 operation、WebSocket 6 API）です。[配備用JSON](apps/gateway/apim/apis/)は1 API 1ファイルで管理し、サンプルAPIは含めません。PII生成MCPと、採用時のCM補助API2個は別計上します。Microsoft Graphへの本人接続もCMを使う正式APIです。

## 費用を確認する

費用は、基盤の稼働、Portal・Brokerの実行と保存、モデル・APIの利用、通信・監視・監査の合計で確認します。対象リージョン、SKU、台数、保持期間は導入先paramsと参照configから確定します。APIを呼ばなくても、稼働・保存に伴う費用があります。

| 費用の対象 | 現行構成で確認するもの |
| --- | --- |
| ネットワーク・入口 | Application Gateway、Firewall、Public IP、Private Endpoint、Private DNS、通信処理・転送量 |
| API・AI | APIM、Foundryで採用するモデル・PII・文書解析・音声等の利用量、採用するAPI Centerのプラン |
| Portal・Broker | 共用Container Apps環境、両アプリのレプリカとCPU・メモリー、SQL初期化・定期ジョブ、非公開ACRとイメージ保存 |
| 永続化・運用 | 共用SQLサーバー内のPortal／credentials DB、管理VMとディスク、Key Vault、LAW／App Insights、ログexport、監査Storageと保持・複製、アラート・通知 |
| Core外の共有資源 | 公開DNS・ドメイン、企業ハブ・ExpressRoute／VPN・中央DNS等、採用する共有資源 |

標準構成は [test.json](config/test.json)・[production.json](config/production.json)、アプリ資源は [Gateway Bicep](apps/gateway/deploy/)・[Portal Bicep](apps/portal/deploy/)で確認できます。導入・変更時に、これらを含む全資源で見積もり、稼働後は対象Subscriptionの利用額と照合します。撤去後も保護・保持により残るデータや共有資源の費用を確認します。

以前の一部基盤だけの概算は [整理前の費用試算](docs/history/pre-20261101-cost-estimate.md)へ分離しました。現行Core全体の金額としては扱いません。更新・撤去は [運用手順](docs/operations.md)を参照してください。

## 構築後に使う資料

- [更新・撤去・途中失敗時の扱い](docs/operations.md)：同じparamsとazd環境を使う`azd down`、削除履歴、保持対象を確認します。
- [通常運用・改修の手順](docs/operations/README.md)・[運用テスト](docs/testing/operations-tests.md)：アプリ・API・モデル、secret・証明書、利用資格・監視などの通常保守を確認します。
- [API・認証・モデル・音声・証明書の仕様](docs/reference/platform.md)：公開APIの契約と運用上の動作を確認します。
- [通信要件とネットワーク設計](docs/reference/network-communication-requirements.md)：業務・保守・Azure基盤通信、[NSGの全規則と判断](docs/reference/network-security-design.md)、[Firewallの全規則と判断](docs/reference/firewall-policy-design.md)、[検証と実証結果の管理](docs/reference/network-security-validation.md)を対応付けます。
- [Foundryモデルの分類・リリース日一覧](docs/reference/foundry-model-catalog.md)：モデルの提供状態と、用途・プロバイダー別の公開日・出典・参考性能値を確認します。
- [企業ネットワークへの接続（任意）](docs/setup/07-hub-spoke.md)：ハブ＆スポーク、中央DNS、経路伝搬等を担当部隊と調整します。
- [テスト・検証](docs/testing/README.md)：構築テスト・動作テストの対象、手順、受入条件と、検証用の補助資料をまとめています。
- [音声の回帰試験画面](tests/browser/realtime.html)：`scripts/realtime_browser.py`のローカル専用relayで利用します。PortalやGatewayのAzure配備には含めません。
- [開発共通ルール・保守手順](DEVELOPMENT.md)：コーディング規約、[テストの共通ルール](DEVELOPMENT.md#テストの共通ルール)と[実行方法](DEVELOPMENT.md#テストの実行方法)、任意の配備補助を扱います。

関連資料：[勉強会動画](docs/videos.md)・[過去の調査・検証記録](docs/history/README.md)。

## ライセンスと開発方針

本プロジェクトの独自ソフトウェア・文書・図は [Apache License 2.0](LICENSE) で提供します。第三者のライブラリ・素材は各権利者の条件に従い、音楽・人物イラスト・ブランド素材・音声サンプルなどの扱いは [MEDIA-LICENSES.md](MEDIA-LICENSES.md) を参照してください。Egakumiの名称・ロゴについて、商標の使用許諾を与えるものではありません。

設定例は架空値であり、実環境での動作確認を示すものではありません。実環境の設定・作業記録は環境別の`.local/<environment>/`へ保護保存し、公開側へ転載せず、[共通ルール](AGENTS.md)に従ってください。

clone後は`python3 -I -B scripts/install_git_hooks.py`でローカルの混入検査を有効にします。[検査の範囲・GitHub Actions・許可リストの管理](docs/public-data-guard.md)も確認してください。
