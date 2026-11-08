# 1. 前提要件の準備

[READMEへ戻る](../../README.md) · 次：[2. Entraアプリの準備](02-entra.md)

## このページのゴール

新しい端末・新規クローンから、Entra・DNS担当者へ依頼する情報と、環境別のローカル保存先を揃えます。AzureのRGや有料設備はまだ作成しません。既存の個人用`.tools/`・過去環境のstate・秘密ファイルを前提にしません。

## 1-1. 実行する端末を用意する

macOS・Linux・WindowsのWSLで、POSIX `sh`を実行できる端末を使います。Windowsの通常のPowerShellだけでhookを動かす構成ではありません。インターネットへ接続し、GitHub、Microsoftのログイン／Azure管理API、ツール・PowerShellモジュールの配布元へ到達できる必要があります。

WSLでWindowsドライブ（`/mnt/c`等）にあるcloneを使う場合は、DrvFSの`metadata`を有効にし、`chmod`でディレクトリー700・秘密ファイル600を保存できる状態にします。既存の`.local/<environment>/secrets/`も700にします。`wsl.conf`の変更は対象ディストリビューションの再起動後に反映されます。[WSLのファイル権限](https://learn.microsoft.com/windows/wsl/file-permissions)・[マウント設定](https://learn.microsoft.com/windows/wsl/wsl-config#automount-options)

Linux用スクリプトは`.gitattributes`でLFを指定しています。Windowsで編集してCRLFになった場合も、VMへ埋め込むshell・PowerShellの本文はBicep側でLFへ正規化します。

| ツール | 用途・条件 | 確認コマンド | 導入案内 |
| --- | --- | --- | --- |
| Git | クローン・版の確認 | `git --version` | [Git](https://git-scm.com/downloads) |
| Python | 3.11以上。通常のup/downには試験用package不要 | `python3 --version` | [Python](https://www.python.org/downloads/) |
| Azure CLI | Azure管理操作 | `az version` | [Microsoft](https://learn.microsoft.com/cli/azure/install-azure-cli) |
| Azure Developer CLI | 1.33以上。up/down・環境管理 | `azd version` | [Microsoft](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) |
| Bicep CLI | 名前計算・テンプレートのコンパイル | `az bicep version` | [Microsoft](https://learn.microsoft.com/azure/azure-resource-manager/bicep/install) |
| PowerShell | 7.2以上。監視設定に使用 | `pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'` | [Microsoft](https://learn.microsoft.com/powershell/scripting/install/installing-powershell) |
| OpenSSH | SSH鍵の生成 | `command -v ssh-keygen` | OSのOpenSSHを導入 |

GatewayとPortalのコンテナーイメージは、配備スクリプトが構築済みの管理VM上で作成し、非公開ACRへ登録します。構築端末にNode.js・DockerやSQLへの直接接続は不要です。Portalをローカル開発する場合のNode.jsと試験環境は[開発手順](../../DEVELOPMENT.md#テストの実行方法)で確認します。

Bicepが未導入なら、Azure CLIのBicep CLIを導入します。この操作はAzure設備を作成しません。

```sh
az bicep install
export BICEP_CLI="${AZURE_CONFIG_DIR:-$HOME/.azure}/bin/bicep"
"$BICEP_CLI" --version
```

別の場所へBicep CLIを導入した場合は、`BICEP_CLI`にその実行ファイルのパスを設定します。各コマンドが同じ版を使うことを確認してください。

## 1-2. リポジトリをクローンする

本リポジトリの「Code」でclone URLを取得します。フォークを利用する場合はそのURLを選びます。

下記の`<...>`を実値へ置き換えてから実行してください。保存先は任意の空ディレクトリです。

```sh
DEPLOY_REPOSITORY_URL='<利用するリポジトリのclone URL>'
DEPLOY_CLONE_DIRECTORY='<任意のクローン先ディレクトリ>'

git clone "$DEPLOY_REPOSITORY_URL" "$DEPLOY_CLONE_DIRECTORY"
cd "$DEPLOY_CLONE_DIRECTORY"
git status --short --branch
```

以後のコマンドは、このクローンの**リポジトリルート**（`azure.yaml`があるフォルダ）で実行します。利用するブランチ／リリースは導入担当者が決め、構築・撤去が終わるまで同じ版を保持します。

PowerShellの依存モジュールを端末の利用者へ導入し、ローカル検査します。

```sh
pwsh -NoProfile -Command 'Install-Module -Name Az.Accounts,Az.ApplicationInsights -Scope CurrentUser -Repository PSGallery -Force'
pwsh -NoProfile -File scripts/deployment/helpers/set-app-insights-cap.ps1 -CheckDependencies
```

企業がPowerShell Galleryを制限している場合は、承認済みの配布方法で同じモジュールを導入します。個人の端末キャッシュをコピーする前提にはしません。

## 1-3. Azure・Entra・DNSの担当と権限を確認する

| 担当 | 必要な準備 |
| --- | --- |
| Azure構築担当 | 対象SubscriptionのOwner相当のAzure RBAC。RG／設備、RBAC割当、RP登録、対象Key Vault／Foundry／APIMの削除・purgeを許可できること |
| Entra担当 | アプリ登録・設定と、採用する委任権限のテナント全体同意を実施できること。開発者のEntra管理ロール保有は前提にしない |
| DNS担当 | 公開ゾーンと、DNS-01のTXT更新に使う資格情報を準備できること。Gateway・PortalのAレコードを管理する担当も決める |
| ネットワーク担当 | IP重複・外向き通信・組織のAzure Policy／閉域化要件を確認すること |

AzureのOwner権限だけでEntraのアプリ作成・管理者同意ができるとは限りません。標準の初期配置はClaudeを含まず、Claudeの購入許可や規約同意を準備する必要はありません。ロックや監査保持の解除権限としてOwnerを扱いません。

`management.encryptionAtHost=true`の場合、Coreは通常のRP登録に加えて`Microsoft.Compute/EncryptionAtHost`を自動登録します。機能が既に登録済みなら再登録せず、登録中なら完了を待ちます。その後、Compute RPを再登録して反映を確認します。前回の中断後やポータルでの登録後も反映確認を省かないため、Compute RPの再登録は機能登録済みの場合も実行します。構築担当には機能・RP登録の権限が必要です。ポータルでの事前登録は必須ではありません。

## 1-4. 導入先の値を決める

後のページでは次の変数を使用します。**すべて自社の実値に置き換えます。** 製品名と環境名は資源名に使うため、作成後に変更して同じ環境を更新することはできません。

```sh
export DEPLOY_SUBSCRIPTION_ID='<構築先Subscription ID>'
export DEPLOY_AZURE_TENANT_ID='<そのSubscriptionのTenant ID>'
# 認証先が同じならAzure用IDを使い、別なら認証用テナントのIDへ置換します。
export DEPLOY_ENTRA_TENANT_ID="$DEPLOY_AZURE_TENANT_ID"
export DEPLOY_PRODUCT='<資源名に使う製品名>'
export DEPLOY_ENVIRONMENT='<資源名とazd環境に使う環境名>'
export DEPLOY_LOCATION='<ネットワーク等の配置リージョン識別子>'
export DEPLOY_FOUNDRY_LOCATION='<選択モデルを提供するFoundryリージョン識別子>'
export DEPLOY_API_CENTER_LOCATION='<API Centerの配置リージョン識別子>'
export DEPLOY_GATEWAY_FQDN='<Gatewayの公開FQDN。httpsやパスを含めない>'
export DEPLOY_PORTAL_FQDN='<Portalの公開FQDN。httpsやパスを含めない>'
```

| 決める値 | 確認すること |
| --- | --- |
| 製品名・環境名 | 英小文字開始、英小文字・数字・内部ハイフン。製品名24字以下・環境名12字以下に加え、共通部分`<environment>-<product>`は17字以下。`ca-<stem>-credentials`の32字上限に合わせ、Key Vault・Storage等の名前も同時に収める |
| リージョン | ネットワークとFoundryは別リージョンを指定可能。SKU・モデルの提供・クォータ・組織の配置制約を確認 |
| 公開FQDN・公開ゾーン | GatewayとPortalの公開名を決め、両方を証明書SAN・公開DNS・EntraのWeb戻り先へ対応付ける |
| IP計画 | VNetの/24×4と各サブネットを、既存VNet・オンプレミス・接続予定先と重複しない範囲へ決める。公開例を使用可能とは推測しない |
| 通知・証明書の連絡先 | APIM発行者、Azure Monitor通知、ACME連絡先を決める |
| 利用範囲 | 通常のGateway・Portal一括構築か、企業接続を挟む段階構築かを決める。採用するモデル・APIと、利用者向けの購読キー・本人接続、下流向けのMIの役割を確認する |

資源名は`apim-<environment>-<product>`等の固定規則で導出します。世界で一意の名前が必要な資源では、既存利用と競合しない意味のある製品名・環境名を組織内で選びます。ランダムな末尾やAPIMだけの別名を自動追加しません。

ここで決めた非秘密の値は、次節で用意する環境別の`evidence/`内の設定表へ控え、[4. params](04-params.md)にも同じ値を記入します。シェル変数はターミナルを閉じると消えるため、別の日や別ターミナルで続ける場合は自社の設定表から再設定します。secretの値をこの変数群へ追加しません。

## 1-5. 環境別のローカル領域を準備する

公開資材はこの1つのcheckoutを正本として使い、実情報はリポジトリルートの`.local/`とazd専用の`.azure/`だけへ保存します。[共通ルール](../../AGENTS.md)に従い、この2領域はGit・公開文書・配布物へ含めません。ルートの`secrets/`・`.state/`、`params/`内の実params、アプリ直下の`.env.local`へ実情報を作らないでください。

初回は、標準構成を`test`または`production`から1つ選んで準備します。次のコマンドはAzure資源を作成せず、公開exampleのconfig・secret参照を環境別保存先へ合わせます。

```sh
umask 077
export DEPLOY_PROFILE='test' # 本番向け標準構成を選ぶ場合は production
python3 scripts/prepare_local_environment.py \
  --environment "$DEPLOY_ENVIRONMENT" --profile "$DEPLOY_PROFILE"
export DEPLOY_LOCAL_DIRECTORY="$(pwd)/.local/${DEPLOY_ENVIRONMENT}"
export DEPLOY_PARAMS_FILE="$DEPLOY_LOCAL_DIRECTORY/params.local.json"
export WORKFORCE_CONFIG="$DEPLOY_PARAMS_FILE"
```

| 保存先 | 用途 |
| --- | --- |
| `.local/<environment>/params.local.json` | 導入先のID・公開名・IP・グループ等の実設定 |
| `.local/<environment>/secrets/` | DNS・Entraの秘密、SSH鍵、採用時のPFX等 |
| `.local/<environment>/state/` | 容量検査、配備・ビルド・撤去の記録 |
| `.local/<environment>/generated/` | azdへ渡す一時生成入力。`main.parameters.json`は秘密を含み得るため保護する |
| `.local/<environment>/evidence/` | 設定表、試験結果、保存後の画面証跡 |
| `.local/<environment>/portal/` | ローカルPortal開発を採用するときのサーバー専用設定 |
| `.azure/` | azdが管理する環境選択・配備先等。手動の証跡置場にはしない |

公開資材の`infra/main.bicepparam`が、環境別の生成JSONをazdへ渡します。構築前処理がazd環境へ保存する`EGAKUMI_ARM_PARAMETERS_FILE`は、`.local/<environment>/generated/main.parameters.json`という非秘密の相対パスだけです。client secret・DNS token・PFX等の秘密値はazdの`.env`へ設定せず、保護された生成JSONから配備パラメーターとして渡します。構築前にBicepが生成JSONを正しく読み取れることを、Azureへ変更を加える前に検査します。

同じ環境を再開するときは、このparamsと記録を保持し、別profileの公開例で上書きしません。環境名・params・azd環境・Tenant／Subscription・stateの対応を照合し、同じcheckoutで構築・更新・撤去を同時実行しません。`.gitignore`は通常の追加を避ける補助であり、強制追加やフォルダー全体の共有を許可するものではありません。

## 次へ進む前の確認

- cloneしたルートに`azure.yaml`、`infra/`、`params/`、`config/`がある。
- 環境別の`.local/<environment>/`を準備し、実paramsと証跡の保存先を確認した。
- 上記ツールとPowerShellの依存検査が成功する。
- Azure／Entra／DNSの担当・権限、公開名とIP計画が決まっている。
- 選択したモデルの料金・地域提供・クォータと、組織の購入条件を確認する担当がいる。

次：[2. Entraアプリの準備](02-entra.md)
