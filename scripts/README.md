# scriptsの用途と配置

このフォルダーには、基盤構築だけでなく、事前準備、API登録、疎通試験、認証、監査ログ確認のスクリプトが含まれます。すべてが`azd up`から実行されるわけではありません。各CLIの直接実行ではPython bytecodeの保存を抑止し、子のPythonにも継承するため、手順の通常コマンドでソース隣へ`__pycache__`を作りません。開発用の`python -m`・`python -c`は`-B`または`PYTHONDONTWRITEBYTECODE=1`を指定します。ユニットテスト本体はリポジトリ直下の`tests/`にあります。

構築後の通常保守は[`operations/`](operations/README.md)を入口にします。既存の配備・WAF処理を再利用し、モデル、Entra secret、証明書、APIキー、監視の計画・対象限定反映と保護記録を揃えています。

## 基盤の構築・更新・撤去

`azure.yaml`の基盤構築・撤去hookは、[deployment/lifecycle.py](deployment/lifecycle.py)を呼びます。`postup`は[deploy_applications.py](deploy_applications.py)を呼び、完成した基盤の同じparams・stateを使ってGateway→Portalを配備します。そこから使う補助処理は、入口と同じ階層にある`helpers/`へまとめています。

```text
scripts/
├── prepare_local_environment.py  # 環境別の保護領域・paramsの初回準備
├── deployment/
│   ├── lifecycle.py              # azd hookの入口
│   └── helpers/                  # 入口から呼び出す補助処理
│       ├── configuration.py
│       ├── catalog_descriptions.py
│       ├── dns_selection.py
│       ├── prepare_secrets.py
│       ├── ssh_keys.py
│       ├── apim_authentication.py
│       ├── credential_login.py
│       ├── apim_preflight.py
│       ├── capacity.py
│       ├── image_build.py
│       ├── credential_image.py
│       ├── portal_image.py
│       ├── powershell_runtime.py
│       ├── set-app-insights-cap.ps1
│       ├── grpc_lifecycle.py
│       ├── cleanup.py
│       └── paths.py
├── deploy_applications.py         # postup: Gateway→Portalの順序制御
├── deploy_gateway.py              # Gatewayの計画・配備
├── deploy_portal.py               # Portalの計画・配備
├── operations/                    # 構築後の通常保守の入口
└── その他の手動コマンド・試験クライアント
```

| 補助処理 | 役割 | 呼ばれる段階 |
| --- | --- | --- |
| `prepare_local_environment.py` | 公開exampleから環境別paramsを作成し、config・secretの相対参照を変換 | 導入先の環境名とprofileの決定後。Azure操作なし |
| `configuration.py` | 設定の読込・合成・入力検査、秘密ファイルの読込 | 各hook。手動の設定検査・API登録等でも共用 |
| `catalog_descriptions.py` | API説明の組み立て | 設定の正規化から呼出し。API登録でも共用 |
| `dns_selection.py` | 準備済みAzure DNS用Subscription・UAMI参照の確認 | `preup` |
| `prepare_secrets.py` | 必要な秘密情報の対話入力・保護保存・再利用 | `preup`・`preprovision` |
| `ssh_keys.py` | 管理VM用SSH鍵の準備 | `preup`・`preprovision` |
| `apim_preflight.py` | APIM名の利用可否確認 | `preprovision` |
| `apim_authentication.py` | 全体認証ポリシーの生成・統合と共通設定の照合 | GatewayのAPI公開処理、手動の設定検査・XML出力で共用 |
| `apim_samples.py` | APIM初期サンプルAPI・製品・対応購読の整理 | `deploy_gateway.py --execute`の登録成功後。業務用定義は保持 |
| `credential_login.py` | CMの保護API・ブラウザー用の一時取引・本人接続セッション用XMLの生成 | API登録・全体ポリシー出力から呼出し |
| `capacity.py` | VM・モデルの提供状況と利用枠の確認 | `preprovision`、手動の`capacity`コマンド |
| `image_build.py` | 管理VM・ACRの照合、構築記録の再利用判定、ビルド結果の検証 | Gateway・Portalのイメージ構築で共用 |
| `credential_image.py`・`portal_image.py` | Gateway・Portalの配布物検査、イメージ構築、一時資源・権限の回収 | `deploy_gateway.py`・`deploy_portal.py` |
| `powershell_runtime.py` | PowerShell実行ファイルとモジュール検索先の選択 | 構築前の依存確認、取込上限の設定 |
| `set-app-insights-cap.ps1` | PowerShell依存確認、Application Insightsの日次取込上限設定 | `preup`・`preprovision` |
| `grpc_lifecycle.py` | gRPC・Realtime用秘密情報の準備、入口設定の確認・更新、撤去後のローカル秘密の削除 | 構築前・構築後・撤去後。API登録でも一部共用 |
| `cleanup.py` | 所有するリソースの照合、モデル・APIM・Gateway／PortalのCA・ジョブ・CAE・管理VM／NICの先行削除、subnet解放、所有5RGと記録したACA管理RGの消失確認 | `predown`・`postdown` |
| `paths.py` | このリポジトリのルート位置の解決 | 補助処理から共用 |

`helpers/`は構築で利用する処理をまとめた配置です。設定検査やAPI登録などから同じ実装を再利用する場合もあり、構築専用のコードを複製しているわけではありません。

`predown`は配備記録と実資源の所有関係を照合し、モデル→APIM→Gateway／Portalのジョブ・認証窓口→PortalのCA→CAE→管理VM／NICの順で削除します。成功したアプリ出力がなくても現在の固定ID・所有タグ・接続先を検証し、全CA／ジョブの404とsubnet解放を待って本体・RG削除へ進みます。CAEのマネージドRGは実際のIDを削除前にstateへ保存し、`postdown`でAzureによる回収後の404を確認します。Portalの処理結果は`.local/<environment>/state/cleanup-predown.json`の`portal`、管理RGの確認結果は`cleanup-postdown.json`の`applicationInfrastructure`へ記録します。

管理VM上で実行する証明書・OS設定のスクリプトは[guest/](../guest/)にあります。Bicepから管理VMへ渡して実行する資材です。

実設定は`.local/<environment>/params.local.json`、秘密は同じ環境の`secrets/`、配備・撤去記録は`state/`、一時生成入力は`generated/`、試験証跡は`evidence/`を使います。`.azure/`はazd専用です。

入口は、[第1章](../docs/setup/01-prerequisites.md#1-5-環境別のローカル領域を準備する)で設定した`DEPLOY_PARAMS_FILE`を使い、リポジトリのルートから次のように実行します。`validate`はローカルの入力検査です。Azureを変更する構築・撤去は通常`azd up`・`azd down`から実行します。

```sh
python3 scripts/deployment/lifecycle.py validate --config "$DEPLOY_PARAMS_FILE"
```

`azd up`には`publishApis:true`が必要です。基盤だけを扱う`azd provision`ではGateway・Portalを実行しません。`deploy_applications.py --config <params>`は両アプリの計画、`--execute`付きは順次配備です。Gateway失敗時はPortalを開始せず、Portal失敗時もup全体を失敗にします。個別コマンドを実行し、今回の終了コードと配備記録で成否を確認します。過去の成功記録だけで工程を省略しません。

設定の検査・出力だけを手動実行するときは、補助処理のCLIも利用できます。

```sh
python3 scripts/deployment/helpers/configuration.py validate --config "$DEPLOY_PARAMS_FILE"
```

## 個別に使うコマンド

Gateway・Portalの配備コマンドは`azd up`からも呼ばれ、個別の更新・再試行にも使えます。それ以外は必要に応じて手動で実行します。

| 用途 | ファイル | 主な実行内容 |
| --- | --- | --- |
| 構築入力・試験定義の準備 | `configure_trial.py`、`prepare_coverage.py`、`prepare_registration.py` | 設定・公開IDやURLの差込み・登録情報をローカルファイルへ保存。Entraアプリの自動作成ではない |
| 前提条件の一覧確認 | `preparation_inventory.py` | ローカル設定の一覧化、指定した場合のみAzure情報の読取 |
| Gatewayの初期配備・更新（`postup`でも使用） | `deploy_gateway.py`（[手順](../docs/setup/credential-sql.md)） | 同じparamsから計画を作成。`--execute`で管理VM上のイメージ作成・非公開ACR登録、credentials DB・CM・実行用ID、SQL初期化、認証窓口・照合ジョブ、API・バックエンド・製品・購読・ポリシーを順に配備。PCのDocker・Actionsは不要 |
| API定義の準備・移行 | `standard_apis.py`、`catalog_publication.py`、`api_migration.py` | `apps/gateway/apim/apis/`で1 API 1ファイルに分けた標準19 APIの展開、カタログ文書、明示したAPI定義の変換を支援する。統合配備の内部処理は`apps/gateway/deploy/`にある |
| Portalの初期配備・更新（`postup`でも使用） | `deploy_portal.py`（[手順](../docs/setup/portal.md)） | 同じparamsから計画を作成。`--execute`で管理VM上のイメージ作成・非公開ACR登録、Portal専用DB作成、手動ジョブでのテーブル・実行用権限の設定、既存Container Appの更新・公開ヘルス確認を実行。構築済みの共通基盤を使うため`azd up`再実行とPCのDockerは不要 |
| Portalのローカル起動 | `run_portal_local.py`（[手順](../docs/testing/portal-local-auth.md)） | 環境別の保護設定を子プロセスだけへ読み込み、`apps/portal`でコマンドを実行。SQL管理作業だけ`--admin`を指定 |
| Portalのローカル認証設定 | `prepare_portal_local_auth.py`（[手順](../docs/testing/portal-local-auth.md)） | 既定は計画のみ。明示実行で環境別の`portal/runtime.env`と`secrets/portal-local-sql-admin.env`を保護保存。Azure設定を変更せず、Azure SQLのデータも取得しない |
| 全体ポリシーの移行準備 | `render_apim_policy.py` | 共通認証を含む全体ポリシーXMLをローカルへ出力。Azureの変更は行わない |
| CM接続の回収 | `cleanup_credential_connections.py`（[手順](../docs/setup/credential-manager-login.md)） | 専用providerの所有接続を照合して回収。セッション失効とCM接続削除を分ける |
| APIの疎通試験 | `integration_client.py`、`coverage_client.py`、`decision_client.py`、`voice_client.py`、`realtime_browser.py` | 構築済みAPIへの要求、応答・通信方式の確認 |
| 監査ログの確認 | `audit_usage.py`、`audit-usage.kql` | ログ結果の検査、照会用クエリ |
| アクセスログの機密情報保護 | `configure_log_privacy.py`（[手順](../docs/testing/log-privacy.md)） | 既定はAzure読取による計画。実行時はprepare→変換確認→export→保存先到達確認→routeの段階で更新 |
| WAF調整と互換性試験 | `waf_policy.py`、`waf_probe.py`（[手順](../docs/testing/waf-compatibility.md)） | 調整資材のローカル計画・明示適用と、公開経路への合成要求による正規データ／攻撃対照の試験を分けて行う |
| 試験クライアントの依存定義 | `auth-requirements.txt`、`integration-requirements.txt` | 認証・疎通試験用Pythonパッケージの一覧 |

`realtime_browser.py`は[tests/browser/realtime.html](../tests/browser/realtime.html)をローカル専用relayから配信します。この画面はWebRTC／Voice Liveの回帰試験資材であり、Portal・Gatewayの配備には含めません。

疎通試験は実APIを呼ぶため、推論等の利用料金が発生する場合があります。API登録の明示実行はAzure設定を変更します。各ファイル先頭の説明とCLIの`--help`で対象と実行条件を確認してください。
