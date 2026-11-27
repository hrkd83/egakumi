# Gatewayの統合配備

共通基盤の上へ、APIMのAPI定義と認証窓口を一つの配備単位として配置します。

```text
apps/
├── apim/
│   ├── apis/                   標準APIのJSON定義（1 API 1ファイル）
│   ├── policies/               APIMのXMLポリシー
│   ├── schemas/                API入出力のスキーマ
│   ├── api-publication.bicep   API・バックエンド・製品・購読
│   ├── waf-*.json              GatewayとPortalの入口に適用するWAF契約
│   └── deploy/                 統合配備とAPIM・CM設定
└── credential-broker/
    ├── credential_broker/      内部FastAPIアプリ
    ├── sql/                    本人とCM接続の対応台帳
    ├── tests/
    ├── Dockerfile
    └── deploy/                 DB・実行用ID・アプリの配備とイメージ構築
```

## 配備する

初回構築では、`azd up`によるCore構築と公開証明書のEntra登録を済ませた後、[Gatewayを個別配備](../../../docs/setup/11-deploy-gateway.md)します。今回の公開入力整理のAzure反映と全環境再構築E2Eは未実施です。既存入力の移行は[判断記録](../../../docs/setup/07-params.md#既存入力の移行を確認する)を確認し、非標準値を自動変換しません。個別更新・再試行では、構築済み基盤と同じparamsを使い、リポジトリのルートから実行します。

```bash
python3 scripts/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE"
python3 scripts/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE" --execute
```

1行目は保存済みCore stateからのローカル計画、2行目はAzureへの配備です。`--deployment-name`を指定するCIでは、計画時にもCore配備結果をAzureから読み取ります。認証窓口のイメージ作成・登録、credentials DB・CM・実行用ID、SQL初期化、アプリ・照合ジョブ、API・ポリシー・製品・購読、対象パスへのWAF調整の順に進みます。SQL初期化や起動が失敗した場合はAPI登録へ進みません。本人接続を使わない設定では認証窓口の配備を省略します。

既存のDB・CM接続・購読は同じIDで更新します。paramsやconfigを書き換えず、秘密値をイメージへ入れません。[apis/](../apis/)にある標準API正本を常用で解決します。公開params/configの`apiDefinitions`・`publishApis`によるAPI選択・切替は廃止し、旧項目は空配列やfalseでも拒否します。定義は`<API名>.json`の1 API 1ファイルで管理し、モデルの配備設定とCMプロバイダーの設定を含めません。標準Graphのアプリ登録・秘密参照はparamsの`credentialProviders.graphDelegated`、表示名・scope・poolはconfigの同slotで管理します。追加providerは定義／登録を`additional[]`の論理名で照合します。今回の配置整理だけで既存アプリID・secret・選択CM由来の本人確認fallbackを切り替えません。

通常のAPIキーは`released-apis`（表示名`Released APIs`）製品に紐づき、リリース済みAPIをまとめて利用します。この製品とAPIとの関連もGateway配備資材に含みます。各API定義の`released: true`を関連付けの正本とし、標準APIはすべて明示しています。追加APIは省略時`false`です。`false`へ変更して配備すると、そのAPIと生成MCPの通常製品との関連を解除します。API本体や既存の検証製品・購読を削除せず、今回指定しなかったAPIにも触れません。

`model-users`・`pii-users`・`connection-users`等の製品と検証用購読は維持します。本人接続の開始・完了確認・終了APIは同じ通常キーで利用できる補助関連を付けます。ブラウザー用の匿名補助APIは製品に含めません。Gateway配備で製品を用意し、その成功を確認してからPortalを個別配備します。

計画は`.local/<environment>/state/gateway-plan.json`、完了結果は`.local/<environment>/state/gateway-deployment.json`に保存します。実際のMicrosoftサインインと各業務APIの疎通は、配備成功後に確認します。

Azure Speechを公開する場合は、SSMLの`/models/azure-speech/v1/synthesize`とWAVの`/models/azure-speech/v1/recognize`に、`wafpol-g-speech-<ID>`を関連付けます。`<ID>`はparamsの`services.apimInstanceId`に保存した構築識別子です。パスは選択したAPI定義のPOST・本文形式・MIME契約から決めます。共通WAFの設定を保ち、正規のSSML/WAVを拒否する規則`920420`とraw本文の`930110`をこの完全パスで調整します。URI・query・header・Cookieには4本のtraversal Blockを合成し、`920470`（Content-Type構文）、他の管理規則と本文検査は維持します。公開WAVは`Content-Type: audio/wav`とし、codec指定はAPIMのバックエンド用ヘッダーに付与します。日本語の認識言語`ja-JP`は`azure-speech-v1.json`のバックエンドURLに明記します。[WAFの用途別構成と検証](../../../docs/testing/waf-compatibility.md)も参照してください。

PIIのREST／MCP入口は、承認したローカル最終候補ではApp GatewayのWAFを分けます。`g-pii-rest`はRESTの`text`だけ、`g-pii-mcp`はMCPの`params.arguments.text`だけを`RequestArgValues / Equals`のデータ欄除外へ指定し、Portalの`p-pii-redact`は維持します。APIのURL・キー／Bearer認証・モデル機能・OBOは変更しません。実WAF確認とAzure反映は未実施で、query関連棚卸後のまとめ工程で移行します。通常reconcileは旧`g-pii-redact`を移行元として保持し、新policyとpath bindingを反映して正常REST／MCPの実WAF通過を確認した後にだけ退役します。確認失敗時に旧bindingへ戻せるよう、旧ポリシーを残します。[PIIの設定と移行順](../../../docs/testing/waf-compatibility.md#pii入口の除外をrestとmcpへ分ける)

[詳細な配備手順](../../../docs/setup/11-deploy-gateway.md)・[API定義](../README.md)・[認証窓口](../../credential-broker/README.md)・[運用と更新](../../../docs/operations.md)

## 継続配備

[gateway.yml](../../../.github/workflow-templates/gateway.yml.example)は、同じコマンドを計画・実行の順に呼びます。GitHub Environment `gateway`に`CORE_PARAMS_PATH`、`CORE_DEPLOYMENT_NAME`とAzure OIDC用の公開IDを設定し、現在の構成ではVNet内のself-hosted runnerを使います。イメージ作成は管理VMで行い、Actionsからの別ビルドは不要です。ワークフローは手動起動です。

音声の回帰試験用画面は`tests/browser/realtime.html`で管理します。`scripts/realtime_browser.py`のローカル専用relayからだけ配信し、Portal・GatewayのイメージやAzure配備には含めません。

公開版は無効なワークフローテンプレートです。[有効化手順](../../../.github/workflow-templates/README.md)に従い、自身の環境を設定してから使用します。
