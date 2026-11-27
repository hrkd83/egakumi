# Gatewayの統合配備

共通基盤の上へ、APIMのAPI定義と認証窓口を一つの配備単位として配置します。本文監査が有効な環境では、中継アプリと保存ワーカーもこの工程で配備します。

```text
apps/
├── api-gateway/
│   ├── apis/                   標準APIのJSON定義（1 API 1ファイル）
│   ├── policies/               APIMのXMLポリシー
│   ├── schemas/                API入出力のスキーマ
│   ├── api-publication.bicep   API・バックエンド・製品
│   ├── waf-*.json              GatewayとPortalの入口に適用するWAF契約
│   └── deploy/                 統合配備とAPIM・CM設定
├── credential-broker/
│   ├── credential_broker/      内部FastAPIアプリ
│   ├── sql/                    本人とCM接続の対応台帳
│   └── deploy/                 DB・実行用ID・アプリの配備とイメージ構築
└── audit-relay/
    ├── audit_relay/            本文監査の中継・保存処理
    └── deploy/                 本文監査用の中継アプリと保存ワーカーの配備
```

## 配備する

初回構築では、`azd up`によるCore構築と公開証明書のEntra登録を済ませた後、[Gatewayを個別配備](../../../docs/setup/11-deploy-gateway.md)します。個別更新・再試行では、構築済み基盤と同じparamsを使い、リポジトリのルートから実行します。

```bash
python3 scripts/deployment/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE"
python3 scripts/deployment/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE" --execute
```

1行目は保存済みCore stateからのローカル計画、2行目はAzureへの配備です。`--deployment-name`を指定するCIでは、計画時にもCore配備結果をAzureから読み取ります。認証窓口のイメージ作成・登録後、本文監査が有効なら監査用アプリを配備します。その後、credentials DB・CM・実行用ID、SQL初期化、Broker・照合ジョブ、API・ポリシー・製品、対象パスへのWAF調整の順に進みます。SQL初期化や起動が失敗した場合はAPI登録へ進みません。

既存のDB・CM接続・購読は同じIDで更新します。paramsやconfigを書き換えず、秘密値をイメージへ入れません。[apis/](../apis/)にある標準API正本を常用で解決します。公開params/configの`apiDefinitions`・`publishApis`によるAPI選択・切替は廃止し、旧項目は空配列やfalseでも拒否します。定義は`<API名>.json`の1 API 1ファイルで管理し、モデルの配備設定とCMプロバイダーの設定を含めません。標準Graphのアプリ登録・秘密参照はparamsの`credentialProviders.graphDelegated`、表示名・scope・poolはconfigの同slotで管理します。追加providerは定義／登録を`additional[]`の論理名で照合します。今回の配置整理だけで既存アプリID・secret・選択CM由来の本人確認fallbackを切り替えません。

`released-apis`（表示名`Released APIs`）はリリース済みAPIをまとめた製品です。例外発行する購読キーのスコープは、利用者に必要なAPIの範囲を見て管理者が選びます。`released: true`のAPIはGateway配備でこの製品に関連付けます。追加APIの既定は`false`です。`false`へ変更して配備すると、そのAPIと生成MCPの製品との関連を解除します。API本体、検証製品、購読はそのまま使えます。

`model-users`・`pii-users`・`connection-users`等の製品を配備します。例外の購読キーは基盤管理者がAzureポータルで発行します。本人接続の開始・完了確認・終了APIは同じ通常キーで利用できる補助関連を付けます。ブラウザー用の匿名補助APIは製品に含めません。Gateway配備で製品を用意し、その成功を確認してからPortalを個別配備します。

計画は`.local/<environment>/state/gateway-plan.json`、完了結果は`.local/<environment>/state/gateway-deployment.json`に保存します。実際のMicrosoftサインインと各業務APIの疎通は、配備成功後に確認します。

Azure Speechを公開する場合は、SSMLの`/models/azure-speech/v1/synthesize`とWAVの`/models/azure-speech/v1/recognize`に、`wafpol-g-speech-<ID>`を関連付けます。`<ID>`はparamsの`services.apimInstanceId`に保存した構築識別子です。パスは選択したAPI定義のPOST・本文形式・MIME契約から決めます。共通WAFの設定を保ち、正規のSSML/WAVを拒否する規則`920420`とraw本文の`930110`をこの完全パスで調整します。URI・query・header・Cookieには4本のtraversal Blockを合成し、`920470`（Content-Type構文）、他の管理規則と本文検査は維持します。公開WAVは`Content-Type: audio/wav`とし、codec指定はAPIMのバックエンド用ヘッダーに付与します。日本語の認識言語`ja-JP`は`azure-speech-v1.json`のバックエンドURLに明記します。[WAFの用途別構成と検証](../../../docs/testing/waf-compatibility.md)も参照してください。

PIIのApp Gateway WAFは、RESTの`g-pii-rest`、MCPの`g-pii-mcp`、Portalの`p-pii-redact`を各入口に適用します。Gateway側のデータ欄の除外はRESTの`text`とMCPの`params.arguments.text`に限定します。運用中のWAF設定は`scripts/operations/waf.py`で確認・更新します。 [PIIの設定と受入確認](../../../docs/testing/waf-compatibility.md#pii入口の除外をrestとmcpへ分ける)

[詳細な配備手順](../../../docs/setup/11-deploy-gateway.md)・[API定義](../README.md)・[認証窓口](../../credential-broker/README.md)・[運用と更新](../../../docs/operations/README.md)

## 継続配備

[gateway.yml](../../../.github/workflow-templates/gateway.yml.example)は、同じコマンドを計画・実行の順に呼びます。GitHub Environment `gateway`に`CORE_PARAMS_PATH`、`CORE_DEPLOYMENT_NAME`とAzure OIDC用の公開IDを設定し、現在の構成ではVNet内のself-hosted runnerを使います。イメージ作成は管理VMで行い、Actionsからの別ビルドは不要です。ワークフローは手動起動です。

音声の回帰試験用画面は`tests/browser/realtime.html`で管理します。`scripts/clients/realtime_browser.py`のローカル専用relayからだけ配信し、Portal・GatewayのイメージやAzure配備には含めません。

公開版は無効なワークフローテンプレートです。[有効化手順](../../../.github/workflow-templates/README.md)に従い、自身の環境を設定してから使用します。
