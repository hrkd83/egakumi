# 07. paramsを完成させる

Core初回構築では、`portal.publicHostName`を設定し、`portal.authentication`は省略します。公開サンプルもこの形です。`azd up`で専用ACA Jobが証明書を準備した後、Entra担当が専用アプリを作成して公開証明書を登録し、`portal.authentication.tenantId`・`clientId`を環境別の保護paramsへ追加します。鍵版とthumbprintは同じ環境の保護stateから読みます。 [08. Core構築](08-provision-core.md) · [09. Core構築後のEntra登録](09-egakumi-portal.md)

前：[06. DNS用マネージドIDの準備](06-dns-identity.md) · [README](../../README.md#新規クローンから構築する) · 次：[08. Coreを構築する](08-provision-core.md)

## このページのゴール

第01章のひな型へ、導入先のID・名前・IP計画・公開名・連絡先と、第02〜06章で準備したEntraとDNSの設定を記入します。このページではAzure資源を作りません。設定欄の詳細は[paramsリファレンス](../reference/configuration/parameters.md)を参照してください。

## 7-1. paramsとconfigの役割

| 場所 | 管理するもの |
| --- | --- |
| `.local/<environment>/params.local.json` | 導入先ごとのID・公開名・IP計画・連絡先、アプリClient IDと秘密ファイル参照 |
| `infra/config/test.json`／`infra/config/production.json` | 構築資材が提供する検証・本番の標準値。初回構築ではそのまま参照する |
| `.local/<environment>/secrets/` | 対話入力や保護された鍵生成で保存する秘密ファイル |
| 資材のコード・API正本 | 固定の通信・認証契約、標準API、params/configから導出する内部設定 |

ファイル参照は**そのJSONのあるフォルダーを基準に解決**します。準備コマンドは選択したprofileの参照をparamsへ設定します。生成先ではconfigを`../../infra/config/test.json`または`../../infra/config/production.json`、秘密を`./secrets/...`で参照します。`.local/`と`.azure/`はチェックアウト直下だけに置き、リンクと所有者専用権限を確認します。実値を公開example・コード・作業記録へ書き込まず、秘密値をJSONへ直接記載しません。

エディター向けのSchemaは、[公開paramsの例](../../params/)の隣に[params用](../../params/params.schema.json)、[標準config](../../infra/config/)の隣に[config用](../../infra/config/options.schema.json)を置いています。[configuration schema](../../infra/config/configuration.schema.json)は、API配列・完全なprovider・導出値を含む内部合成形式の参照です。内部形式の項目を、そのまま公開params/configへ追加することはできません。

準備済みparamsが移設前の標準configを参照している場合も、準備コマンドは入力を保持し、配備時は同じprofileを読み込みます。エディターの参照を更新するときは、paramsの `$schema` に `../../params/params.schema.json` を指定します。

## 7-2. 環境別paramsと自動生成IDを確認する

第1章の[準備コマンド](01-prerequisites.md#1-5-環境別のローカル領域を準備する)で、選んだ公開exampleからparamsを作成します。本番向けの標準構成は`--profile production`を選びます。環境名は任意で、SKUの選択とは独立です。

```sh
export DEPLOY_LOCAL_DIRECTORY="$(pwd)/.local/${DEPLOY_ENVIRONMENT}"
export DEPLOY_PARAMS_FILE="$DEPLOY_LOCAL_DIRECTORY/params.local.json"
export WORKFORCE_CONFIG="$DEPLOY_PARAMS_FILE"
test -f "$DEPLOY_PARAMS_FILE"
```

公開例の`services.apimInstanceId`は`<AUTO_GENERATED_APIM_INSTANCE_ID>`です。これはテンプレートの初回自動生成マーカーであり、固定の配備IDではありません。`prepare_local_environment.py`が初回に英小文字・数字6文字のIDへ置換し、paramsへ保存します。通常の再実行は既存params全体と保存済みIDを保持します。`azd up`、登録、更新、撤去の読込時にIDを再生成しません。未解決マーカーや不正な既存IDは検証で停止し、暗黙に修復しません。

APIM名・用途別WAF名・EntraのCM戻り先・ライフサイクル所有確認は同じ保存済みIDを使います。撤去後の新規構築だけは[新しい識別子の準備](../operations/teardown.md#撤去後に新しく構築する)とCM戻り先更新を先に行います。

公開例のGUID・ホスト・CIDR・会社名・メールは実値から独立した架空例です。導入先の値は生成した保護paramsに記入します。既存paramsへ公開例を重ねてコピーせず、別環境の入力・state・秘密も自動流用しません。

## 7-3. 環境の値を記入する

1. `subscriptionId`・`azureTenantId`・`entraTenantId`・製品／環境名・3リージョンを確定する。
2. `network.addressSpaces`と`network.subnetPrefixes`をIP計画へ合わせる。公開例のCIDRが未使用だと推測しない。
3. Gateway／Portalの公開FQDN、APIM発行者、監視通知先、ACME連絡先を記入する。証明書DNS名は公開FQDN2件から自動生成する。
4. 第05章と第06章で確認したDNS側Subscription IDとUAMIの完全なResource IDを設定する。DNS-01プラグインは資材でAzureへ固定する。
5. `management.adminUsername`とSSH公開鍵参照を設定する。保守時の本人権限は[管理VMの運用](../operations/management-vm-access.md)に従う。
6. `credentialProviders.graphDelegated.clientId`にToolsアプリのClient IDを設定し、`clientSecretFile`の保護参照を確認する。
7. `gateway.authentication.entra.audience`にAPIアプリのClient IDを設定する。`gateway.authentication.credentialLogin.identity`にはCredentialアプリの`clientId`と、Tools用とは異なる`clientSecretFile`を設定する。

### 基盤用Entraアプリの登録情報を記入する

| 役割 | paramsに置く値 |
| --- | --- |
| API | `gateway.authentication.entra.audience` |
| Tools | `credentialProviders.graphDelegated.clientId`と`clientSecretFile` |
| Credential | `gateway.authentication.credentialLogin.identity.clientId`と`clientSecretFile`。Tools用とは別のファイル |
| Portal | 公開証明書登録後に`portal.authentication.tenantId`と`clientId`を追加 |

すべてのアプリでApplication (client) IDを使い、認証用`entraTenantId`に登録します。API試用はこの基盤構成で既定有効です。試用をメンテナンスで一時停止する場合は`portal.apiAuthentication.enabled`を`false`にしてPortalだけ再配備します。通常のOBO初回は保護stateの証明書を固定します。管理者が別版を発行・Entra登録済みの場合だけ、`gateway.authentication.entra.obo.keyId`と公開証明書DERのSHA-256の`certificateThumbprint`を組で指定します。

### 同一テナントと別テナントの指定

`azureTenantId`はAzure構築・RBAC先、`entraTenantId`は社員API認証、標準Graph OAuth、任意の接続窓口アプリの認証先です。本文監査を有効にする標準configでは2つのテナントを揃えます。`entraTenantId` を省略すると `azureTenantId` を使います。別テナントで構築する場合は、本文監査を無効にした個別構成でそれぞれのIDを指定します。標準Graphの`oauthTenantId`と接続窓口identityの`tenantId`は公開入力から導出し、独立した上書き欄を設けません。

追加providerが別の認証テナントを使う場合は、`params.credentialProviders.additional[].oauthTenantId`を設定できます。管理VMに接続する担当者へは、保守時に [本人のAzure RBAC](../operations/management-vm-access.md) を付与して回収します。認証先の変更と、Azure配置先の変更は別の操作です。

### Gateway全体の認証とAPIごとの認可

購読キーヘッダーは`api-key`、社員委任scopeは`Gateway.Invoke`で資材固定です。キーあり要求はAPIMの購読検証、キーなしBearer要求はEntra JWT検証へ進みます。対象はAPI正本から求め、認証方式の切替、許可Client ID一覧、対象API一覧をparamsへ入力しません。APIの`subscriptionRequired:true`と専用open ProductによるBearer経路を構成します。

公開paramsに置くのはAPIアプリの`audience`です。`userLimits`はconfigに置き、標準configの要求回数はユーザー単位で60秒あたり1,000件です。トークン単位の制限は既定で無効です。例外発行の購読キーには共通の利用上限を設定しません。

```json
{
  "gateway": {
    "authentication": {
      "entra": { "audience": "<API_APP_CLIENT_ID>" }
    }
  }
}
```

共通の制限値と変更時の確認は[標準config](../reference/configuration/config-options.md)を参照してください。

共通Named Valuesのtenant／audienceはparams、scopeは固定資材、userLimitsはconfigから内部生成します。手入力欄やNamed Valuesの手動変更を増やしません。API正本・policyの読戻し、revision、Product所属、キー範囲と復旧の確認は[Entraアプリの役割](../reference/identity/entra-apps.md)と[追加受入項目](../testing/integration-tests.md#entra-gateway-oauthと専用open-productの追加受入)に従います。

### 基盤作成とAPI利用を分けて選ぶ

標準APIは[API正本](../../apps/api-gateway/apis/)から解決します。解決したAPI配列や環境固有URLをparamsへ書き戻しません。

| 入口 | 実行範囲 |
| --- | --- |
| `azd up`（`azd provision`と同じ） | CoreとPortal初期サンプル、認証用公開証明書2枚を用意する |
| 個別配備コマンド、`--execute`なし | 計画を確認する |
| 個別配備コマンド、`--execute`あり | 指定したアプリの反映を実行する |

## 7-4. ローカルで設定を検査する

```sh
python3 -B scripts/deployment/helpers/configuration.py validate --config "$DEPLOY_PARAMS_FILE"
```

未知項目・ID形式・IP重複・認証の組合せ等を検査します。secret値、Azure権限、実モデルの配備可否、通信の受入はこの検査で確認しません。秘密ファイルがない場合は、正式手順の保護された対話入力を使います。

## 次へ進む前の確認

- 導入先ID・公開名・IP計画・連絡先とアプリ登録を保護paramsに設定した。
- 初回生成済みAPIM IDが6文字で、未解決マーカーが残っていない。
- secret値をJSONへ記載せず、明示した保護参照を確認した。
- 参照したconfigと資材の固定契約を確認した。
- ローカル入力検証と、Azure反映・実接続受入を区別した。

次：[08. Coreを構築する](08-provision-core.md) · [標準configのリファレンス](../reference/configuration/config-options.md)
