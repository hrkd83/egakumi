# Core内の管理ポータル

`apps/portal`はAIゲートウェイのサービス提供用フロントアプリです。Next.js・TypeScriptを使い、Better AuthによるMicrosoft Entraサインイン、SQLでのセッション管理、グループ所有のAPIキー管理を実装しています。Model Gatewayの各モデル、Tool GatewayのMicrosoft Graph /me・PII Redaction・Document Analysisを実行できます。Hosted Agent APIは準備中ページです。

Tool GatewayはREST・MCP・A2Aに分けて案内します。RESTは上記3 API、MCPはPII RedactionのStreamable HTTP接続を掲載し、A2Aは準備中です。MCPの初期化・ツール照会・実行と購読キーの扱いはPortalのサーバー側が担当します。Document Analysisのファイル受付と解析結果は画面表示のために使い、PortalのSQLへ保存しません。[文書解析の仕様と配備](../reference/document-analysis.md)

公開入口は`https://portal.<domain>`です。Gatewayの`https://gateway.<domain>`とは共通App Gatewayのホスト名で振り分けます。

App Gatewayは公開HTTPSを終端し、Portalへの後段通信とヘルスプローブには閉域HTTP80を使います。Firewallを通る往復経路、ACA内部VIPとbackend Hostは維持します。ACAの`allowInsecure: true`はこのHTTP受付を追加し、ACAのHTTPS443受付そのものは残します。APIMから共用ACAへのHTTPS通信も維持します。公開ポートや後段プロトコルを選ぶparams/configは設けません。

言語付きURLへの転送、OAuthの戻り先、Secure CookieとOrigin検査は公開`BETTER_AUTH_URL=https://...`を基準にします。ACAが`X-Forwarded-Proto`を`http`へ上書きしても、内部HTTP URLをブラウザーへ案内しません。本番Firewallの既存IDPS設定と、検証用のIDPS Offはこの変更で切り替えません。

今回の公開入力整理は資材に実装しています。Azureへの一括反映、全環境再構築E2Eと実受入は未実施です。ローカル試験結果の最終集約前に一式成功と判断しません。[入力の配置判断と移行条件](04-params.md#公開入力71主項目の判断記録)を確認してください。

## 配備資材の置き場所

```text
apps/portal/
├── app/・components/・lib/   Next.jsアプリ本体
├── Dockerfile              Linux用コンテナーイメージ
├── .dockerignore           ローカル設定・秘密・生成物の除外
├── deploy/
│   ├── main.bicep          Portal配備の入口
│   ├── database.bicep      認証DB・バックアップ・必要な権限
│   ├── api-keys.bicep      既存APIMへの購読管理権限
│   ├── schema-job.bicep    SQL初期化ジョブ
│   ├── group-reconciliation-job.bicep  所有グループの定期照合
│   ├── application.bicep   Container Appと認証用設定
│   ├── gateway-egress.bicep Portalから公開GatewayへのHTTPS許可
│   └── build-image.sh     管理VM上でのイメージ作成・ACR登録
├── scripts/                SQL初期化・実行用権限の設定
└── sql/                    バージョン付きテーブル定義

scripts/deploy_portal.py     手元とCIから共通で使う配備コマンド
.github/workflow-templates/portal.yml.example GitHub Actionsの入口
```

## コア基盤とPortal配備の分担

| 工程 | 作成・更新するもの |
| --- | --- |
| `azd provision` | 共通SQLサーバー、Private EndpointとDNS、ネットワーク、ACR、Container Apps環境、Portal用実行ID、初期Container App |
| `azd up` | 共有基盤・Gateway・Portal初期サンプル・KV自己署名認証証明書を準備 |
| Portal配備工程 | Portal専用DB、署名鍵と読み取り権限、SQL初期化ジョブ、認証・APIキー台帳と実行用SQL権限、管理グループと標準API正本から生成する製品承認・検証用APIM購読の作成・台帳登録、APIMの購読管理権限、Portalイメージ、既存Container Appの本体 |

新規構築は`azd up`で共有資源・Gateway・Portal ACA初期サンプルと認証証明書を準備します。`postprovision`が管理VMで実行された発行結果を照合し、公開証明書と固定参照を保護保存します。`postup`は`deploy_applications.py --gateway-only --execute`でGatewayだけを配備します。[専用Portalアプリの手動登録](entra-portal.md)後、以下の個別配備でPortal本体をビルドしてサンプルを更新します。`azd provision`ではGateway本体も未配備です。

**Portal専用DBはPortal配備資材で管理します。構築済み基盤のPortalだけを更新・再試行するときは、`deploy_portal.py --execute` を使えます。** 共通SQLサーバー`sql-<環境>-<製品>`を再利用し、その中へ`sqldb-<環境>-<製品>-portal`を作成します。CM用の`...-credentials` DBとは分離します。

コアの初期Container Appは配備先を用意するための仮イメージです。Portalの画面・認証・ヘルスAPIはPortal配備後に利用可能になります。

Portal配備はネットワークRGの専用Firewall規則グループ `rcg-portal-gateway` も管理します。ACAサブネットから、構築入力の `gateway.publicHostName` だけへのHTTPS/443を許可します。既存の共通規則を置き換えず、PortalのサーバーからAPIM経由でモデルを実行するための通信経路を追加します。

DBのSKU・容量・バックアップは標準`config`の`portalAuthentication`で管理します。検証用はBasic・5 DTU・2 GB・バックアップ7日・Local、本番用はGeneral Purpose・2 vCore・32 GB・14日・Geoです。配備コマンドは既存の`.local/<environment>/params.local.json`を正本として読みます。管理グループObject IDとグループ表示名の正規表現が未設定の場合だけ、初回対話で入力し、同じparamsの`portalApiKeys.platformGroupId`と`groupNamePattern`へ保存します。製品・購読数・内部配列はparamsへ保存しません。

## Entraと認証の準備

Portalは認証窓口・CMと同じ単一テナントアプリ`AI Gateway Platform`を共用します。[Entra事前準備](02-entra.md)に従い、Webの戻り先へ以下を追加します。

```text
https://<PORTAL_HOST>/api/auth/callback/microsoft
```

ログイン時に要求するスコープは`openid profile email`です。グループ所属照会には同じアプリのアプリケーション権限`GroupMember.Read.All`と`User.ReadBasic.All`、および管理者同意を準備します。新構成は専用Portalアプリへ公開証明書を登録し、専用UAMIからKV内の非exportable鍵でclient assertionへ署名します。新Portalのclient secretは不要です。標準Graphの登録値はparamsの`credentialProviders.graphDelegated.clientId`・`clientSecretFile`に置き、共通表示名・scope・poolはconfigに置きます。内部の論理名`graph-delegated`、秘密参照名と選択profileを保持し、CMの既存Client ID・secretは維持し、Portal本体は`portal.authentication.tenantId/clientId`と保護certificate stateを使います。本人は検証済みのテナントIDとオブジェクトIDで識別します。

PortalのSQLには利用者・アカウントの対応・ログインセッションと、暗号化したGraph本人接続のクライアント状態を保存します。Microsoftのアクセストークン・更新トークン・IDトークンは保存しません。Graphのトークン管理はCMが担当し、`X-Core-Connection-Session`はPortalログインとは別の期限・用途です。BFFが確認済みtid/oidと購読に紐付け、ブラウザーへ秘密値を公開しません。

## 配備の実行

初期構築では、Entra・DNS・秘密ファイルとparamsを準備して、リポジトリ直下で実行します。

以下は[第4章](04-params.md)で作成した`DEPLOY_PARAMS_FILE`と、第1章で決めた`DEPLOY_ENVIRONMENT`を使います。別のターミナルでは、構築対象の同じ値を設定し直してください。

```sh
export WORKFORCE_CONFIG="$DEPLOY_PARAMS_FILE"
azd up --environment "$DEPLOY_ENVIRONMENT"
```

標準APIは正本から常用で解決し、公開`apiDefinitions`・`publishApis`入力は廃止しました。旧入力は空配列やfalseでも拒否します。基盤だけを準備するときは`azd provision`を使い、アプリの計画／反映は個別コマンドの`--execute`で区別します。既存paramsは自動変換せず、[移行条件](04-params.md#既存入力の移行を確認する)を先に確認します。

Gateway配備に失敗した場合は初期構築が失敗します。Portal本体はEntra登録後の独立した工程で、失敗時はその個別コマンドの終了結果を確認します。作成済みの基盤・Gatewayを保持して原因を直し、必要な個別コマンドから再試行できます。全体の成否は実行したコマンドの終了コードで確認し、古い成功結果JSONを流用しません。

以下はPortalだけを配備・再配備する手順です。前提はコア基盤とGatewayの構築完了、Azure CLIへのログイン、Python 3.11以上、Bicep CLIです。手元のDockerや公開ACRは不要です。既存管理VMから、コア基盤で用意した通信経路を使ってイメージを作成します。

最初にリポジトリ直下で計画を確認します。

```sh
python3 scripts/deploy_portal.py --config "$DEPLOY_PARAMS_FILE"
```

既定の計画モードは配備先と作業内容を表示し、Azureリソースの変更・イメージ作成・秘密の生成は行いません。既定では構築時の`.local/<environment>/state/latest.json`を参照します。

実行するときは`--execute`を追加します。

```sh
python3 scripts/deploy_portal.py \
  --config "$DEPLOY_PARAMS_FILE" \
  --execute
```

初回は、`azd up`のPortal工程または個別Portal配備で、管理グループObject IDと対象グループ表示名の正規表現だけを入力します。`platformGroupId`は管理画面と検証用購読の管理グループです。表示名は入力・保存せずGraphから取得し、現在の所属、`securityEnabled=true`、正規表現との一致を確認します。無人実行ではこの2項目をparamsへ事前設定してください。

公開例は`groupNamePattern:""`で、`platformGroupId`を省略しています。準備コマンドが作成した保護paramsへ次の2項目を設定するか、初回対話を使います。`<...>`は用途付きプレースホルダーです。

```json
{
  "portalApiKeys": {
    "platformGroupId": "<PLATFORM_GROUP_OBJECT_ID>",
    "groupNamePattern": "^ORG_"
  }
}
```

共通configは次の形です。初期値5を現在値のまま移設しています。

```json
{
  "portalApiKeys": { "verificationMaxSubscriptions": 5 }
}
```

管理グループ1件と標準API正本の全製品から、内部の製品承認と検証用購読を自動生成します。製品subsetと購読数を初回対話で選ばせず、生成済みの`groups`・`verificationSubscriptions`を公開paramsへ書き戻しません。旧項目は空配列でも入力検査で拒否します。

`groupNamePattern`は企業・環境ごとのRE2互換の正規表現です。上の`^ORG_`は独立した架空の命名規則例であり、既定値ではありません。

| 設定例 | 対象の名前 |
| --- | --- |
| `^ORG_` | `ORG_`で始まる名前 |
| `^(ORG_|DEPT_)` | どちらかの接頭辞で始まる名前 |
| `^組織_[0-9]{3}$` | `組織_`の後に数字3桁が続く名前 |
| `_HR$` | `_HR`で終わる名前 |

通常は大文字小文字を区別します。先頭一致は`^`、完全一致は`^...$`で指定します。最大512文字で、空欄・不正な構文・先読み・後方参照は実行設定として受け付けません。配備ジョブとアプリは共通のRE2検証を使い、Node標準の正規表現へフォールバックしません。空欄は初回入力待ちであり、全グループの許可にはなりません。

Graphへ正規表現を送るのではなく、取得した名前をサーバー側で評価します。移管先の検索語は名前の先頭から入力し、検索結果にも同じ正規表現を適用します。対象がないページに続きがある場合は、画面の追加取得で次を確認できます。セキュリティグループ条件は正規表現にかかわらず必須です。

既存の`006_group_name_pattern.sql`には初回の固定`ORG_`から`^ORG_`へのSQL移行があります。適用前に現行DBと移行内容を確認し、非標準値をこの例へ無断変換しません。今回の公開params整理は、旧グループ／承認／購読の自動変換を許可するものではありません。

通常の所有候補は、Graphで現在の所属を確認でき、`securityEnabled=true`で表示名が`groupNamePattern`に一致するグループです。初回利用時にObject IDで台帳へ登録し、通常発行カタログを適用します。管理グループの検証製品承認をすべての利用者へ配布する設定ではありません。キーの表示・発行・更新時にも現在の所属と属性を確認します。

内部の検証用購読は、標準製品ごとに`platform-<productId>`という安定した購読IDと標準表示名で生成します。Gatewayで製品を登録した後にPortalを配備し、SQL初期化で購読の作成・登録を行います。個別更新でもGateway→Portalの順を守ります。

同じ生成ID・同じ製品スコープの購読がAPIMに存在すれば、既存キーを変更せず採用します。明示的404の場合だけ新規作成し、通信障害や権限不足を不存在へ読み替えません。APIM側の既存表示名を勝手に変更せず、masterや生成対象外の共有購読を一括採用しません。

旧paramsに追加所有グループ、限定した製品、異なる上限、任意の購読IDがある場合は、保護params・SQL台帳・APIM metadataを確認してから個別に移行します。旧入力を削除するだけで新生成IDへ付け替えず、所有者・製品範囲・購読ID・キー保持と復旧方法を確定するまで停止します。

新規処理はAPIM操作前にSQLへ`creating`の予約を残し、同じ購読の削除や並行配備と排他します。途中で失敗した場合も、次のPortal配備でその予約から再開します。登録完了までは購読数の枠を確保し、同じキーを重複発行したり、削除済みの購読を復活させたりしません。

検証用管理グループの共通購読数上限はconfigの`portalApiKeys.verificationMaxSubscriptions`から内部`maxSubscriptions`へ適用し、初期値は製品ごとに5件です。通常発行キーのカタログ上限とは別です。作成中、停止中、削除処理中も含め、削除が確定するまで枠を維持します。この値は購読数の制限であり、利用金額の上限ではありません。現在の購読は金額上限なしの`budgetMode: "unlimited"`として保存します。JPY・月次リセットによる金額上限の指定は、Gateway側の金額集計・停止処理を実装するまでは提供しません。

処理は次の順で進みます。

1. 管理VMに一時的な組み込み`AcrPush`を付与し、公開ソースだけを渡してイメージを作成します。非公開ACRの`portal`へ登録し、digestを取得します。一時ロールとRun Commandは回収します。
2. 共通SQLサーバーへPortal専用DBを作成・更新し、`caj-<環境>-<製品>-psql`を配備します。Portal実行IDへ、組み込みの`API Management Service Contributor`を対象APIM単体の範囲で割り当てます。
3. この手動ジョブを配備スクリプトが起動し、`sql/`のDDL、管理グループとAPI正本から生成した製品承認、検証用購読の作成・台帳登録、実行用IDのSQL権限を設定します。SQL初期化に成功するまでアプリ本体は更新しません。
4. Microsoft認証の戻り先には、後述の限定WAF設定を適用します。この段階ではPortalの稼働中backendを切り替えません。
5. 既存の`ca-<環境>-<製品>-portal`をdigest指定のイメージへ更新し、HTTP80受付、認証用環境変数とKey Vault参照を渡します。今回の期待imageと正常revisionが一致するまで待ち、以前のrevisionがHealthyというだけでは進みません。
6. FirewallのAppGW→ACA TCP80許可、内部VIP、backend HostとHTTP受付を照合してから、App Gatewayの`behttp-portal`と`probe-portal`を同一ETag付きPUTでHTTP80へ揃えます。認証不要の`/api/health`、HTTP 200判定を使い、ほかのプローブ・API転送・公開証明書を保持します。新しいリビジョンと公開URLのヘルスを確認します。Readinessでは`/api/health/ready`から実行用IDのSQL照会と専用鍵の準備も確認します。
7. Gatewayの固定ポリシーとKey Vault参照を確認して、購読プロファイルの参照を有効化します。結果は`.local/<environment>/state/portal-deployment.json`へ保存します。

SQL初期化ジョブはスケジュール実行しません。テーブル初期化をアプリの起動処理にも含めません。再配備では既存データを維持し、未適用のマイグレーションを適用します。

本人接続の保存用`010_connection_sessions.sql`も同じSQL初期化工程で適用します。`portalConnectionState`に暗号化した本人接続・認証取引・日次更新候補を保存し、実行IDへSELECT・INSERT・UPDATEだけを許可します。Readinessでも010と表の利用可否を確認します。Graph用に別DBや新しいジョブを用意する必要はありません。対象環境へはGateway、Portalの順に配備し、12属性の実取得と本人接続の継続利用を別途確認します。

このジョブには既存のスキーマ管理IDとPortal実行IDの2つを割り当てます。SQLはスキーマ管理ID、APIMのメタデータ確認・購読作成はPortal実行IDを明示的に使い、SQL用の`AZURE_CLIENT_ID`をAPIMへ流用しません。購読登録のための別ジョブやカスタムロールは追加しません。

アプリ更新工程では`caj-<環境>-<製品>-psync`も配備します。このジョブは15分ごとに、台帳にある所有グループのうち観測が古いものを最大100件照合します。Entra全体のメンバー名簿は複製しません。Portalと同じイメージ・実行IDとGraph資格情報を使い、購読の停止・削除・メール通知は行いません。総グループ数や通信状況によって全件の照合に複数回必要なため、各行の確認日時を参照します。

別の構築記録を使う場合は`--state-file <path>`、Azureに記録された完了済みのコア構築出力を読む場合は`--deployment-name <構築名>`を指定します。作成済みのPortalイメージを使う場合は`--image-digest sha256:<digest>`を指定できます。

### 既存HTTPS後段からHTTP80へ移行する

新規構築はBicepの固定HTTP80設定を使います。構築済みのHTTPS後段は、最終80-onlyのCoreを先に配備すると旧Firewall443許可が先に消えるため、次の順序で移行します。構築前処理は旧HTTPS後段を検出した場合に停止し、Coreの先行切替を防ぎます。Azure CLIは認可済みのWindows側を使い、WSL上のLinux Azure CLIでは認証しません。

以下はWindows PowerShellの例です。`DEPLOY_PARAMS_FILE`は同じ対象環境の保護入力を指定します。移行機能をparams/configに追加せず、既存環境だけの運用操作として実行します。

```powershell
python -B scripts/operations/portal_backend_http.py status --config $env:DEPLOY_PARAMS_FILE
python -B scripts/operations/portal_backend_http.py prepare --config $env:DEPLOY_PARAMS_FILE
python -B scripts/operations/portal_backend_http.py prepare --config $env:DEPLOY_PARAMS_FILE --execute
python -B scripts/deploy_portal.py --config $env:DEPLOY_PARAMS_FILE --execute
python -B scripts/operations/portal_backend_http.py status --config $env:DEPLOY_PARAMS_FILE
```

`prepare`は`rcg-portal`の所有済み`portal-ingress`へ`allow-appgw-aca-http` TCP80を追加し、旧`agw-aca-https` TCP443と他規則を保持します。旧HTTP名`agw-aca-http`が単独で存在する場合は、通信条件を変えずに新名へ揃えます。Core・Portalのreadiness確認は新旧HTTP名を認識し、同コレクションでの新旧HTTP規則の同時存在、同名重複、許可条件の不一致では書き込み前に停止します。既定は読取りによる計画です。`--execute`は環境別ロック、事前の保護記録、ETagによる更新、読戻しを使います。Portal配備は修正済みimage/revisionのHTTP受付を確認してから、変更前Gateway設定を`.local/<environment>/state/portal-http80/`へ保護記録してAppGWを切り替えます。途中失敗時はその時点の実状態を読み、古い成功記録だけで再開しません。

公開HTTPS、公開80からHTTPSへの転送、言語redirect、Microsoft login/callback/session/logout、Secure Cookie・CSRF、代表APIの逐次転送と保守通信を確認します。`status`の`coreHttpContractReady`は設定の読取り照合であり、実際の疎通成功を表すものではありません。疎通確認後に最終Coreを再配備し、このAppGW→ACA区間の旧443許可を撤去します。以降のCore・Portal再配備もHTTP80へ収束し、APIM→ACA HTTPS443を維持します。

切戻しは次の計画を確認してから実行します。

```powershell
python -B scripts/operations/portal_backend_http.py rollback --config $env:DEPLOY_PARAMS_FILE
python -B scripts/operations/portal_backend_http.py rollback --config $env:DEPLOY_PARAMS_FILE --execute
```

切戻しは必要な旧Firewall443許可を先に復元し、現在の他設定を保持してPortal backend/probeだけを同一PUTでHTTPS443へ戻します。ACAのHTTP受付とFW80は残し、稼働経路を先に閉じません。公開疎通を確認してから不要な移行設定を扱います。AppGW全体PUTによる既存接続への影響は別途確認し、無停止を保証しません。

## SQLと秘密情報

### 通常発行キー・会社情報

通常のキーは、リリース済みAPIをまとめたAPIM製品`released-apis`に固定します。Gateway配備資材がこの製品を作り、API定義の`released: true`に従って関連付けます。`false`へ変更したAPIは公開製品から外し、API本体と既存の検証用製品は維持します。独自APIの未指定は`false`です。Graphの本人接続に必要な認証開始・完了確認等の補助APIも、同じキーで使えるよう配備資材で関連付けます。

配備順はGateway→Portalです。初期構築の `azd up` はこの順に実行します。構築済み環境では個別コマンドで更新でき、Portalからの製品選択・製品作成は不要です。`model-users`・`pii-users`・`connection-users`等の検証用製品と宣言済みの検証用購読は保持します。

発行フォームでは、所有グループ・用途名に加えて、クライアント種別・会社・連絡先メールを必須入力とします。クライアント種別はスクラッチ開発システム、SaaS、OSSベース、ローコード／ノーコード、その他です。システム／サービス／プロダクト名とチーム名は任意です。連絡先は担当者が交代しても連絡できるメーリングリストを推奨します。これらの情報は統計・棚卸に使い、会社によるワークスペース分割やキーの所有権判定には使いません。

会社は`/<言語>/company-administration`から、基盤管理グループの現在のメンバーだけが登録・改名・選択停止できます。paramsや構築時の会社seedは使用しません。最初に管理者が実際の会社を登録します。選択停止は新規発行の候補から外す操作であり、既存キーを失効させません。更新は読込時の版と照合し、並行編集は409で検出します。

新規発行するAPIM購読の表示名は、選択した会社のマスタ名を使用します。APIM側の上限に合わせ100文字までとし、Portal側では会社の正式名と用途名を別々に保持します。既存購読の表示名は自動で一括変更しません。制限編集画面では数値欄の上下限を常時表示します。

Model Gatewayの実行パネルでは、現在の所属グループで利用できるキーから選択して実行します。候補は有効な台帳と対象APIを含む製品に限定し、実行時にも現在の所属・APIMの有効状態・製品/API関連を再確認します。ブラウザーへ渡すのは台帳IDと表示情報だけです。モデル実行には選択キーを使用し、Portal共通の組み込みキーへは代替しません。

`008_key_usage_companies.sql`が会社マスタ`portalCompany`・会社操作監査`portalCompanyAudit`とキーの利用元属性を追加します。既存の検証用キーは属性を推測せずNULLのまま移行します。会社の物理削除は提供せず、参照と監査を保持します。Portal配備のSQLジョブにこの移行と最小権限を含め、実行用IDにはDDLや監査の変更・削除を許可しません。

Azure SQLは公開アクセスを無効にし、Private EndpointとEntra認証を使います。SQL初期化ジョブは既存のスキーマ管理IDを使います。Portal本体は既存の`id-<環境>-<製品>-portal-pull`を再利用し、認証用4テーブルの読取・追加・更新・削除、APIキー承認表の読取、購読台帳の読取・追加・更新、監査表の読取・追加だけを許可します。Graphによる観測と適格グループの初回登録には、限定ストアドプロシージャのEXECUTEを付けます。承認表への直接更新、DDL、監査行の変更・削除は許可しません。

APIキー管理の組み込みロールは購読以外のAPIM設定も変更できるため、リソースグループやサブスクリプション全体へ広げず、対象APIM単体の範囲に限定します。アプリ側でも承認されたグループ・製品と、Portalから発行した購読または配備正本で明示採用した検証用購読だけを操作します。APIM全体の購読一覧からの自動取り込みは行いません。

`007_apim_subscription_identity.sql`は`portal-UUID`形式の内部IDを維持したまま、実際の`apimSubscriptionId`との対応を追加します。APIM操作は実ID、所有権・利用制限・既存の外部キーは内部IDを使います。配備による初回登録は`creationSource: "deployment"`・`createdByObjectId: NULL`とし、架空の個人OIDを作らず`portalApiKeyImportAudit`へ実行ごとの`runId`・メタデータのSHA-256要約・APIMスコープを記録します。秘密キーは保存しません。

登録時の利用制限は無制限です。再配備では処理途中の`creating`予約だけを再開し、登録完了済みの対応は読み飛ばします。所有グループの移管、削除済み状態、設定した利用制限、既存の監査を維持し、削除済みの検証用購読を再作成・復活させません。

## ローカルSQLへの既存購読登録

この節はローカル開発・試験専用です。通常のAzure配備では前述のSQL初期化ジョブが購読を準備するため、この手動importは使いません。ローカルSQLとAzure SQLは同期しません。

既存APIMのID・製品スコープ・状態を確認したメタデータマニフェストを用意すれば、ローカルの管理者用SQL設定で台帳登録だけを試せます。このCLIはAPIMへ接続せず、新しいキーを発行しません。先にローカルSQLのマイグレーションと、同じテナントの基盤管理グループ・製品承認を準備します。

```json
{
  "schemaVersion": 1,
  "tenantId": "<利用者EntraテナントID>",
  "platformGroupId": "<基盤管理グループObject ID>",
  "apimResourceId": "/subscriptions/<AzureサブスクリプションID>/resourceGroups/<サービスRG>/providers/Microsoft.ApiManagement/service/<APIM名>",
  "subscriptions": [
    {
      "subscriptionId": "<確認済みAPIM購読ID>",
      "productId": "model-users",
      "displayName": "基盤管理用Model Gateway検証",
      "state": "active",
      "scope": "/subscriptions/<AzureサブスクリプションID>/resourceGroups/<サービスRG>/providers/Microsoft.ApiManagement/service/<APIM名>/products/model-users"
    }
  ]
}
```

リポジトリルートで、絶対パスの`DEPLOY_LOCAL_DIRECTORY`と同じ環境の`DEPLOY_PARAMS_FILE`を設定し、最初は`--execute`を付けず計画を確認します。マニフェストは`.local/<environment>/evidence/`へ保存します。`--apim-resource-id`には配備先のAPIMリソースIDを独立に指定し、マニフェストの対象と照合します。`PORTAL_APIM_RESOURCE_ID`が設定済みなら、この引数は省略できます。

```sh
python3 scripts/run_portal_local.py --config "$DEPLOY_PARAMS_FILE" --admin -- \
  node --conditions=react-server --experimental-strip-types \
  scripts/import-local-api-key-subscriptions.ts \
  --manifest "$DEPLOY_LOCAL_DIRECTORY/evidence/portal-subscription-import.json" \
  --apim-resource-id '<配備先APIMリソースID>'
```

計画が正しければ同じコマンドの末尾に`--execute`を追加します。変更先はローカルSQLだけで、登録完了済みの台帳対応は読み飛ばします。購読キーやトークンをマニフェストへ含めると検証エラーになります。

## 実行時の秘密・権限

Azure上のPortal本体はKey Vaultの次の値をマネージドIDで参照します。秘密値をイメージ・`params`・ブラウザーへ埋め込みません。

- `core-credential-<論理プロバイダー名>`：共用Entraアプリの既存client secret。標準の論理プロバイダー名は`graph-delegated`。
- `core-portal-auth-secret`：Better Auth専用の署名鍵。初回だけ作成し、存在する場合は更新しません。
- `core-subscription-profile-key`：APIMからPortalへのプロファイル照会専用鍵。両方の実行IDへ、このsecretを読むための組み込みロールを割り当てます。

新規署名鍵は端末の環境別領域に保護保存してからsecureパラメーターでKey Vaultへ渡します。ローカル検証用の`portal/runtime.env`はAzureへ渡しません。カスタムAzureロールは作成しません。

配備を実行するIDにはサービスRGへの配備、既存管理VMでのRun Command、対象マネージドIDの割当、対象ACR・Key Vaultシークレット・APIMへの必要な組み込みロールの割当、および既存App Gatewayのヘルス監視を更新する権限が必要です。これは利用者のPortalログイン権限とは別です。

## GitHub Actions

GitHub Actionsを使う場合、`.github/workflow-templates/portal.yml.example`も同じ`deploy_portal.py`を呼びます。通常の`azd up`と個別配備コマンドはActionsを前提にしません。イメージは管理VMで作るためrunnerでDockerやSQL接続を準備する必要はありません。Azure管理APIへの接続とPython 3.11以上・Azure CLI・Bicepを準備します。

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

### 購読ごとの保護上限

最新のGateway資材を配備してから、このPortalを配備します。初期構築は `azd up`、構築済み環境の更新は個別コマンドでGateway→Portalの順に実行できます。Portal配備がSQLの`003_api_key_limits.sql`と専用のKey Vault鍵`core-subscription-profile-key`を準備し、内部URL・鍵参照をAPIMへ設定します。ログイン用の鍵やEntraアプリのシークレットは流用しません。

APIキー管理画面のキー詳細から「利用制限の設定」を開きます。初期値は全項目無制限です。要求回数と期間（1〜300秒）、対応するLLMの毎分トークン数、期間内トークン数を個別に設定できます。設定の保存はSQLの行と監査の更新だけで、APIMのXMLや購読キーを書き換えません。競合した更新は409で停止し、最新値を読み直します。

APIMは検証済み購読IDを使って専用の内部APIへ照会し、正常な応答を30秒キャッシュします。キャッシュのない状態でSQL・通信・認証が失敗した場合は503を返し、無制限へ切り替えません。Portal管理外の既存購読は、SQL照会に成功した場合だけ未設定の無制限として扱います。全体ポリシー`service.xml`の再生成は不要です。

`subscription-profile.xml`は固定処理の共通ソースですが、キャッシュポリシーの制約によりAPIMのfragmentリソースにはせず、Gateway配備時に各APIのXMLへ展開します。購読設定の変更では再展開しません。`rate-limit.xml`が取得済みデータで回数制限を適用し、本文を扱う`token-limit.xml`は対応するHTTP APIだけから参照します。

トークン制限は現在、Foundry OpenAIのHTTP Chat Completions / stateless Responses操作が対象です。入力と出力の合計を扱い、SSEはAPIMの推計を含みます。画像・音声・Realtime・Embedding等へトークン制限を推測適用しません。期間の境界はUTCで、月次は毎月1日00:00 UTC（日本時間09:00）です。金額上限の保証ではありません。

保存済み表示はSQLへの保存成功を示し、Azureの実際の制限適用まで検証したという意味ではありません。Gateway→Portalの配備後、少数のリクエストで無制限・設定した上限・主副キー共通のカウンターを確認してください。観測手順と読み取り専用KQLは[購読単位の利用量観測](../testing/subscription-limits.md)にあります。

### 認証とキー管理

- 公開URLを未認証で開くとカタログを表示せずサインイン画面へ移動する。
- Microsoft認証後はカタログへ戻り、再読み込みでログインを維持する。
- ログアウト後はカタログを再び保護する。
- `/api/health`がHTTP 200と`{"status":"ok","service":"egakumi-portal"}`を返し、App GatewayのbackendがHealthyになる。
- SQL初期化ジョブが成功し、Portal用DBとCM用DBが分離されている。
- APIキー管理画面で、本人が所属する承認済みグループだけを選択できる。
- グループ所有の購読を発行・表示・停止／再開・棚卸・削除でき、別グループの利用者からは参照できない。
- 対象外接頭辞・セキュリティグループ以外の候補は、URLや本文でIDを直接指定しても発行・移管先に指定できない。

### Tool GatewayのGraph本人接続

GatewayのGraph APIは`graph-profile-v1.json`の`$select`で、本人の表示名・名・姓・UPN・ID・役職・会社・部署・従業員ID・勤務先所在地・メール・アカウントの有効状態の12項目に固定します。Portalで任意のGraph URLや取得項目を入力させません。GatewayのAPI定義を更新してからPortalを配備します。

`/<言語>/tools`のMicrosoft Graph /meカードからグループ所有の有効な購読を選んで実行します。BFFがGraphへ先にアクセスし、接続が必要なときだけ認証URLを返します。同じ本人のMicrosoft認証を完了すると、BFFで完了状態と本人を確認してから読み取りを一度再開します。12属性と実際のJSONを確認してください。null・未返却の属性は「－」、アカウントの有効状態がfalseの場合は「無効」と表示します。継続利用では保存済み本人接続を使い、認証画面を再表示しません。

キー・本人接続セッション・pollSecret・日次更新候補がブラウザーの応答へ含まれないこと、本人が一致しない場合にプロフィールを返さないこと、認証待ちでパネルを閉じると照会が停止することも確認します。条件付きアクセスによる拒否を自動認証ループへ変換しません。詳細は[ローカル確認手順](../testing/portal-local-auth.md#5-tool-gatewayのgraph確認)と[共通クライアント契約](../reference/connection-session-clients.md)を参照してください。

保存状態はAES-256-GCMで暗号化し、`core-portal-auth-secret`（実行時の`BETTER_AUTH_SECRET`）から用途を分けて鍵を導出します。この秘密を更新すると既存のGraph暗号文は復号できなくなるため、管理者による保存状態の初期化後に本人の再認証が必要です。画面の再実行だけでは復旧せず、暗号化状態の不正は自動破棄せず利用を停止します。CMのMicrosoftトークンをPortalが保管する構成には変更しません。

### 所有グループの変更と管理ダッシュボード

キー詳細の所有者変更では、移管元の現在の所属メンバーが、環境の対象条件に一致するセキュリティグループへ直接変更できます。移管先への所属や承認は要求しません。候補には「所属中／未所属」を表示し、確認画面に移管元・移管先の名称とObject IDを出します。未所属の移管先へ移すと、以後そのキーをPortalで参照・管理できなくなることを明示します。変更は移管先の製品承認と購読数上限を守り、変更元・変更先・操作者・日時を同じSQLトランザクションで監査へ残します。

所有者変更はSQL台帳の変更で、APIM購読ID・主キー・副キー・製品・利用制限は維持します。移管元の人が既に取得したキー値は失効しないため、失効が必要なら別途キーを再生成・切替します。

`/<言語>/api-key-administration`は、`platformGroupId`で指定した基盤管理グループの現在のメンバーだけに表示します。サイドメニューを隠すだけでなく、ページとAPIの両方で認可します。登録済み所有グループの正常・削除・対象外・確認不能、影響する購読数、各行の最終確認日時を確認できます。Graphの403・429・通信障害をグループ削除とは扱いません。ダッシュボードの再照合も同じ管理グループに限定します。グループの廃止だけを理由に購読を自動停止・削除することはありません。

ログインは設定した単一Entraテナントを使用します。会社マスタはキーの利用元属性であり、認証テナントやワークスペースを分離する設定ではありません。グループ操作は前述のGraphアプリケーション権限を使います。

この確認でGraph等の実API呼び出しが完了したという意味にはなりません。購読キーの管理と、CMによる本人のGraph接続は別に検証します。

`/api/health`はログイン不要のプロセス稼働確認用APIです。応答に`Cache-Control: no-store`を付け、SQLへのアクセスや秘密情報の返却は行いません。

Microsoftから返る認証コードには、WAFのSQL文字列検出で誤検知される文字列が含まれることがあります。現行資材の`wafpol-p-auth-<ID>`は共通WAF設定を引き継ぎ、`/api/auth/callback/microsoft`の`code`引数の値だけを、規則`942430`・`942440`・`942450`から除外します。`<ID>`はparamsの`services.apimInstanceId`に保存した構築識別子です。この戻り先以外の画面・APIには適用せず、他の規則・他の引数は検査を継続します。認証コードはアプリ側でもstate・PKCE・Microsoftとのコード交換で検証します。

HTTPSのセッションCookie値にも不透明な文字列が含まれるため、Portal HTTPS listener専用の`wafpol-p-base-<ID>`を使います。正本`apps/portal/waf-session-exclusions.json`に従い、`__Secure-egakumi.portal.session_token`の値を規則942450だけから除外します。URI別policyはlistener側のpolicyを継承しないため、上記callbackとPortalのデータ欄policyにも同じ除外を合成します。共通WAFの本文検査・サイズ制限・ログのマスク・customRulesを保ち、Gateway側のpolicyや他のCookie・規則は変更しません。

## 購読台帳からAPIMへの同期

Portal配備で`009_subscription_sync.sql`を適用し、実行IDへ監査表のSELECT・INSERTだけを付与します。Container Appには、その配備で作成・利用したAzure SQLのサーバー名・DB名を同期の固定接続先として設定します。新しいリソースやカスタムロールの追加、コアの`azd up`再実行は不要です。

配備後、基盤管理グループのメンバーで`/<言語>/subscription-sync`を開きます。接続先が意図したAzure SQLとAPIMであることを確認し、差分を選択して確認画面から同期します。最大10件ずつ、作成・更新・削除をSQLの台帳へ一致させます。SQL未登録のAPIM購読も削除します。`master`と処理途中のSQL行は対象外です。再作成時のキー変更、activeへの再有効化、削除を確認画面で確認してください。キー値は画面・監査で取得しません。

反映後に一致していることと、各件の結果・監査を確認します。失敗時は最新一覧を取得し、反映済みの操作を推測して繰り返さないでください。非同期削除待ちは未完了として表示します。ローカルSQLを使う画面の同期はサーバー側で禁止しており、ローカルとAzureのSQLデータを転送・統合する処理はありません。

## ローカル開発と撤去（開発環境）

ローカルSQL・localhostのEntra戻り先・環境別の`portal/runtime.env`の作成とlauncherは[ローカル認証テスト](../testing/portal-local-auth.md)を使います。実装・検証コマンドは[PortalのREADME](../../apps/portal/README.md)を参照してください。

コア基盤全体の`azd down`は同じRG内のPortal DB・初期化ジョブ・Container App・ACRも撤去対象にします。Portalだけの再配備では共有リソースや既存DBを削除しません。

公開版のActionsファイルは無効な `.yml.example` テンプレートです。自身のrunner・Environment・OIDCを設定してから必要なファイルを `.github/workflows/` へコピーします。[設定上の注意](../../.github/workflow-templates/README.md)を参照してください。
