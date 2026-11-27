# Egakumi

> **WIP / Work in Progress（開発中）**
>
> 本リポジトリは、設計・実装・検証を継続している開発中のプロジェクトです。仕様・API・構成・導入手順には未完成・未検証の部分があり、互換性を保たない変更を行う場合があります。公開資料や勉強会動画の存在は、本番利用に向けた検証の完了を意味しません。本番利用前に、導入先でセキュリティ・権限・動作・構築／撤去・費用を評価・検証してください。

Egakumiは、AIを企業・組織の「働き手」として迎え、既存の業務環境・権限・運用につなぐための構想と共通基盤です。人がAIに相談する体験から、AIが働きかけるバディ、専門AIチームへの業務委託へと発展する体験を目指します。

このリポジトリでは、その土台となる **Egakumi Core** を公開しています。企業でAIを利用するための接続、APIの保護と認証、利用制限、監視・監査を、Azure上で構築・運用するための資材です。将来の体験構想と、このCoreが提供する機能の範囲は区別して説明します。

[勉強会動画](docs/videos.md) · [初回構築の手順](#新規クローンから構築する) · [Apache 2.0](LICENSE) · [素材・商標の扱い](MEDIA-LICENSES.md)

変更に着手する前に、[リポジトリ共通ルール（AGENTS.md）](AGENTS.md)と[開発ルール](DEVELOPMENT.md)を確認してください。**企業・実環境固有の情報は、Git管理する資材・公開文書・画像・ログ・コミット履歴へ含めません。** 資材はこの1つのcheckoutを正本として使い、実設定・秘密・記録はルートの`.local/<environment>/`、azd専用情報は`.azure/`へ保護保存します。この2領域はGit・公開用の配布物へ含めません。

## Egakumi Coreの構成

Application Gateway・Azure Firewall・API Management・Private Endpointで保護したFoundryを中心に、AIモデル・PII・文書解析とCMによる本人OAuth接続の正式APIを公開するAzure基盤です。BicepとAzure Developer CLIで構築・撤去し、外部サービス向けの汎用protocol中継も備えます。

初回構築は、共用基盤、証明書のEntra登録、Gateway、Portalの順に進みます。GatewayとPortalは、それぞれ初回配備・更新・再試行ができます。

APIM名は`apim-<environment>-<product>-<apimInstanceId>`です。公開例の`<AUTO_GENERATED_APIM_INSTANCE_ID>`は初回自動生成のマーカーです。第1章の準備コマンドが6文字の英小文字・数字へ置換してローカルparamsへ保存し、第03章で同じ値からEntraのCM戻り先を確定します。構築・更新・途中再開では識別子を保持し、撤去後の新規構築は[新しいAPIM識別子の準備](docs/operations.md#撤去後に新しく構築する)から進めます。

共通基盤の構築資材は[infra/](infra/README.md)、証明書Jobと管理VMで実行するスクリプトは[infra/guest/](infra/guest/)にあります。[apps/api-gateway/](apps/api-gateway/README.md)にAPI定義、APIMポリシー、WAFポリシーの設定、Gatewayの配備資材をまとめています。認証窓口のアプリとSQLは[apps/credential-broker/](apps/credential-broker/README.md)にあります。

業務APIの入口は、購読キーを提示した場合はAPIM購読キー、キーなしのBearer要求はEntraユーザートークンにより認可します。不正なキーをBearerで救済しません。Graph等の本人OAuth接続を使うAPIは、入口認可に続いて[Credential Managerの共通接続機能](docs/reference/credential-manager-login.md)を利用します。公開APIと配備モデルの正本は [API定義](apps/api-gateway/apis/)と参照configです。[Gatewayの購読キー認証](docs/reference/gateway-key-auth-plan.md)を参照してください。

HTTP・WebSocket・MCP・Realtime・CMを含む業務APIへ、同じURLのEntra Bearer認可を追加する公開資材を実装しています。対象はAPI正本・生成MCP・設定済みCM保護入口から自動生成し、ブラウザーの一時取引認証は専用経路に保持します。Entra設定を省略した旧構成はキー方式を維持します。この全API対応の変更はAzureへ再配備しておらず、実接続の受入は対象環境ごとに確認します。[認可設定と確認範囲](docs/setup/07-params.md)を参照してください。

PortalはEntraサインインと本人のBearerによるAPI試用を提供します。OAuthの `Gateway.Invoke` を標準のクライアント接続とし、必要な場合だけ基盤管理者がAzureポータルでAPIM購読キーを発行します。[購読キーの運用](docs/operations/access-and-keys.md)

## 新規クローンから構築する

**`azd up` はCoreを構築して終了します。** このリポジトリでは `azd provision` と同じ処理です。CoreはAPIM本体、Foundryとモデル、ACA環境、ネットワーク・通信制御、Private Endpoint、認証用の公開証明書などを準備します。基盤用のEntraアプリはAPI・Tools・Credential・Portalの4つです。GatewayとPortal本体は、証明書をEntraに登録してから別々に配備します。

| 順番 | 手順 | この段階で行うこと |
| --- | --- | --- |
| 01 | [事前準備](docs/setup/01-prerequisites.md) | 端末・Azure権限・命名・IP計画を確認し、paramsのひな型とAPIM識別子を用意 |
| 02 | [egakumi-apiを登録](docs/setup/02-entra-api.md) | GatewayのAPIをEntraに登録し、委任スコープとv2アクセストークンを設定 |
| 03 | [egakumi-toolsを登録](docs/setup/03-entra-tools.md) | Credential Manager用のWeb戻り先、Graphの委任権限、専用シークレットを設定 |
| 04 | [egakumi-credentialを登録](docs/setup/04-entra-credential.md) | 本人確認用のWeb戻り先と専用シークレットを設定 |
| 05 | [公開DNSの準備](docs/setup/05-public-dns.md) | Azure Public DNSゾーンを用意し、NSを委任 |
| 06 | [DNS用マネージドIDの準備](docs/setup/06-dns-identity.md) | 証明書の発行と更新に使うIDを作成し、DNSの操作権限を付与 |
| 07 | [paramsを完成させる](docs/setup/07-params.md) | 導入先のID・公開名・IPと、Entra・DNSの準備結果をひな型へ反映 |
| 08 | [Coreを構築する](docs/setup/08-provision-core.md) | `azd up` で共用基盤、Portal初期サンプル、認証用公開証明書2枚を準備 |
| 09 | [Core構築後のEntra登録](docs/setup/09-egakumi-portal.md) | `egakumi-api` にGraph OBO用証明書を登録し、`egakumi-portal` を作成・証明書登録してparamsを追記 |
| 10 | [ハブ＆スポーク接続（任意）](docs/setup/10-hub-spoke.md) | 必要な環境でピアリング・DNS・経路・Firewallを整え、Gateway・Portalの配備前に通信を確認 |
| 11 | [Gatewayを個別配備する](docs/setup/11-deploy-gateway.md) | `deploy_gateway.py` でBroker・CMと、APIMのAPI・ポリシー・バックエンド・製品を配備 |
| 12 | [Portalを個別配備する](docs/setup/12-deploy-portal.md) | `deploy_portal.py` で認証DB・SQL権限・Portal本体を配備し、Portal本体で初期サンプルを置き換え |
| 13 | [初回構築を受け入れる](docs/setup/13-verify-deployment.md) | 公開DNS・TLS、Gatewayのキー／Bearer、Portalのヘルス・サインインと必要な機能を確認 |

検証・本番の標準configはそのまま使います。既定値を確認したい場合や、変更が必要な場合に参照してください。 [configリファレンス](docs/reference/config-options.md)

Core完了からPortal本体の配備までは、App GatewayでPortalバックエンドが異常表示でも進めます。Portalの `/api/health` が HTTP 200 を返すことは、第12章の配備後に確認します。

Azure基盤は `azureTenantId`、ユーザー認証は `entraTenantId` で指定します。本文監査を有効にする標準構成では、Azure基盤と認証先に同じテナントを使います。 [テナントの設定](docs/setup/07-params.md#同一テナントと別テナントの指定)

構築・配備には、同じ環境の保護paramsとstateを使います。途中で停止した場合は、作成済みの基盤と記録を保持し、失敗した工程を再実行します。GitHub Actionsや管理端末上のDockerは標準手順の前提に含まれません。 [更新・再試行の手順](docs/operations.md#同じ環境を更新する)

撤去時は、同じparamsとazd環境で `azd down` を実行します。 [撤去の範囲・順序・再実行](docs/operations.md#撤去する)

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
| `rg-<environment>-<product>-maint` | Ubuntu管理VM、保守と非公開ACR向けイメージビルド |

RGと共通基盤用マネージドIDは`azd up`が作成します。[管理ポータル](docs/setup/12-deploy-portal.md)用のACA環境・初期CA・非公開ACRと、`portal.<domain>`のApp Gateway入口も共通基盤に含みます。Portal本体・専用DB・SQL初期化・ヘルス監視・WAF調整は`deploy_portal.py`で配備します。認証窓口の実行用ID・credentials DB・CM・アプリは`deploy_gateway.py`が担当します。

Coreの[標準API一覧](docs/reference/api-design-proposal.md)は正式19 API（HTTP 13 API・19 operation、WebSocket 6 API）です。[配備用JSON](apps/api-gateway/apis/)は1 API 1ファイルで管理し、サンプルAPIは含めません。PII生成MCPと、採用時のCM補助API2個は別計上します。Microsoft Graphへの本人接続もCMを使う正式APIです。

## 費用を確認する

費用は、基盤の稼働、Portal・Brokerの実行と保存、モデル・APIの利用、通信・監視・監査の合計で確認します。対象リージョン、SKU、台数、保持期間は導入先paramsと参照configから確定します。APIを呼ばなくても、稼働・保存に伴う費用があります。

| 費用の対象 | 現行構成で確認するもの |
| --- | --- |
| ネットワーク・入口 | Application Gateway、Firewall、Public IP、Private Endpoint、Private DNS、通信処理・転送量 |
| API・AI | APIM、Foundryで採用するモデル・PII・文書解析・音声等の利用量、採用するAPI Centerのプラン |
| Portal・Broker | Portal用とBroker用のContainer Apps環境、各アプリのレプリカとCPU・メモリー、SQL初期化・定期ジョブ、非公開ACRとイメージ保存 |
| 永続化・運用 | 共用SQLサーバー内のPortal／credentials DB、管理VMとディスク、Key Vault、LAW／App Insights、ログexport、監査Storageと保持・複製、アラート・通知 |
| Core外の共有資源 | 公開DNS・ドメイン、企業ハブ・ExpressRoute／VPN・中央DNS等、採用する共有資源 |

標準構成は [test.json](config/test.json)・[production.json](config/production.json)、アプリ資源は [Gateway Bicep](apps/api-gateway/deploy/)・[Portal Bicep](apps/portal/deploy/)で確認できます。導入・変更時に、これらを含む全資源で見積もり、稼働後は対象Subscriptionの利用額と照合します。撤去後も保護・保持により残るデータや共有資源の費用を確認します。

以前の一部基盤だけの概算は [整理前の費用試算](docs/history/pre-20261101-cost-estimate.md)へ分離しました。現行Core全体の金額としては扱いません。更新・撤去は [運用手順](docs/operations.md)を参照してください。

## 構築後に使う資料

- [更新・撤去・途中失敗時の扱い](docs/operations.md)：同じparamsとazd環境を使う`azd down`、削除履歴、保持対象を確認します。
- [通常運用・改修の手順](docs/operations/README.md)・[運用テスト](docs/testing/operations-tests.md)：アプリ・API・モデル、secret・証明書、利用資格・監視などの通常保守を確認します。
- [API・認証・モデル・音声・証明書の仕様](docs/reference/platform.md)：公開APIの契約と運用上の動作を確認します。
- [通信要件とネットワーク設計](docs/reference/network-communication-requirements.md)：業務・保守・Azure基盤通信、[NSGの全規則と判断](docs/reference/network-security-design.md)、[Firewallの全規則と判断](docs/reference/firewall-policy-design.md)、[検証と実証結果の管理](docs/reference/network-security-validation.md)を対応付けます。
- [Foundryモデルの分類・リリース日一覧](docs/reference/foundry-model-catalog.md)：モデルの提供状態と、用途・プロバイダー別の公開日・出典・参考性能値を確認します。
- [企業ネットワークへの接続（任意）](docs/setup/10-hub-spoke.md)：ハブ＆スポーク、中央DNS、経路伝搬等を担当部隊と調整します。
- [テスト・検証](docs/testing/README.md)：構築テスト・動作テストの対象、手順、受入条件と、検証用の補助資料をまとめています。
- [TIPS: CodexをAIゲートウェイにOAuthで接続する](docs/tips/codex-gateway-oauth.md) — Azureポータルで専用アプリを登録し、端末でCodexを手動設定する任意の手順。
- [音声の回帰試験画面](tests/browser/realtime.html)：`scripts/realtime_browser.py`のローカル専用relayで利用します。PortalやGatewayのAzure配備には含めません。
- [開発共通ルール・保守手順](DEVELOPMENT.md)：コーディング規約、[テストの共通ルール](DEVELOPMENT.md#テストの共通ルール)と[実行方法](DEVELOPMENT.md#テストの実行方法)、任意の配備補助を扱います。

関連資料：[勉強会動画](docs/videos.md)・[過去の調査・検証記録](docs/history/README.md)。

## ライセンスと開発方針

本プロジェクトの独自ソフトウェア・文書・図は [Apache License 2.0](LICENSE) で提供します。第三者のライブラリ・素材は各権利者の条件に従い、音楽・人物イラスト・ブランド素材・音声サンプルなどの扱いは [MEDIA-LICENSES.md](MEDIA-LICENSES.md) を参照してください。Egakumiの名称・ロゴについて、商標の使用許諾を与えるものではありません。

設定例は架空値であり、実環境での動作確認を示すものではありません。実環境の設定・作業記録は環境別の`.local/<environment>/`へ保護保存し、公開側へ転載せず、[共通ルール](AGENTS.md)に従ってください。

clone後は`python3 -I -B scripts/install_git_hooks.py`でローカルの混入検査を有効にします。[検査の範囲・GitHub Actions・許可リストの管理](docs/public-data-guard.md)も確認してください。
