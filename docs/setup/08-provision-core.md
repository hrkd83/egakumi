# 08. azd upで共用基盤を構築する

前：[07. 構築パラメーター](07-params.md) · [README](../../README.md) · 次：[09. Entraに証明書を登録](09-egakumi-portal.md)

第02〜04章で登録したEntraアプリと[公開DNS](05-public-dns.md)、環境別paramsを使い、`azd up`で共用基盤（Core）を作ります。このリポジトリの`azd up`は共用基盤の準備で終了します。`azd provision`でも同じ工程を実行できます。CoreはAPIM本体、Portal用の仮Container Appと、API OBO用・Portal署名用の公開証明書を準備します。証明書のEntra登録、GatewayのAPI公開、Portal本体の配備は第09〜12章で行います。

この章の`azd up`からCore設備の作成と課金が始まります。事前準備で別途作成した公開DNS等の費用は[第05章](05-public-dns.md)で確認します。 [費用の目安](../../README.md#費用を確認する) · [構成と設定項目](../reference/config-options.md)

## 8-1. 実行フォルダと入力を確認する

第01章でcloneしたリポジトリのルート（`azure.yaml`のあるフォルダ）で操作します。新しいターミナルを使う場合は、自社の設定表から第1章の`DEPLOY_*`変数を設定し直してください。

```sh
cd '<任意のクローン先ディレクトリ>'
umask 077
export DEPLOY_LOCAL_DIRECTORY="$(pwd)/.local/${DEPLOY_ENVIRONMENT}"
export DEPLOY_PARAMS_FILE="$DEPLOY_LOCAL_DIRECTORY/params.local.json"
export WORKFORCE_CONFIG="$DEPLOY_PARAMS_FILE"
export BICEP_CLI="${BICEP_CLI:-${AZURE_CONFIG_DIR:-$HOME/.azure}/bin/bicep}"

git status --short --branch
python3 scripts/deployment/lifecycle.py validate
```

`valid: true`、導出された5RG、公開APIの設定状態を確認します。`WORKFORCE_CONFIG`は基盤構築・撤去が読むparamsの入口です。個人用・過去試験用の`.local/<environment>/state/`内の合成設定や、他人のID・URLを含む設定を使いません。

paramsの`services.apimInstanceId`は第1章の初回prepareが公開マーカー`<AUTO_GENERATED_APIM_INSTANCE_ID>`を6文字IDへ置換して保存した値を使います。未解決マーカーは配備入力として受け付けません。第03章で算出したCM戻り先がEntraに保存されていることを確認してください。構築や途中再開で識別子を生成し直すことはありません。撤去後の新規構築は[新しいAPIM識別子を準備](../operations.md#撤去後に新しく構築する)してから、この章を進めます。

標準API正本を常用で解決し、公開`apiDefinitions`・`publishApis`入力は廃止しました。旧項目は空配列やfalseでも拒否し、既存paramsを無断で自動変換しません。[入力配置と移行条件](07-params.md#既存入力の移行を確認する)を確認してから実行します。Coreの初回入力では`portal.publicHostName`を設定し、`portal.authentication`は第09章で追加します。

第01章で作成した環境別paramsを使います。`secrets/`・`state/`・`generated/`は同じ`.local/<environment>/`の下に置き、`.azure/`はazdだけが管理します。まだ秘密や配備記録がないことは正常です。第03章と第04章で用意したTools用とCredential用の各client secretを安全な保管先から非表示入力できる状態にし、第05章と第06章で確認したAzure DNS側Subscription IDとUAMIのリソースIDを用意します。DNS用IDの公開参照はparamsへ事前設定します。Entraのsecretをparams／configへ書き込まず、案内に従って非表示入力します。

## 8-2. Azure CLIとazdの両方へログインする

```sh
az login --tenant "$DEPLOY_AZURE_TENANT_ID"
az account set --subscription "$DEPLOY_SUBSCRIPTION_ID"
az account show --query '{subscriptionId:id,tenantId:tenantId}' -o json

azd auth login --tenant-id "$DEPLOY_AZURE_TENANT_ID"
azd auth login --check-status
```

表示されたTenant／Subscriptionがparamsと一致することを確認します。Azure CLIのログインだけでazdのログインを省略しません。`DEPLOY_LOCATION`はネットワーク等の配置先で、Foundry／API Centerの配置先はparamsの別項目です。

## 8-3. azd環境を作る

初めて使うcloneで、新しいazd環境を作成します。azd環境名はparamsの`environmentName`と同じ値にします。

```sh
azd env new "$DEPLOY_ENVIRONMENT"
azd env set AZURE_TENANT_ID "$DEPLOY_AZURE_TENANT_ID"
azd env set AZURE_SUBSCRIPTION_ID "$DEPLOY_SUBSCRIPTION_ID"
azd env set AZURE_LOCATION "$DEPLOY_LOCATION"
```

同じcloneの作業を再開する場合は、既存環境を確認して`azd env select "$DEPLOY_ENVIRONMENT"`を使います。別環境の値を上書きしません。選択と入力は`.azure/`へ保存されますが、ローカルparams・secretの代わりにはなりません。

### 資源を作らずに追加確認する場合

次の読取検査は任意です。同じ設定で、現在の利用枠とVMイメージ・モデルの提供を確認します。

```sh
python3 scripts/deployment/lifecycle.py capacity
```

結果は`.local/<environment>/state/capacity.json`です。モデルやVMの提供・クォータ不足、読取不能はその理由を確認します。新規Subscriptionで必要RPが未登録の場合も、読取検査が失敗することがあります。構築者の登録権限を確認してください。構築時は必要RPを登録してから内部の検査を進めます。

`management.encryptionAtHost=true`の場合は、`preprovision`で`Microsoft.Compute/EncryptionAtHost`の登録も確認します。未登録なら自動登録し、登録中なら重ねて要求せず待機します。機能登録済みの場合もCompute RPを再登録し、前回の中断などで反映が未完了でも確認してから構築へ進みます。機能登録とRP反映の待機上限は合計30分です。権限不足、未対応の状態、待機超過では構築を停止します。`false`の場合は機能の読取・登録を行いません。`validate`・`capacity`コマンドはこの登録処理を実行しません。

検査の`verified`は、読めた範囲で既知の制限がなかったという結果です。実配備時のサービス容量やMicrosoft側のモデル利用制限まで保証しません。Claudeはazdの構築対象外で、専用のモデル配備・Marketplace確認・RP登録の実装はありません。構築後にGUIで手動追加します。

## 8-4. azd upを実行し、対話入力する

ローカルの通常ターミナルで実行し、対話を有効にします。Coreが正常終了した後に、第09章でEntraへ証明書を登録します。

```sh
export AZD_NON_INTERACTIVE=false
azd up --environment "$DEPLOY_ENVIRONMENT"
echo "終了コード: $?"
```

初回は次の順に案内されます。

1. **端末の検査**：ツール・PowerShell依存と、params／環境の整合を確認。
2. **Azure DNS用IDの確認**：paramsへ事前設定したDNS側Subscription IDとUAMIリソースIDを検査する。不足・不正な参照があれば構築前に停止する。
3. **秘密入力の案内**：対象アプリ・取得元・保存先を表示し、ToolsのCM用とCredentialの本人確認用のclient secretを用途ごとに非表示入力する。DNS用の秘密入力はない。
4. **端末の保護保存**：CM用Core client secretは既定で`.local/<environment>/secrets/entra-client-secret.txt`へ保存し、POSIX権限600とする。同じsecret保存先を使うCM providerが複数ある場合は1回だけ入力する。
5. **SSH鍵**：公開鍵・秘密鍵が両方ない場合だけ、指定した`.local/<environment>/secrets/`へ鍵対を生成。有効な既存鍵は再利用し、秘密鍵はAzureへ送らない。

Core配備の直前には、所有RGと管理VMの正確なGET結果を確認します。VMが未作成の404の場合だけ、`management.adminUsername`のローカル認証用パスワードを生成し、`.local/<environment>/secrets/management-serial-credential.json`へ保護保存します。元の404・生成世代・Core引渡しの証跡は`.local/<environment>/state/management-serial-initialization.json`へ先に保存し、Coreには非秘密の内部世代だけを渡します。公開params/configにパスワードや作成フラグを追加せず、OSProfileへパスワードを渡しません。

Core成功後に、元の世代を持つ配備出力とVM UUID・SAMI・tenantを照合して保護記録へ確定し、公開Managed Run Command資材の名前付き`protectedParameters`だけへ元のパスワードを渡します。guestはSSHの有効設定を確認し、初期ローカル認証とlogin PAMの確認後だけ完了を記録します。SSHのパスワード認証無効と鍵認証は維持します。部分失敗でVMだけ作成済みの場合も、元Core入力とmanagement子配備の同世代・VM出力を照合するまで秘密を読みません。初期化要求が不明になった後の回復は、パスワードを再設定せず、元の資格情報によるPAM認証を最大1回だけ行います。元の初期作成証跡がない既存VMは、この秘密ファイルを読まず、既存パスワードも変更しません。

これはSerial Console用のローカル認証準備です。PAM認証には認証ログやロックアウトへの影響があり、完了記録は実Serial接続の成功を示しません。Managed Run Commandの保護設定やrootプロセスのメモリー等に値が残り得るため、保持がゼロとは扱いません。実ログインの成功は[管理VMの保守手順](../operations.md#serial-consoleのローカル認証を準備する)で別に確認します。

事前チェックは、配備先の一致、必要な秘密情報、追加配備の容量、監査データの保持保護、既存の通信・証明書・稼働アプリの維持を確認します。Azureの応答にある読取メタデータや空の既定値は、実際の設定に影響する項目と分けて扱います。

既存の保護入力とAzure DNS用の公開IDが正常なら再利用します。形式検査だけでsecretの有効性やDNS権限を確認したとは扱いません。DNS用ID・権限の変更は[運用手順](../operations.md#dns用マネージドidと権限を更新する)を参照してください。

### 入力が保存される場所

| 入力 | 対話直後 | 構築後・運用中 |
| --- | --- | --- |
| DNS用公開ID（Azure） | paramsの`certificate.azureDnsSubscriptionId`・`certificate.azureDnsManagedIdentityResourceId` | 証明書用ACA JobからDNS用UAMIを使い、DNS側のRBAC権限で更新 |
| Tools client secret | `credentialProviders.graphDelegated.clientSecretFile`が指す保護ファイル | Graph委任接続のAPIM OAuth providerへsecure入力 |
| Credential client secret | `gateway.authentication.credentialLogin.identity.clientSecretFile`が指す別の保護ファイル | BrokerのOIDC本人確認専用。Key Vault経由で参照 |
| 管理VMのSSH秘密鍵 | 端末のsecrets内 | 端末だけで保持。公開鍵だけをVMへ設定 |
| 初期管理VMのローカル認証パスワード | `.local/<environment>/secrets/management-serial-credential.json`へ自動生成・保護保存 | 初回のsecure入力だけで設定。配備成功後にVM個体へ結び付け、既存VMの更新では上書きしない |
| azdへの一時配備入力 | `.local/<environment>/generated/main.parameters.json`へ保護保存 | 基盤の後処理で回収。再試行には元のparams・secret・stateを使う |

証明書とACMEの更新状態はKey Vaultへ保管します。Azure DNSはUAMIで認証します。

秘密値はparams／config、チャット、Gitへ入力しません。入力した秘密値はKey Vaultだけへ保存される構成ではなく、再実行のために端末の環境別領域にも残ることを理解して管理します。証跡は同じ環境の`evidence/`へ保存し、`.local/`・`.azure/`を公開用のZIP・画像・ログへ含めません。環境名・Tenant／Subscription・stateの対応を実行前に照合し、同じcheckoutの別環境を同時に構築・更新・撤去しません。

`preprovision`は生成JSONを保護保存し、その相対パスだけをazd環境の`EGAKUMI_ARM_PARAMETERS_FILE`へ設定します。公開の`infra/main.bicepparam`をazdがhook後と配備直前に評価して、JSONの値を配備パラメーターへ渡します。秘密値を公開テンプレートへ埋め込んだり、azdの`.env`へ設定したりする必要はありません。通常はこの受渡しを手作業で設定しません。

## 8-5. Coreの完了を確認する

1. 基盤構築コマンドの終了コードが`0`であることと、今回の`postprovision`の成功を確認します。
2. 今回更新された`.local/<environment>/state/latest.json`で、5つのリソースグループ、APIM・Foundry・App Gatewayの出力を対象環境と照合します。同じ環境の`state/public-dns.json`で、Gateway・Portal用Aエイリアスが対象のApp Gateway Public IPに接続されたことを確認します。
3. 今回の証明書Jobの成功を確認し、`.local/<environment>/generated/`に`gateway-obo-public.cer`と`portal-auth-public.cer`が揃ったことを確認します。両ファイルはEntra認証用の公開証明書です。App GatewayのHTTPSリスナーが使うACME証明書はKey Vaultに保存されます。
4. Portal用のContainer AppにはMicrosoftのQuickstartイメージが載っています。この時点ではApp GatewayでPortalバックエンドが異常表示でも問題ありません。公開`/api/health`のHTTP 200は第12章のPortal本体配備後に確認します。

後続の章でも同じ環境のparams・state・公開証明書を使います。同じ環境に対する構築・配備は順に実行します。Coreが完了したら[09. Entraに証明書を登録](09-egakumi-portal.md)へ進みます。

### 基盤構築時の補足

空環境での証明書発行、非公開ACRの通信、途中再開の条件は [証明書用Job](../operations/certificate-jobs.md) を参照します。

管理VMの保守クライアントと担当者本人用のロール定義はCoreの資材で準備し、Gateway／Portalが後段で作成したApp・JobのIDを保護inventoryへ追加します。対象は構築出力から取得し、公開params/configによる作成フラグはありません。[管理VMからの保守操作](../operations.md#管理vmから保守データを取得する)で、監査Blob・保存ログ・ACAログ・App execの認可と実取得を確認してください。資材のローカル試験通過と、初期Azure配備・保守操作の実環境確認は別の結果です。

標準API正本を常用で読み込み、接続先は作成済み基盤の出力からメモリー内で解決します。公開paramsにAPI一覧の選択欄は設けず、内部配列も書き戻しません。標準APIに必要なWAFの経路・本文形式と監査ログ等の情報は基盤工程へ先に反映します。

`postprovision`は、第05章で準備したAzure Public DNSゾーンのGateway・Portal用Aエイリアスを、対象環境のAppGW Public IPリソースへ接続します。作成したAエイリアスは同じ環境の`state/public-dns.json`で照合します。

API Centerの同名削除履歴が残る場合は、5RG作成後に対象環境のsvc RG内の同名1件だけを所有照合して復元し、Bicepを再適用します。確認不能なら停止します。個別に別名の資源を追加しません。

APIMのドメイン・基盤更新は15分以上かかる場合があります。Developerでは更新中の通信も一時中断し得ます。コマンドを再起動せず、同じ更新の完了を待ちます。[Microsoftの更新仕様](https://learn.microsoft.com/azure/api-management/configure-custom-domain)

監視通知先の初回登録では、Azureから届く確認メールを**送信から30分以内**にリンクとOTPで確認します。構築完了を待たずに行ってください。OTPは端末・paramsへ入力しません。期限切れならAzure Portalの対象Action Groupの「概要」→「通知」→「再送信」を使い、確認済み状態とテスト通知の実受信を別々に確認します。

ARMのemail receiverの`status: Enabled`は、メールアドレスの確認完了を示す値ではありません。Azure Portalで受信先の確認状態を確認してからテスト通知を送り、要求の受付・Azureの送信完了・受信箱への実到達を区別してください。受付に失敗した要求を、送信済みとして案内しません。

## 失敗した場合

エラーが出た段階・配備名・エラーコード・correlation IDを控え、秘密値を含めず原因を確認します。失敗した配備やAzure側の利用制限は、同じコマンドを繰り返す前に対象工程の配備状態を確認します。既存モデルの更新失敗では、旧モデルが稼働している可能性もあるため、状態だけで自動削除しません。

失敗した工程の終了コードを確認し、原因を修正してから次の工程へ進みます。作成済みの基盤と記録を保持し、Coreで失敗した場合は同じ環境の入力で`azd up`を再試行します。

途中まで作成された資源にも費用が発生します。撤去する場合は[同じparamsでのdownとstate不足時の扱い](../operations.md)へ進みます。構築したcloneの`.azure/`・`.local/<environment>/state/`と設定を消してから撤去を始めないでください。
