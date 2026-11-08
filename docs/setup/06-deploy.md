# 6. azd upでの構築

前：[5. configの調整](05-config.md) · [README](../../README.md) · 次：[7. ハブ＆スポーク対応（任意）](07-hub-spoke.md) · [更新・撤去](../operations.md)

## このページのゴール

新規クローンに自社のparamsと、Entra・DNS担当者が準備した資格情報を与え、1回の`azd up`で共通基盤、Gateway、Portalまで順に構築します。コマンドの正常終了、公開DNSの反映、APIの実応答とPortalへのサインインを分けて確認します。

**このページの`azd up`からCore設備の作成と課金が始まります。** 公開DNS等、事前準備で別途作成した資源の費用は第3章で確認します。 第1〜5章の準備・料金・保持方針を確定してから実行してください。使い終えた検証環境は[撤去手順](../operations.md#撤去する)で削除します。

## 6-1. 実行フォルダと入力を確認する

第1章でcloneしたリポジトリのルート（`azure.yaml`のあるフォルダ）で操作します。新しいターミナルを使う場合は、自社の設定表から第1章の`DEPLOY_*`変数を設定し直してください。

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

`valid: true`、導出された5RG、公開APIの設定状態を確認します。`WORKFORCE_CONFIG`はup/downが読むparamsの入口です。個人用・過去試験用の`.local/<environment>/state/`内の合成設定や、他人のID・URLを含む設定を使いません。

全体構築には`publishApis:true`が必要です。test・productionの公開例はtrueですが、既存のlocal paramsは自動変更しません。falseのまま`azd up`を実行すると、`preup`の冒頭で課金資源の作成前に停止します。基盤だけを準備する場合は、同じ入力で`azd provision`を使います。

第1章で作成した環境別paramsを使います。`secrets/`・`state/`・`generated/`は同じ`.local/<environment>/`の下に置き、`.azure/`はazdだけが管理します。まだ秘密や配備記録がないことは正常です。第2章のCore client secretと、第3章のDNS資格情報は、必要な方式だけ安全な管理場所から入力できる状態にします。標準のDNS／Entra入力では、JSONへsecretを書き込んだり、secretファイルを手作業で作成したりしません。既存PFXを使う`prepared`方式だけは、第5章の例外手順で同じ環境の`secrets/`へ保護PFX／パスワードファイルを事前準備します。

## 6-2. Azure CLIとazdの両方へログインする

```sh
az login --tenant "$DEPLOY_AZURE_TENANT_ID"
az account set --subscription "$DEPLOY_SUBSCRIPTION_ID"
az account show --query '{subscriptionId:id,tenantId:tenantId}' -o json

azd auth login --tenant-id "$DEPLOY_AZURE_TENANT_ID"
azd auth login --check-status
```

表示されたTenant／Subscriptionがparamsと一致することを確認します。Azure CLIのログインだけでazdのログインを省略しません。`DEPLOY_LOCATION`はネットワーク等の配置先で、Foundry／API Centerの配置先はparamsの別項目です。

## 6-3. azd環境を作る

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

結果は`.local/<environment>/state/capacity.json`です。モデルやVMの提供・クォータ不足、読取不能はその理由を確認します。新規Subscriptionで必要RPが未登録の場合も、読取検査が失敗することがあります。構築者の登録権限を確認してください。通常の`azd up`は必要RPを登録してから内部の検査を進めます。

`management.encryptionAtHost=true`の場合は、`preup`と`preprovision`の共通事前処理で`Microsoft.Compute/EncryptionAtHost`の登録も確認します。未登録なら自動登録し、登録中なら重ねて要求せず待機します。機能登録済みの場合もCompute RPを再登録し、前回の中断などで反映が未完了でも確認してから構築へ進みます。機能登録とRP反映の待機上限は合計30分です。権限不足、未対応の状態、待機超過では構築を停止します。`false`の場合は機能の読取・登録を行いません。`validate`・`capacity`コマンドはこの登録処理を実行しません。

検査の`verified`は、読めた範囲で既知の制限がなかったという結果です。実配備時のサービス容量やMicrosoft側のモデル利用制限まで保証しません。Claudeはazdの構築対象外で、専用のモデル配備・Marketplace確認・RP登録の実装はありません。構築後にGUIで手動追加します。

## 6-4. azd upを実行し、対話入力する

ローカルの通常ターミナルで実行し、対話を有効にします。CI用の非対話・`--no-prompt`を付けません。

```sh
export AZD_NON_INTERACTIVE=false
azd up --environment "$DEPLOY_ENVIRONMENT"
echo "終了コード: $?"
```

初回は次の順に案内されます。

1. **端末の検査**：ツール・PowerShell依存と、params／環境の整合を確認。
2. **DNS事業者の選択**：Cloudflare／Route53／Azure Public DNSを選択。Enterはparamsの現在値を維持し、選んだ非秘密のplugin名をparamsへ保存する。Azureでは不足するDNS側Subscription IDと事前作成済みUAMIのリソースIDも入力し、非秘密の参照としてparamsへ保存する。事業者を変更したらFirewallの宛先も読み直す。
3. **秘密入力の案内**：必要な値・対象アプリ・取得元・保存先を短く表示。「Enterで入力欄へ進む」で進み、設定したAPIに必要なCM用Core client secret、Cloudflare／Route53のDNS資格情報を非表示入力する。Azure DNS用の秘密入力はない。
4. **端末の保護保存**：必要な秘密入力だけを保存する。既定ではCloudflare／Route53のDNS資格情報は`.local/<environment>/secrets/dns-plugin-arguments.json`、CM用Core client secretは`.local/<environment>/secrets/entra-client-secret.txt`へ保存し、POSIX権限600とする。同じsecret保存先を使うCM providerが複数ある場合は1回だけ入力する。Azure DNSの資格情報ファイルは作成しない。
5. **SSH鍵**：公開鍵・秘密鍵が両方ない場合だけ、指定した`.local/<environment>/secrets/`へ鍵対を生成。有効な既存鍵は再利用し、秘密鍵はAzureへ送らない。

基盤とGatewayの構築が終わると、最後のPortal工程でAPIキー所有グループの設定を入力します。未設定の`groupNamePattern`・基盤管理グループのObject ID・承認製品と購読数上限を、同じparamsへ保存します。この入力の完了まで`azd up`の完了にはなりません。非対話で初期構築する場合は、`portalApiKeys.groups`・`platformGroupId`・`groupNamePattern`を事前に設定してください。

事前チェックは、配備先の一致、必要な秘密情報、追加配備の容量、監査データの保持保護、既存の通信・証明書・稼働アプリの維持を確認します。Azureの応答にある読取メタデータや空の既定値は、実際の設定に影響する項目と分けて扱います。

既存の保護入力が正常なら再利用します。DNS事業者を変更した場合は、古い事業者の資格情報を流用せず、新しい入力を検査して置き換えます。同じ事業者での資格情報の失効・有効期限は、形式検査だけでは検出できません。更新方法は[資格情報の更新](../operations.md#dns資格情報を更新する)を参照してください。

### 入力が保存される場所

| 入力 | 対話直後 | 構築後・運用中 |
| --- | --- | --- |
| DNS資格情報（Cloudflare／Route53） | paramsが指すGit管理外の保護ファイル | 管理VMがCore Key Vaultの`<certificateName>-dns-plugin-args`へ登録。証明書の定期更新時に利用 |
| DNS用公開ID（Azure） | paramsの`certificate.azureDnsSubscriptionId`・`certificate.azureDnsManagedIdentityResourceId` | 管理VMにUAMIを関連付け、そのIDで認証しDNS側のRBAC権限で更新。DNS資格情報ファイルや`<certificateName>-dns-plugin-args`は作成・利用しない |
| CM用Core client secret | paramsが指すGit管理外の保護ファイル | APIMのOAuth providerへsecure入力 |
| 管理VMのSSH秘密鍵 | 端末のsecrets内 | 端末だけで保持。公開鍵だけをVMへ設定 |
| PortalのAPIキー所有設定 | 同じparamsの`portalApiKeys` | PortalのSQL初期化へ渡す非秘密のグループID・製品承認 |
| azdへの一時配備入力 | `.local/<environment>/generated/main.parameters.json`へ保護保存 | 基盤の後処理で回収。再試行には元のparams・secret・stateを使う |

DNS-01方式では、証明書とACMEの更新状態をDNS事業者によらずKey Vaultへ保管します。Azure DNSではDNS資格情報secretが存在しなくても正常です。

秘密値はparams／config、チャット、Gitへ入力しません。入力した秘密値はKey Vaultだけへ保存される構成ではなく、再実行のために端末の環境別領域にも残ることを理解して管理します。証跡は同じ環境の`evidence/`へ保存し、`.local/`・`.azure/`を公開用のZIP・画像・ログへ含めません。環境名・Tenant／Subscription・stateの対応を実行前に照合し、同じcheckoutの別環境を同時に構築・更新・撤去しません。

`preup`・`preprovision`は生成JSONを保護保存し、その相対パスだけをazd環境の`EGAKUMI_ARM_PARAMETERS_FILE`へ設定します。公開の`infra/main.bicepparam`をazdがhook後と配備直前に評価して、JSONの値を配備パラメーターへ渡します。秘密値を公開テンプレートへ埋め込んだり、azdの`.env`へ設定したりする必要はありません。通常はこの受渡しを手作業で設定しません。

## 6-5. 構築の進行を確認する

`azd up`は次の処理を続けて行います。途中で同じコマンドを並行実行しません。

| 順序 | 実行する内容 |
| --- | --- |
| 1 | Tenant／Subscription・予定APIM名・容量・購入制限を確認し、必要RPを登録 |
| 2 | 5RG、用途別ID、監視・監査先を作成。実IDをローカルのstateへ記録 |
| 3 | VNet・subnet・NSG・UDR・Firewall・Private DNS、サービス・PE・RBACを作成 |
| 4 | 管理VMでDNS-01の証明書を取得し、Key Vaultへ登録。AppGW・APIMの公開設定を仕上げる |
| 5 | API Center同期とgRPC入口を設定し、`postprovision`で対象配備のCore出力と公開DNSの結果を保存 |
| 6 | 対話可能な`postup`から`deploy_applications.py --execute`を呼び、Gatewayのイメージ、credentials DB・CM・SQL初期化、認証窓口、API・ポリシー・Productを配備 |
| 7 | Gatewayの成功後、Portalの初回グループ設定、イメージ、認証DB・検証用購読・SQL権限を準備し、本体・照合ジョブを配備。公開ヘルスを確認して全体を完了 |

`apiDefinitions: []`は標準APIを使う指定です。接続先は作成済み基盤の出力からメモリー内で解決し、paramsの配列は書き換えません。標準APIに必要なWAFの経路・本文形式と監査ログ等の情報は基盤工程へ先に反映します。

Azure Public DNS以外でAレコードを手動管理する場合は、`postprovision`で対象環境のAppGW Public IPが分かった時点で、第3章の手順に従ってGateway・Portalの両ホストを設定します。Portal工程は公開URLのヘルス確認を行うため、その工程までに名前解決が必要です。反映が間に合わず停止した場合は、DNSを確認してPortalの個別コマンドから再試行します。

API Centerの同名削除履歴が残る場合は、5RG作成後に対象環境のsvc RG内の同名1件だけを所有照合して復元し、Bicepを再適用します。確認不能なら停止します。個別に別名の資源を追加しません。

APIMのドメイン・基盤更新は15分以上かかる場合があります。Developerでは更新中の通信も一時中断し得ます。コマンドを再起動せず、同じ更新の完了を待ちます。[Microsoftの更新仕様](https://learn.microsoft.com/azure/api-management/configure-custom-domain)

監視通知先の初回登録では、Azureから届く確認メールを**送信から30分以内**にリンクとOTPで確認します。構築完了を待たずに行ってください。OTPは端末・paramsへ入力しません。期限切れならAzure Portalの対象Action Groupの「概要」→「通知」→「再送信」を使い、確認済み状態とテスト通知の実受信を別々に確認します。

ARMのemail receiverの`status: Enabled`は、メールアドレスの確認完了を示す値ではありません。Azure Portalで受信先の確認状態を確認してからテスト通知を送り、要求の受付・Azureの送信完了・受信箱への実到達を区別してください。受付に失敗した要求を、送信済みとして案内しません。

## 6-6. 構築完了と公開DNSを確認する

1. `azd up`の終了コードが`0`で、GatewayとPortalの両工程が完了したことを確認する。`postprovision`の`deployed: true`は基盤工程の結果であり、それだけを全体の成功と扱わない。
2. **実行した配備で更新された**`.local/<environment>/state/latest.json`で、Subscription／resourceGroups／実出力を確認する。古いstateを配備成功の証拠にしない。
3. Azure Portalで5RG、APIM／Foundry／AppGW等の作成状態、選択したモデルの配備成功を確認する。
4. `.local/<environment>/state/latest.json`の`outputs.gateway.publicIpId`・`outputs.gateway.publicIp`を、PortalのそのPublic IPと照合する。
5. Azure Public DNSでは、`.local/<environment>/state/public-dns.json`でGateway・PortalのAエイリアスが**対象環境のAppGW Public IPリソース**へ接続されたことを確認する。その他のDNS事業者では第3章の手順で両ホストを設定する。CloudflareはDNS-onlyを使用する。
6. GatewayのFQDNで、TLS証明書のSAN・信頼チェーン・有効期限を確認する。native gRPCを使う場合はconfigで選択した追加TCPポートでも確認する。
7. 実行した配備で更新された`.local/<environment>/state/gateway-deployment.json`の`status: deployed`と、`.local/<environment>/state/portal-deployment.json`の`status: deployed`・`publicHealthVerified: true`を確認する。過去の成功結果が残っているだけの場合と区別する。
8. 正規の購読キーと必要な本人セッションでGatewayの実応答を確認し、PortalのMicrosoftサインインと管理画面も確認する。初期構築後に同じ配備コマンドを重ねて実行する必要はない。

DNS-01は一時TXTを使うため、AレコードをAppGWへ切り替える前でも証明書を取得できます。DNS検証が必要な場合はACMEの検証処理が一時TXTを作成・回収します。CAの認証を再利用する場合は、更新時に新しいTXTを作らないこともあります。GatewayのAやEntraのドメイン確認TXTと混同しません。

`azd provision`だけを実行した場合は、Gateway・Portalの配備は未実行です。モデルが作成されただけではGateway経由で呼べないため、[第4章のAPI登録](04-params.md#基盤作成とapi利用を分けて選ぶ)と、以下の個別配備手順を確認します。

### GatewayとPortalの個別更新・再試行

構築済み環境の更新や途中失敗からの再開では、同じparamsとCoreの`.local/<environment>/state/latest.json`を維持して必要な個別コマンドを使います。Gatewayが失敗した場合は修正後にGatewayを再試行し、成功してからPortalへ進みます。Portalだけが失敗した場合は、その工程だけを再試行できます。企業接続を途中に挟む段階構築では、`azd provision`で基盤を作り、[7. ハブ＆スポーク対応](07-hub-spoke.md)を済ませてから個別に配備します。

Gatewayの計画確認と反映は次のとおりです。

```sh
python3 scripts/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE"
python3 scripts/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE" --execute
```

1行目は計画のみです。2行目は既存管理VMでイメージを作成して非公開ACRへ登録し、credentials DB・CM・SQL初期化ジョブ、本体、定期照合ジョブ、APIの順に配備します。PC側のDockerやActions runnerは不要です。ビルド時だけ管理VMへACRスコープの組み込み`AcrPush`を割り当て、終了時に解除します。SQL初期化失敗時は本体更新・API登録に進みません。詳細と必要権限は[Gateway配備手順](credential-sql.md#4-gatewayを配備する)を参照してください。

`.local/<environment>/state/gateway-deployment.json`の完了結果を確認したら、公開APIの疎通試験へ進みます。paramsは、このコマンドでは編集しません。初回の認証窓口用共有鍵は保護されたローカルファイルへ生成し、Key Vaultへ登録します。

Portalの計画確認と反映は次のとおりです。Gatewayの製品登録後に実行し、SQL初期化・起動・公開ヘルスの確認まで待ちます。詳細は[Portal配備手順](portal.md)を参照してください。

```sh
python3 scripts/deploy_portal.py --config "$DEPLOY_PARAMS_FILE"
python3 scripts/deploy_portal.py --config "$DEPLOY_PARAMS_FILE" --execute
```

基盤を再配備せず両アプリを順に更新する場合は、`python3 scripts/deploy_applications.py --config "$DEPLOY_PARAMS_FILE" --execute`も使えます。どの入口でも実行したコマンドの終了コードを確認し、失敗した工程以降へ進みません。

## 失敗した場合

エラーが出た段階・配備名・エラーコード・correlation IDを控え、秘密値を含めず原因を確認します。Failed配備やAzure側の利用制限を、同じupの繰返しだけで解消する前提にしません。既存モデルの更新失敗では、旧モデルが稼働している可能性もあるため、状態だけで自動削除しません。

Gatewayに失敗するとPortalへ進まず、Portalに失敗した場合もup全体は非0で終了します。作成済み基盤や完了済みのアプリを自動で撤去せず、上の個別コマンドで再開できます。設定・`.local/<environment>/state/latest.json`・各配備記録を消さず、同じ環境の入力を使います。

途中まで作成された資源にも費用が発生します。撤去する場合は[同じparamsでのdownとstate不足時の扱い](../operations.md)へ進みます。構築したcloneの`.azure/`・`.local/<environment>/state/`と設定を消してから撤去を始めないでください。

## 完了条件と次の作業

- up全体の正常終了、実行したGateway・Portal配備の成功記録、Azure側の作成状態、公開DNS／TLSを確認した。
- 選んだAPIの実応答とPortalのMicrosoftサインインを、配備成功とは別に確認した。
- 通知先を使う場合は初回確認と実受信を確認した。
- 管理VMの証明書更新設定と、定期更新のDNS認証を確認した。AzureはUAMIの関連付け・DNS側RBAC権限、Cloudflare／Route53はKey VaultのDNS資格情報を確認する。
- params／config・`.azure/`・`.local/<environment>/state/`・端末のsecretとSSH鍵を、安全に保持した。

企業接続が必要なら[7. ハブ＆スポーク対応（任意）](07-hub-spoke.md)へ進みます。構築済みGateway・Portalの変更は[個別配備](../../README.md#8-gatewayを配備する)を使い、運用時は[プラットフォームの仕様](../reference/platform.md)も確認します。構築テストが終わったら[撤去手順](../operations.md#撤去する)へ進み、5RG・モデル・利用枠と削除履歴を確認します。
