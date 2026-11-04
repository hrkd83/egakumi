# 開発ルール・保守手順

本書はEgakumi CoreのIaC、Gateway・Portal、配備・運用スクリプトを変更する担当者向けに、コーディング規約、コメント、テストの共通ルールと実行方法、保守用の開発手順をまとめたものです。

作業開始前に[リポジトリ共通ルール](AGENTS.md)を確認します。本プロジェクトはWIPです。公開資材は1つのcheckoutを正本とし、実設定・秘密・実行記録はルートの`.local/<environment>/`、azd専用情報は`.azure/`へ保護保存します。実情報をGit管理する資材・文書・履歴へ含めません。この2領域以外のignore対象は実情報の保存先になりません。以下の記録指示もこの境界に従います。

| 文書 | 管理する内容 |
| --- | --- |
| [README](README.md) | 導入手順と構成、構築・運用資料への入口 |
| 本書 | 開発・コーディング・テストの共通ルール、ローカル実行方法、開発用の補助手順 |
| [動作テスト仕様書](docs/testing/integration-tests.md) | 構築後の動作テストの対象・手順・受入条件・判定・証跡 |
| [構築テスト仕様書](docs/testing/construction-tests.md) | 構築資材のテストの対象・手順・受入条件・判定・証跡 |

公開する受入条件と架空値による結果例は各テスト仕様書を正本とし、本書には再掲しません。実環境の結果・対象・証跡は環境別のGit管理外領域`.local/<environment>/`で管理します。動作テストと資材改修・再試験の完了後に、確定した資材で再構築して構築テストを別試行で行います。具体的な実施順・開始条件は各仕様書を参照してください。

## 目次

- [コーディング規約](#コーディング規約)
- [コメントの共通ルール](#コメントの共通ルール)
- [テストの共通ルール](#テストの共通ルール)
- [テストの実行方法](#テストの実行方法)
- [構築前チェックの責務](#構築前チェックの責務)
- [ソースを読む順番](#ソースを読む順番)
- [設定生成と独立した回帰確認](#設定生成と独立した回帰確認)
- [音声・モデルのCLI確認](#音声モデルのcli確認)
- [固定依存と配布物](#固定依存と配布物)

## コーディング規約

新規実装と変更箇所に以下を適用します。既存の公開名・API契約・設定・保存データとの互換性を保ち、規約のための一括整形や改名は機能変更と分けます。書式の例外が必要な場合は、周辺コードとの整合と理由を変更説明へ残します。Portal固有のルールは [apps/portal/AGENTS.md](apps/portal/AGENTS.md)も参照してください。

Coreの資源名は`<環境>-<製品>`を共通部分とし、ランダム末尾を付けません。APIM名は`apim-<環境>-<製品>`です。名前計算は`prepare_registration.py`とBicepで一致させ、APIMだけの個別名指定を追加しません。[主要名と長さ](README.md#作成される構成)を確認します。

### Python

- [PEP 8](https://peps.python.org/pep-0008/)に従います。インデントはスペース4つ、関数・変数・モジュールは`snake_case`、クラスは`PascalCase`、定数は`UPPER_SNAKE_CASE`を使います。
- importは標準ライブラリー、外部依存、プロジェクト内の順に分け、ワイルドカードimportを避けます。公開関数やモジュール境界には引数・戻り値の型を記述します。
- 原則としてコードは79文字、コメント・docstringは72文字を目安に改行します。長いURL・外部契約の文字列などは可読性を優先し、意味を壊して分割しません。
- 公開モジュール・クラス・関数・メソッドには [PEP 257](https://peps.python.org/pep-0257/)に従う`"""`のdocstringを付けます。先頭に要約を書き、詳細は空行を挟みます。入力・戻り値・例外・副作用・実行前提のうち利用者に必要な情報を日本語で説明します。
- 例外は復旧・安全なエラー変換ができる場所で捕捉します。失敗を握りつぶして成功扱いにせず、外部コマンドの終了コードとタイムアウトを扱います。

### TypeScript・TSX・JavaScript

- Portalのアプリ・テストはTypeScriptを使い、コンポーネントは`.tsx`、その他は`.ts`に置きます。既存の`.mjs`等の実行・設定ファイルはその形式を維持します。
- インデントはスペース2つ、文字列はダブルクォート、文末はセミコロンを基本とします。関数・変数は`camelCase`、型・クラス・Reactコンポーネントは`PascalCase`、共有する固定定数は`UPPER_SNAKE_CASE`を使います。
- [PortalのESLint設定](apps/portal/eslint.config.mjs)とTypeScriptの型検査に従います。外部入力は`unknown`等で受けて検証し、型チェックを避けるための`any`や無根拠な型アサーションを追加しません。
- 公開する関数・コンポーネント・型と、前提や副作用のある内部処理には`/** ... */`のTSDocコメントを付けます。要約は日本語で書き、型情報はTypeScript宣言で表します。
- 引数には [`@param 名前 - 説明`](https://tsdoc.org/pages/tags/param/)、戻り値には [`@returns`](https://tsdoc.org/pages/tags/returns/)を必要に応じて使います。非同期処理ではPromiseが解決する値を説明します。制約・設計上の理由は [`@remarks`](https://tsdoc.org/pages/tags/remarks/)、呼出し側が扱う例外や使用例は`@throws`・`@example`で補足します。型宣言の逐語的な繰り返しだけのタグは増やしません。
- 既存JavaScriptの公開処理にはJSDocを使います。TypeScriptの新規実装をJavaScriptへ置き換えません。

### その他の言語・設定

| 対象 | 書式・命名・コメントのルール |
| --- | --- |
| PowerShell | インデントはスペース4つ。関数は承認済み動詞による`Verb-Noun`、引数は`PascalCase`を使います。公開スクリプト・関数は[コメントベースヘルプ](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_comment_based_help)で`.SYNOPSIS`・`.DESCRIPTION`・`.PARAMETER`・`.EXAMPLE`等を記述し、必要権限・変更対象・失敗時の状態を説明します。 |
| Bash・shell | shebangと対応シェルを明示し、インデントは既存ファイルのスペースに揃えます。変数展開を引用し、配列で引数境界を保ち、終了コードと一時ファイルの回収を扱います。[Shell Style Guide](https://google.github.io/styleguide/shellguide.html)のコメント構成を参考に、冒頭で目的・実行環境、関数前で引数・出力・終了状態・副作用を説明します。 |
| Bicep・bicepparam | [Bicepの推奨事項](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/best-practices)を基準とし、インデントはスペース2つ、シンボルは`camelCase`を使います。公開パラメーターは`@description`で用途・単位・範囲を示し、機密入力には`@secure`を使います。複雑な依存・保持条件は`//`で説明します。Azure資源名は本書のCore命名規則に従います。 |
| APIM policy XML・埋込みC#式 | インデントは周辺XMLに合わせ、ポリシーの適用順・継承・失敗時の応答を追える構成にします。XMLコメントでポリシーの目的・認可境界・順序の理由を説明し、C#式は処理を短く分け、外部値を検証してから使います。生成処理を変えたときは生成後のXML・式も検証します。 |
| SQL ServerのSQL | 既存schema・表・列・procedureの命名を維持し、SQLキーワードは大文字、処理単位を改行して記述します。migration冒頭の`--`コメントで目的・適用順・データへの影響を説明します。値はパラメーターで渡し、更新のトランザクション境界・権限・再実行時の扱いを明示します。 |
| KQL | `let`で定義する値や集計の目的が分かる名前を使い、パイプラインを改行します。`//`コメントで対象期間・出典・相関キー・欠測や重複の扱いを説明します。 |
| JSON・YAML | インデントはスペース2つ。JSONの説明はSchemaの`description`や対応READMEへ、YAMLの補足は`#`へ記述します。環境固有値と標準値を分け、JSONへ独自のコメントキーを追加しません。 |
| HTML・CSS | HTMLは意味に合う要素・ラベル・アクセシビリティを保ち、CSSは既存のクラス・変数・トークンの命名を使います。HTMLの`<!-- ... -->`、CSSの`/* ... */`は構造・互換性対処・見た目だけでは分からない制約の説明に使います。 |

## コメントの共通ルール

- プロジェクトの説明コメントは日本語を基本とし、識別子・公開エラーコード・公式用語はその表記を保ちます。外部資料や生成コードの原文は必要に応じて維持します。
- 読み手が目的、前提、入力・出力、副作用、権限境界、設計上の理由を把握できる内容にします。自明なコードを逐語訳するコメントや、履歴を並べるコメントは避けます。
- 処理や契約を変更したらコメント・help・Schema説明・公開ガイドも同時に更新します。公開説明と内部実装上の補足を分けます。
- TODOには未解決の内容と対応条件またはissueへの参照を残します。秘密値、個人情報、実環境のキー・token・証明書・認証URLをコメントや使用例へ記録しません。

## テストの共通ルール

- 公開入力と結果、権限境界、副作用、失敗時の状態を検証します。実装の改行・変数名・説明文だけに依存する検査は追加しません。
- カタログは名前の重複、容量、参照先、公開操作との整合を検証します。廃止済みの名前や以前の値の禁止リストを維持しません。
- 同じ契約の正常・異常・境界は表形式や `subTest` でまとめ、共通入力はfixtureへ置きます。別のテストクラスをfixture代わりにしません。
- 認可、秘密非表示、所有確認、削除保護、並行実行の競合、再実行時のデータ保持は維持します。ソース照合も、配布物や秘密公開の境界を直接守るものは残します。
- 純粋なロジック、HTTP、SQL、実Azureの検証を分けます。同じBicepソースのコンパイルは共有し、各テストには独立した結果を渡します。
- 試験データ・資源は生成した識別子で隔離し、所有した範囲だけを後片付けします。

protocol中継の回帰試験は[tests/fixtures/external-api-definitions.json](tests/fixtures/external-api-definitions.json)の合成入力を使い、Coreのローカル試験として実行します。

変更内容に応じてローカル試験・型検査・lint・ビルドを実行し、不具合修正には再現条件を検証する回帰テストを追加・更新します。書式や説明文だけの変更は差分・参照先・整合性を確認します。実行した検査、失敗・スキップと理由、残る確認事項を変更説明へ記録します。

試験対象は実施revisionの`config/`、`params/`、`apps/gateway/apim/apis/`から決めます。モデル名・版・件数を共通手順へ複写しません。試験実行と不具合修正・配備を区別し、ローカル成功を実環境での受入完了に読み替えません。

実環境の試験では以下を共通ルールとします。各試験の対象・具体的な受入条件・実行順は2つの仕様書に従います。

- 試行開始前に、対象の仕様書・結果票を環境別のGit管理外領域`.local/<environment>/`へ保存します。対象環境・入力参照・相関ID・実測値を含む記録はその環境内で管理し、配備記録は同じ環境の`state/`、試験結果と生証跡は`evidence/`へ分けます。別環境・別試行の結果は分けます。
- 項目ごとの実施直後に、非公開側の仕様書・詳細記録へ判定・確認済み条件・未確認条件・証跡を記録します。公開側へは、元環境に結び付かない一般化した修正内容・架空値での再現条件だけを反映します。
- 全期待条件を実測した項目だけを`PASS`とします。不具合を修正しただけでは合格にせず、修正後の再試験結果を記録します。判定区分は各仕様書に従います。
- 採用しない構成や利用者指定の除外は理由を記録します。外部条件不足、試験中断、未実施を成功扱いにしません。
- 生証跡と実環境の相関ID・参照先は、環境別の保護した`.local/<environment>/evidence/`内だけで管理します。公開する仕様書・作業記録に、企業固有情報、禁止対象の実値を元にしたハッシュ、秘密値、個人情報、実環境の要求／応答本文を貼り付けません。
- 実Azureの更新・課金・障害注入・撤去は対象環境で合意した範囲に限定し、専用データと復元条件を用意します。一時権限・試験データ・一時設定の後片付けと、残る資源・保持・費用も記録します。

## テストの実行方法

### ローカル確認

勉強会の制作資材・出力は`docs/study/`にローカルで保管し、Git管理には含めません。公開する動画一覧は[勉強会動画](docs/videos.md)で管理します。制作記録も企業・実環境固有情報を除去したものだけを配置します。

clone後は、先に`python3 -I -B scripts/install_git_hooks.py`でコミット前・push前の混入検査を有効にします。検査内容・例外管理・停止時の扱いは[公開情報の混入検査](docs/public-data-guard.md)を参照してください。作業ツリー検査はignore対象の生成物も含むため、この公開チェックアウトへ依存パッケージや実行記録を生成する前に配置先を確認します。

Python 3.11以上と [端末の準備条件](docs/setup/01-prerequisites.md) を使います。Azureへの変更、実際のサインイン、推論は行いません。

```sh
# リポジトリのルートで実行します。
python3 -m venv .venv
.venv/bin/python -m pip install -r scripts/integration-requirements.txt -r apps/gateway/credential-broker/requirements.txt
.venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=apps/gateway/credential-broker .venv/bin/python -m unittest discover -s apps/gateway/credential-broker/tests -v
```

Windowsでは仮想環境の `Scripts/python.exe` を使い、環境変数は利用するシェルの書式で指定します。

| 対象 | 検証すること |
| --- | --- |
| 設定 | Schema、相対パス・合成、型・範囲、モデルとAPIの参照整合、秘密参照、ネットワーク境界 |
| 構築・公開 | 計画モード、所有環境の照合、失敗時の停止、既存設定・データの保持、一時資源・権限の回収 |
| APIM | 生成したARM／XML、認可、入力・出力制約、下流への書換え、各プロトコルの契約、監査の相関 |
| 認証窓口 | 取引とセッションの所有者・期限・一度限りの利用、Cookie、CM接続、SQL競合・利用枠 |
| Portal | 認証、テナント・グループ・所有者の認可、APIキー管理、購読同期、モデル応答とstream |

以下の出力を保存する場合は、[第1章](docs/setup/01-prerequisites.md#1-5-環境別のローカル領域を準備する)で`DEPLOY_ENVIRONMENT`・`DEPLOY_LOCAL_DIRECTORY`・`DEPLOY_PARAMS_FILE`を設定します。架空データだけの確認でも保存先を環境別領域へ揃えます。

PowerShellを使う試験は `WORKFORCE_PWSH`、インストール済み `pwsh`、対応するportable版から実行環境を選びます。Bicepの実行ファイルは `BICEP_CLI` で指定できます。依存不足でスキップした項目は理由とともに記録します。

```sh
mkdir -p "$DEPLOY_LOCAL_DIRECTORY/generated"
az bicep build --file infra/main.bicep --outfile "$DEPLOY_LOCAL_DIRECTORY/generated/main.compiled.json"
export WORKFORCE_PWSH="$(.venv/bin/python -c 'from scripts.deployment.helpers.powershell_runtime import locate_powershell; print(locate_powershell())')"
"$WORKFORCE_PWSH" -NoProfile -File tests/certificate-offline.ps1
.venv/bin/python tests/cap-offline.py
```

証明書とcapのオフライン試験は合成入力・偽モジュールを使います。実際のDNS、ACME、Azureへ接続しません。生XMLの追加Razor検査には、公式の `System.Web.Razor.dll` を `WORKFORCE_RAZOR_DLL` で指定します。

PortalはNode.js 24を用意し、`apps/portal` で実行します。

```sh
npm ci
npm test
npm run lint
npx tsc --noEmit
npm run build
```

SQL統合試験は通常の `npm test` ではスキップします。[ローカルSQLの準備](docs/testing/portal-local-auth.md) を済ませ、専用ローカルSQL環境で `PORTAL_SQL_INTEGRATION_TESTS=1` と試験用資格情報を指定して実行します。管理者には試験専用DBの作成権限、権限検証には `PORTAL_SQL_RUNTIME_USER`／`PORTAL_SQL_RUNTIME_PASSWORD` の事前作成済みログインが必要です。各試験は独立したDBを作成し、終了時にそのDBだけを削除します。

### 実環境の試験

構築済み環境の動作確認は [動作テスト仕様書](docs/testing/integration-tests.md)、資材の構築・更新・撤去の確認は [構築テスト仕様書](docs/testing/construction-tests.md)の手順・結果票を使います。共通の実行準備を終えたら、該当する仕様書で対象構成を展開し、項目ごとに実測と証跡を記録します。構築と個別配備のコマンドは [README](README.md)、コマンドごとの役割は [スクリプト一覧](scripts/README.md)を参照してください。

## 構築前チェックの責務

`python3 scripts/deployment/lifecycle.py validate --config <params>` はローカルの設定検証です。`preup`／`preprovision` は、必要な依存、DNS資格情報、Azure登録・権限・容量、APIM名の利用可否を構築直前に確認します。詳細は [スクリプト一覧](scripts/README.md) を参照してください。

イメージの配備先、ビルド結果、再利用するdigest・ソースhash・一時資源の回収記録はGatewayとPortalで共通の検査を使います。サービス固有の配布物allowlistと秘密・symlinkの拒否は、それぞれの配布境界で確認します。実資源の状態は変わるため、構築直前の確認はローカルテストとは別に必要です。

## ソースを読む順番

| 順番 | 入口 | 分かること |
| --- | --- | --- |
| 1 | [params](params/test.example.json)・[config](config/test.json) | 自社の値と標準値。JSONの説明はSchemaとREADMEに記載 |
| 2 | [azure.yaml](azure.yaml) | Coreのup/downのhookと処理順序 |
| 3 | [configuration.py](scripts/deployment/helpers/configuration.py)・[lifecycle.py](scripts/deployment/lifecycle.py) | 合成、入力・秘密参照検査、事前確認、構築後の設定 |
| 4 | [main.bicep](infra/main.bicep)・[modules](infra/modules) | 5RG、ネットワーク、ID、サービスの依存関係 |
| 5 | [certificate-bootstrap.sh](guest/certificate-bootstrap.sh)・[certificate.ps1](guest/certificate.ps1)・[configure-audit.sh](guest/configure-audit.sh) | Ubuntu初期化、証明書取得・更新・復旧、OS監視 |
| 6 | [deploy_applications.py](scripts/deploy_applications.py) | 基盤構築後のGateway→Portalの順次実行と、途中失敗時の停止 |
| 7 | [Gateway配備資材](apps/gateway/)・[deploy_gateway.py](scripts/deploy_gateway.py)・[deploy_portal.py](scripts/deploy_portal.py) | 各アプリのDB・実行用ID・ジョブ・イメージと、API・製品・検証用購読の配備 |
| 8 | [cleanup.py](scripts/deployment/helpers/cleanup.py) | モデル・APIM先削除、削除保護と所有照合 |
| 9 | [tests](tests)・[Portalテスト](apps/portal/tests/) | ローカルで検査する契約と回帰テスト。実環境の受入条件は冒頭の2仕様書を参照 |

Pythonのdocstring、PowerShellのhelp、Bicepの入力説明、policy XMLとshellのコメントから役割・順序・保証範囲を追えます。秘密値や運用中の証明書をコメント・公開例へ転記しません。

`scripts/`には構築以外の手動コマンドや疎通試験クライアントも含まれます。基盤のhookは`deployment/lifecycle.py`、up完了前のアプリ配備は`deploy_applications.py`が担当し、補助処理は`deployment/helpers/`にまとめています。[用途別の一覧](scripts/README.md)で、構築時に使う処理とAPI登録・試験・運用で使う処理を確認できます。

`infra/`は共通基盤、`apps/gateway/`と`apps/portal/`はその上で動くアプリの配備単位です。初期の`azd up`はAPIM本体、共通SQLサーバー・SQL初期化用ID、ネットワーク・Private Endpoint、ACA環境・ACR、共有Entra secretを用意し、`postprovision`で実出力と入口設定を仕上げます。その後、対話可能な`postup`から`deploy_applications.py --execute`を実行します。GatewayのSQL初期化・起動確認・APIと製品の登録に成功してから、Portalの認証DB・検証用購読・イメージと本体を配備します。

`azd provision`は基盤だけを対象にします。全体の`azd up`には`publishApis:true`が必要で、falseなら`preup`の冒頭で課金資源の作成前に停止します。既存のlocal paramsを自動変更しません。標準APIの空配列はメモリー内で解決し、基盤に必要なWAF・監査ログの情報も同じ定義から準備します。環境別endpointを解決したAPI配列をparamsへ書き戻しません。

構築済み環境の更新・途中失敗からの再開では、同じparamsとCoreの`.local/<environment>/state/latest.json`を使ってGateway／Portalの個別コマンドを実行できます。アプリの更新だけで基盤の再構築は不要です。既存CLIを別プロセスで呼び、今回の終了コードで後続工程へ進むため、以前の成功記録を今回の成功と扱いません。Portalの初期グループ設定は最後の対話工程で同じparamsへ保存します。非対話なら`portalApiKeys.groups`・`platformGroupId`・`groupNamePattern`を事前に設定します。

APIM全体ポリシーは[service.xml](apps/gateway/apim/policies/service.xml)、各APIのXMLは`apps/gateway/apim/policies/`、標準APIのJSONは[apps/gateway/apim/apis/](apps/gateway/apim/apis/)にあります。API定義は`<API名>.json`の1 API 1ファイルで管理し、公開パス・操作・認可・下流接続の変更を対象APIのファイル内で確認できる構成です。モデルの配備・SKU・容量は`config/test.json`または`config/production.json`、CMプロバイダーと環境固有値はparamsで管理します。`apps/gateway/apim/api-publication.bicep`と`apps/gateway/deploy/api_publication.py`が登録を担当し、`apps/gateway/deploy/broker_deployment.py`がアプリ配備を担当します。この2つは統合コマンドから利用する内部実装です。

購読キーによる共通認可は全体ポリシーに集約し、`gateway.authentication.subscriptionKeyHeader`（既定`api-key`）とAPIM標準の購読検証を使います。API側は`<base />`で継承し、モデル・入力・出力等の操作制約と下流認証を設定します。API登録は子資源の配備成功を確認した後、ETag付きで共通ポリシーを統合・読戻し確認します。

`gateway.authentication.credentialLogin`を設定すると、[CM共通接続窓口](docs/setup/credential-manager-login.md)を追加します。`/auth/connections`のstart/status/logoutは購読必須、`/auth/connections/browser`のlaunch/callbackはブラウザー用の一時取引で保護します。本人セッションを必要とするAPIだけがCMを利用します。キーはサーバー側へ保管し、ブラウザーのJavaScriptへ配布しません。

DNS事業者の選択は`scripts/deployment/helpers/dns_selection.py`、秘密の案内・非表示入力・保護保存は`scripts/deployment/helpers/prepare_secrets.py`が担当します。`preup`はローカル依存を検査してから選択結果を元のparamsへ保存し、再読み込みしてFirewallの接続先を計算します。`preprovision`は保存済みの値を使うため、同じupの中でDNS選択を重複させません。標準の公開例を直接書き換えず、導入先paramsを使います。秘密の値はparams/configへ書き込みません。

## 設定生成と独立した回帰確認

通常の構築・更新は[準備コマンド](docs/setup/01-prerequisites.md#1-5-環境別のローカル領域を準備する)で公開例から作成した環境別paramsと、その参照先configを使います。`apiDefinitions: []`は [標準API定義](apps/gateway/apim/apis/)を読み、Gateway配備時にCoreの実outputsから接続先を解決します。標準導入で回帰用の合成設定やbindingsを別に作る必要はありません。

設定を変更するときは [params](docs/setup/04-params.md)・[config](docs/setup/05-config.md)・Schemaを揃え、[ローカル確認](#ローカル確認)を実行します。配備・API公開・撤去では同じ正本設定を使います。

以下は目的を限定した開発用補助です。公開用の入力・結果例は、実値から独立した架空データだけに限定します。実環境を扱う入力・出力は環境別の`.local/<environment>/`に限定します。ルートの`.state/`・`secrets/`等へ実情報を保存しません。通常の導入手順と分けます。

| 補助CLI | 用途と範囲 |
| --- | --- |
| [configure_trial.py](scripts/configure_trial.py) | 既存のCM設定と標準定義から、テキスト・PII・Graphの試験用合成設定を作成する。秘密値を読み取らず、Azureへ接続しない。出力では`publishApis:true`になるため、作成だけと配備を区別する |
| [prepare_coverage.py](scripts/prepare_coverage.py) | API定義と非秘密のbindingsを合成する独立回帰用の補助。未解決参照・モデル矛盾・APIの上書きを検査し、出力の`publishApis`は`false`にする。Azure変更や推論は行わない |
| [integration_client.py](scripts/integration_client.py)・[coverage_client.py](scripts/coverage_client.py) | Gateway直接呼出し、負例、接続・監査等の確認。実要求を送るコマンドは接続先と費用・状態変更の範囲を確認して使う |

```sh
python3 scripts/configure_trial.py --help
python3 scripts/prepare_coverage.py --help
python3 scripts/integration_client.py --help
python3 scripts/coverage_client.py run --help
```

補助CLIの入力・出力・実行条件は [スクリプト一覧](scripts/README.md)を参照してください。実環境の設定・秘密ファイル・参照先は、環境別のGit管理外領域`.local/<environment>/`内で管理します。token・キー・実環境固有値を公開側の引数例・ログ・作業記録へ直接書きません。

APIの公開パス・認可・操作を変更したら、GatewayとPortalの呼出し処理・カタログ・ガイドを整合させます。正常系の画面操作はPortalで確認し、Gatewayの直接実行結果と分けます。本人接続の仮セッション保存・本人照合・再実行は [共通クライアント契約](docs/reference/connection-session-clients.md)に従います。

## 音声・モデルのCLI確認

`coverage_client.py prepare --directory "$DEPLOY_LOCAL_DIRECTORY/evidence/preparation/assets"`は合成PNG／PDF／無音WAVを生成します。STT正常系には既知の発話を含む合成WAVを使います。Speech REST TTSの16kHz WAVは16kHz STTへ渡し、WSS用には変換します。

```sh
python3 scripts/coverage_client.py convert-audio \
  --input "$DEPLOY_LOCAL_DIRECTORY/evidence/tts-16k.wav" --output "$DEPLOY_LOCAL_DIRECTORY/evidence/tts-24k.wav"
```

WSSの独立CLIは同じ合成WAVで各製品・scenarioを確認します。`roundtrip`は実音声と正常終端、`cancel`は取消確認、`disconnect`は途中切断、`keepalive`は短い無送信後の往復です。取消送信・端末切断だけでサービス側の停止・課金終了と判定しません。

```sh
python3 scripts/voice_client.py --url wss://gateway.example.com/models/voice-live/v1/ws \
  --product voice-live --voice en-US-Ava:DragonHDLatestNeural \
  --audio-file "$DEPLOY_LOCAL_DIRECTORY/evidence/tts-24k.wav" --key-file "$DEPLOY_LOCAL_DIRECTORY/secrets/gateway-key.txt" \
  --scenario cancel --output "$DEPLOY_LOCAL_DIRECTORY/evidence/voice-live-cancel.json"
```

RealtimeとVoice Live、Speech単体のSTT／TTSは固定モデル・voice・API版・イベントを分けます。WebRTCはPortalと回帰試験画面で確認し、ICE／DTLS・実RTP・正常終端・終了制御を記録します。CLI等を併用しても、単なるSDP交換や無音ファイルを音声往復成功にしません。

`tests/browser/realtime.html`は`realtime_browser.py`が読み込むWebRTC／Voice Liveの回帰試験用画面です。Portalの実装とは独立して接続・中断・異常系を確認するため、試験資材として管理します。ローカル専用relayからだけ配信され、PortalのイメージやAzureへの配備資材には含まれません。

DecisionのCLIは既定で計画表示だけです。実行はhelpの`--execute`等に従い、短い合成入力を使います。匿名・不正キーと、利用範囲外の有効キーを分け、数値・相関・usageだけを記録します。

## 固定依存と配布物

CoreのPython依存は[共通requirements](scripts/integration-requirements.txt)と[Brokerのrequirements](apps/gateway/credential-broker/requirements.txt)、PortalのNode依存は[package.json](apps/portal/package.json)と[package-lock.json](apps/portal/package-lock.json)で管理します。変更内容に応じたローカル検査と、該当する仕様書の実環境試験で確認します。

公開ソースには架空値と配布可能な素材だけを含めます。秘密・実環境の導入先設定・証明書・実行証跡は環境別のGit管理外領域`.local/<environment>/`で管理し、wheel／image／ZIP等の配布物にも混入させません。実情報の保存を認めるのはルートの`.local/`とazd専用の`.azure/`だけであり、ignore対象一般を保存・公開の許可と解釈しません。不要な生成物は個別に回収し、別用途のデータを一括削除しません。
