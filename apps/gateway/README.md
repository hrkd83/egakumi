# Gateway

共通基盤の上へ、APIMのAPI定義と認証窓口を一つの配備単位として配置します。API定義とアプリ本体は別のフォルダーで管理します。

```text
apps/gateway/
├── apim/
│   ├── apis/                   標準APIのJSON定義（1 API 1ファイル）
│   ├── policies/               APIMのXMLポリシー
│   ├── schemas/                API入出力のスキーマ
│   └── api-publication.bicep   API・バックエンド・製品・購読
├── credential-broker/
│   ├── credential_broker/      内部FastAPIアプリ
│   ├── sql/                    本人とCM接続の対応台帳
│   ├── tests/
│   └── Dockerfile
└── deploy/                     DB・CM・アプリのBicepと配備処理
```

## 配備する

`azd up`は共通基盤を準備した後、この配備コマンドを呼び出します。個別更新・再試行では、構築済み基盤と同じparamsを使い、リポジトリのルートから実行します。基盤だけを先に作る場合は`azd provision`を使います。

```bash
python3 scripts/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE"
python3 scripts/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE" --execute
```

1行目は保存済みCore stateからのローカル計画、2行目はAzureへの配備です。`--deployment-name`を指定するCIでは、計画時にもCore配備結果をAzureから読み取ります。認証窓口のイメージ作成・登録、credentials DB・CM・実行用ID、SQL初期化、アプリ・照合ジョブ、API・ポリシー・製品・購読、対象パスへのWAF調整の順に進みます。SQL初期化や起動が失敗した場合はAPI登録へ進みません。本人接続を使わない設定では認証窓口の配備を省略します。

既存のDB・CM接続・購読は同じIDで更新します。paramsやconfigを書き換えず、秘密値をイメージへ入れません。`apiDefinitions: []`は[apis/](apim/apis/)にある標準APIをまとめて選び、非空なら指定したAPIを対象にします。定義は`<API名>.json`の1 API 1ファイルで管理し、モデルの配備設定とCMプロバイダーの設定を含めません。

通常のAPIキーは`released-apis`（表示名`Released APIs`）製品に紐づき、リリース済みAPIをまとめて利用します。この製品とAPIとの関連もGateway配備資材に含みます。各API定義の`released: true`を関連付けの正本とし、標準APIはすべて明示しています。追加APIは省略時`false`です。`false`へ変更して配備すると、そのAPIと生成MCPの通常製品との関連を解除します。API本体や既存の検証製品・購読を削除せず、今回指定しなかったAPIにも触れません。

`model-users`・`pii-users`・`connection-users`等の製品と検証用購読は維持します。本人接続の開始・完了確認・終了APIは同じ通常キーで利用できる補助関連を付けます。ブラウザー用の匿名補助APIは製品に含めません。`azd up`では基盤完成後にこのGatewayコマンドを呼んで製品を用意し、その成功後にPortalを配備します。個別コマンドで段階構築・更新する場合も同じ順序です。

計画は`.local/<environment>/state/gateway-plan.json`、完了結果は`.local/<environment>/state/gateway-deployment.json`に保存します。実際のMicrosoftサインインと各業務APIの疎通は、配備成功後に確認します。

Azure Speechを公開する場合は、SSMLの`/models/azure-speech/v1/synthesize`とWAVの`/models/azure-speech/v1/recognize`に、既存の`wafpol-agw-<stem>-custom-mime`を関連付けます。パスは選択したAPI定義のPOST・本文形式・MIME契約から決めます。共通WAFの設定を引き継ぎ、正規のSSML/WAVを拒否する規則`920420`だけをこの完全パスで調整します。`920470`（Content-Type構文）、他の管理規則・本文検査、Portalや認証callbackの経路は維持します。公開WAVは`Content-Type: audio/wav`とし、codec指定はAPIMのバックエンド用ヘッダーに付与します。日本語の認識言語`ja-JP`は`azure-speech-v1.json`のバックエンドURLに明記します。

[詳細な配備手順](../../docs/setup/credential-sql.md)・[API定義](apim/README.md)・[認証窓口](credential-broker/README.md)・[運用と更新](../../docs/operations.md)

## 継続配備

[gateway.yml](../../.github/workflow-templates/gateway.yml.example)は、同じコマンドを計画・実行の順に呼びます。GitHub Environment `gateway`に`CORE_PARAMS_PATH`、`CORE_DEPLOYMENT_NAME`とAzure OIDC用の公開IDを設定し、現在の構成ではVNet内のself-hosted runnerを使います。イメージ作成は管理VMで行い、Actionsからの別ビルドは不要です。ワークフローは手動起動です。

音声の回帰試験用画面は`tests/browser/realtime.html`で管理します。`scripts/realtime_browser.py`のローカル専用relayからだけ配信し、Portal・GatewayのイメージやAzure配備には含めません。

公開版は無効なワークフローテンプレートです。[有効化手順](../../.github/workflow-templates/README.md)に従い、自身の環境を設定してから使用します。
