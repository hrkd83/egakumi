# 12. Portalを個別配備する

前：[11. Gatewayの個別配備](11-deploy-gateway.md) · [README](../../README.md) · 次：[13. 初回構築の受入確認](13-verify-deployment.md)

PortalはMicrosoft Entraでサインインし、Model GatewayとTool Gatewayを本人の `Gateway.Invoke` アクセストークンで試す画面です。Microsoft Graphの本人プロフィールはGatewayからBrokerのOBOを通じて取得します。例外発行の購読キーについては[APIMの購読運用](../operations/access-and-keys.md)を参照してください。

## CoreとPortal配備の分担

| 工程 | 作成・更新するもの |
| --- | --- |
| [Core構築](08-provision-core.md) | 共通SQLサーバーとPrivate Endpoint、ACR、Container Apps環境、初期Container App、Portalの実行IDと認証用公開証明書 |
| [Entra登録](09-egakumi-portal.md)と[Gateway配備](11-deploy-gateway.md) | Portalの専用アプリ登録・証明書と委任の設定、APIMのAPIとポリシー、Brokerの配備 |
| Portal配備 | PortalのSQLデータベース、SQL初期化ジョブ、認証と試用に必要なKey Vault参照、Gateway宛のFirewall許可、PortalのContainer App |

Coreが作る初期Container AppにはMicrosoftの初期イメージが載ります。公開App Gatewayが使うHTTPSプローブは `/api/health` のHTTP 200を要求するため、Portal本体の配備まではバックエンドが異常と判定されます。

実装は [apps/portal](../../apps/portal/) にあり、[deploy](../../apps/portal/deploy/) にBicepとビルド処理、[sql](../../apps/portal/sql/) にSQLマイグレーションがあります。端末とCIは同じ [deploy_portal.py](../../scripts/deployment/deploy_portal.py) を使います。

## 配備する

Core、Entra登録、Gateway配備が完了したら、同じ環境の保護paramsと `.local/<environment>/state/latest.json` を使います。設定するEntra値と証明書については[第09章](09-egakumi-portal.md)を参照してください。

対象Azure環境へログインし、Python 3.11以上とBicep CLIを使える端末で、リポジトリのルートから計画を確認して実行します。

```sh
python3 -B scripts/deployment/deploy_portal.py --config "$DEPLOY_PARAMS_FILE"
python3 -B scripts/deployment/deploy_portal.py --config "$DEPLOY_PARAMS_FILE" --execute
```

1. 管理VMで公開ソースをビルドし、非公開ACRへPortalイメージを登録します。管理VMにビルド時だけ必要な `AcrPush` を割り当て、終了後に回収します。
2. 共通SQLサーバー上にPortal専用DBを作成し、手動のSQL初期化ジョブでマイグレーションと実行IDのデータベース権限を設定します。再配備では適用済みのマイグレーションと既存の認証データを保持します。
3. 認証コールバックのWAF設定、Portalサブネットから公開GatewayへのHTTPS許可、Key VaultとマネージドIDの設定を配備し、Portal本体をdigest指定のイメージへ更新します。
4. Portalの正常なリビジョン、App GatewayのHTTPSバックエンドとプローブ、公開 `/api/health` を確認します。Container Appsの `/api/health/ready` はSQLを検査し、配備コマンドは公開 `/api/health/api-auth` でSQLと試用の暗号鍵を確認します。

終了コード `0` と今回の配備に対応する `.local/<environment>/state/portal-deployment.json` の `status: deployed`、`publicHealthVerified: true` を確認します。途中で失敗した場合は、同じ環境の入力でPortal配備を再試行できます。別の完了済みCore記録を使う場合は `--state-file` または `--deployment-name`、既存イメージを使う場合は `--image-digest sha256:<digest>` を指定します。

## 認証と保存データ

公開URLは `https://portal.<domain>` です。App GatewayでTLSを終端し、WAFで検査してからFirewallを経由してACAへHTTPSで転送します。ブラウザー向けの戻り先、Secure Cookie、Origin検査は公開URLを基準にします。

PortalのサインインはOIDCを使います。Gateway向けのアクセストークンは本人の同意済み `Gateway.Invoke` を使って別に取得します。PortalはKey Vaultにある非エクスポート鍵でEntraのクライアントアサーションへ署名します。専用DBに利用者とログインセッションを保存し、試用資格情報は専用のKey Vault鍵で暗号化してサーバー内に保持します。文書・音声などの業務本文はPortal DBへ保存しません。[基盤の本文監査](../reference/audit/request-body-audit.md)を参照してください。

## GitHub Actions

GitHub Actionsを使う場合、`.github/workflow-templates/portal.yml.example`も同じ`deploy_portal.py`を呼びます。初回の段階構築と個別配備コマンドは端末から実行できます。イメージは管理VMで作るためrunnerでDockerやSQL接続を準備する必要はありません。Azure管理APIへの接続とPython 3.11以上・Azure CLI・Bicepを準備します。

現在のworkflowに合わせ、`example-vnet`ラベル付きLinux self-hosted runnerとGitHub Environment `portal`を用意して、以下の変数を設定します。

| 変数 | 値 |
| --- | --- |
| `AZURE_CLIENT_ID` | Azure配備用OIDCログインID |
| `AZURE_TENANT_ID` | コア基盤の`azureTenantId` |
| `AZURE_SUBSCRIPTION_ID` | コア基盤のサブスクリプション |
| `CORE_PARAMS_PATH` | runner上の`.local/<environment>/params.local.json`への絶対パス。checkout後に保護した入力から準備し、別環境のファイルを流用しない |
| `CORE_DEPLOYMENT_NAME` | 完了済みのコア基盤のsubscription deployment名 |

OIDCのfederated credentialは`repo:<owner>/<repository>:environment:portal`を対象にします。公開の未信頼PRを配備runnerで実行しません。CIのAzureログイン先は`azureTenantId`で、利用者のサインイン先`entraTenantId`と区別します。

## 受入確認

1. 許可されたテナントの利用者でサインインし、API試用時に同一人物のBearer認証が完了することを確認します。
2. JSON・SSE・音声・MCPの代表的な試用を行います。Graphでは本人のプロフィールを取得し、追加認証の要求がある場合は同じアカウントで再試行します。
3. 別のアカウント、期限切れの取引、ログアウト後の利用が拒否されることを確認します。
4. [第13章の受入](13-verify-deployment.md)へ進みます。購読キーの例外経路も検証する場合は、基盤管理者が発行した限定スコープのキーで外部クライアントから確認します。

## ローカル起動と撤去

[ローカル認証テスト](../testing/portal-local-auth.md)には、開発用Entraの戻り先、ローカルSQL、保護された環境設定と起動方法を記載しています。基盤全体の `azd down` は同じリソースグループ内のPortal DB、初期化ジョブ、Container App、ACRも撤去対象にします。
