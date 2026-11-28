# scriptsの使い方

| フォルダー | 用途 | 主な入口 |
| --- | --- | --- |
| [setup/](setup/) | 環境別の保護入力とEntra登録情報を準備 | [prepare_local_environment.py](setup/prepare_local_environment.py)、[prepare_registration.py](setup/prepare_registration.py) |
| [deployment/](deployment/) | 基盤の構築・撤去、GatewayとPortalの配備 | [lifecycle.py](deployment/lifecycle.py)、[deploy_gateway.py](deployment/deploy_gateway.py)、[deploy_portal.py](deployment/deploy_portal.py) |
| [operations/](operations/) | 構築後の更新、証明書・モデル・監視・WAFの保守 | [運用コマンド一覧](operations/README.md) |
| [policy/](policy/) | APIMの共通ポリシーや手動追加API用のポリシーXMLをローカル生成 | [render_apim_policy.py](policy/render_apim_policy.py)、[render_apim_portal_policy.py](policy/render_apim_portal_policy.py) |
| [clients/](clients/) | Entra認証、REST・MCP、本人接続、音声の試験と組み込み例 | [動作テスト](../docs/testing/integration-tests.md)、[本人接続クライアント](../docs/reference/identity/connection-session-clients.md) |
| [validation/](validation/) | APIの疎通とWAFの検査、公開WAF manifestの生成 | [coverage_client.py](validation/coverage_client.py)、[waf_probe.py](validation/waf_probe.py)、[waf_contracts.py](validation/waf_contracts.py) |
| [audit/](audit/) | 監査ログの照合・利用量集計用KQL | [監査資料](../docs/reference/audit/request-body-audit.md) |
| [portal_local/](portal_local/) | Portalのローカル認証設定と開発用起動 | [ローカル認証テスト](../docs/testing/portal-local-auth.md) |
| [catalog/](catalog/) | 標準APIとモデル上限の読込・合成 | 配備とローカル検査で共用するPythonモジュール |

ルートの `install_git_hooks.py`、`check_public_data.py`、`check_secrets.py` はGitとCIの検査に使います。 [公開情報の混入検査](../docs/public-data-guard.md)

## 新規構築の順序

1. [事前準備](../docs/setup/01-prerequisites.md)で環境別の保護領域を作り、Entraの登録情報を準備します。
2. [paramsの手順](../docs/setup/07-params.md)に従って入力を検査し、`azd up` で基盤を構築します。`azure.yaml` は `deployment/lifecycle.py` のhookを呼びます。
3. [基盤構築後のEntra登録](../docs/setup/09-egakumi-portal.md)を済ませ、[Gateway](../docs/setup/11-deploy-gateway.md)、[Portal](../docs/setup/12-deploy-portal.md)の順に配備します。両コマンドの既定は計画で、実行時に `--execute` を指定します。
4. [受入確認](../docs/setup/13-verify-deployment.md)で、今回の配備記録、公開ヘルス、代表APIの実接続を確認します。

```sh
python3 -B scripts/deployment/lifecycle.py validate --config "$DEPLOY_PARAMS_FILE"
```

構築後は `python3 -B scripts/operations/release.py --config "$DEPLOY_PARAMS_FILE" --component all` で更新を計画し、`--execute` を付けてGateway、Portalの順に更新します。途中で停止した場合は同じparamsと基盤の配備記録で対象工程を再試行します。撤去は `azd down` で行い、[撤去手順](../docs/operations/teardown.md#撤去する)で所有資源と削除結果を確認します。

## 構築コードを読む

`deployment/lifecycle.py` が基盤の構築工程の順序を管理し、現在の資源の読戻しを `deployment/existing_resources.py`、ARM応答の共通検証を `deployment/arm_response.py` に置きます。 `deployment/helpers/` には入力合成、証明書、イメージ作成、ネットワーク、撤去などの処理があります。GatewayのAPI配備は [API Gateway資材](../apps/api-gateway/deploy/README.md)、Portalアプリと認証用DBの配備は [Portal資材](../apps/portal/deploy/) にあります。

環境別入力、秘密、state、生成物、試験証跡は `.local/<environment>/` の所定の位置に保存します。`.azure/` はazdが管理します。検証用CLIで生成する証跡も同じ保存境界を使います。 [保護領域の規約](../AGENTS.md)

直接実行のCLIはbytecode保存を抑止します。Pythonモジュールを開発時に直接読み込むときは `-B` を使います。テスト本体は [tests/](../tests/) にあります。
