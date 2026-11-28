# 環境の撤去と再構築

[対象環境と記録](common.md) · [構築手順](../setup/08-provision-core.md) · [撤去処理](../../scripts/deployment/helpers/cleanup.py)

## 撤去する

対象環境の利用を終了するときに実行します。実行中の音声セッション等を停止し、構築時のparams・config・`.azure/`・`.local/<environment>/state/latest.json`、監査保持とDB／必要データの保管を確認します。

`azd down`では、azd本体のRG削除確認より前に`predown` hookがモデル・APIM・アプリ・管理VMの削除を開始します。対象Tenant／Subscription・環境名・所有5RGは、コマンド実行前に照合します。

```sh
export WORKFORCE_CONFIG="$DEPLOY_PARAMS_FILE"
AZD_NON_INTERACTIVE=false azd down --environment "$DEPLOY_ENVIRONMENT"
```

上の指定はこのコマンドだけ対話を有効にします。表示された所有RGの削除確認には対象を照合して`Y`を入力します。`predown`はその確認より先に削除を始めるため、実行前の対象照合は省略しません。非対話の既定回答で停止した場合も、作成済みの先行回収記録を保持して同じ入力から再開します。

azd本体でKey Vault・Foundryの完全削除を確認された場合は`N`を選びます。続くCoreの`postdown`が対象IDと保護設定を照合します。

Coreの`postdown`は`--purge`の有無にかかわらず、対象のKey Vault・Foundry・APIMの論理削除記録を照合して、保護設定が許すものを完全削除します。Key Vaultのpurge protectionや監査WORMを解除する指定ではありません。

azd本体の撤去も`infra/main.bicepparam`を評価するため、`predown`は同じ環境の`generated/main.parameters.json`へ秘密なしの撤去用入力を再生成します。azd環境の`EGAKUMI_ARM_PARAMETERS_FILE`にはその相対パスだけを保持し、秘密値を`.env`へ追加しません。生成入力はazd本体の撤去が終わるまで保持し、`postdown`で回収します。再試行に必要な元のparams・secret・stateは消しません。

| 段階 | 処理・完了確認 |
| --- | --- |
| 変更前 | stateと現物のID・種類・命名・region・所有タグ・接続先、全監査保持を照合し、ACA管理RGの実IDを保存する |
| `predown` | モデル→CMの接続権限・接続・provider→APIM→Broker・全ジョブ→Portal CA→Portal用CAE→Portalサブネット解放→管理VM・残存する所有NICの順で回収する |
| azd本体 | 所有5RGを削除する。専用DB・ACR／イメージ・Vault秘密・専用IDも所有RGと共に回収する |
| `postdown` | 5RGと記録済みACA管理RGの404、Key Vault・Foundry・APIMの削除記録と保護による保持を記録する |

Gateway／Portalの配備成功記録がなくても、固定IDと実資源の所有・接続先を照合します。全アプリ・ジョブの不存在を確認してからCAEを削除し、サブネットの解放を待ちます。ACAの`infrastructureResourceGroup`はAzure側の削除を待ち、名前で`ME-*`を探索・直接削除しません。構築時・撤去時に記録した管理RGを確認します。

Locked WORMがある場合は変更前に停止します。Unlockedでも構成の監査保持が有効なら解除しません。Key Vaultのpurge protectionは期限まで保持します。環境名だけで削除保護を切り替えません。外部DNSゾーン・DNS用ID・Entra登録・同意・企業ハブは撤去対象に広げません。

結果は`.local/<environment>/state/cleanup-predown.json`と`.local/<environment>/state/cleanup-postdown.json`に保存されます。成功・未完了・保護による保持・確認不能を区別し、保持・残存があるものを完全消去済みとは扱いません。

### 撤去後に新しく構築する

同じ製品名・環境名で新しく構築するときは、次の順でAPIM識別子とEntraのCM戻り先を更新します。構築途中の再試行や、稼働中の環境更新では識別子を保持します。

1. 同じ環境の`cleanup-postdown.json`で、旧APIMと関連する旧環境の稼働資源が撤去済みであることを確認する。保持資源・削除履歴・内部accountの未確認は、それぞれの記録で区別する。
2. [前提準備](../setup/01-prerequisites.md)で選んだ`DEPLOY_PROFILE`を設定し、次のコマンドを実行する。コマンドは対応する撤去記録を検査してから、paramsの`services.apimInstanceId`を更新する。旧stateは`.local/<environment>/history/apim/<旧識別子>/state/`へ保管し、通常の`state/`は新しい構築用に空にする。paramsの他の設定とsecretは保持する。

   ```sh
   python3 -I -B scripts/setup/prepare_local_environment.py \
     --environment "$DEPLOY_ENVIRONMENT" --profile "$DEPLOY_PROFILE" \
     --new-apim-instance
   python3 -I -B scripts/setup/prepare_registration.py --config "$DEPLOY_PARAMS_FILE"
   ```

3. [ToolsのWeb戻り先の確認](../setup/03-entra-tools.md#cmのweb戻り先を確認する)に従い、その環境のCM用Web戻り先1件を新しいURIへ置き換え、保存後に確認する。Gateway・Portalの公開名を継続する場合は、その2件の戻り先、アプリの権限・同意、有効なsecretを保持する。
4. 同じparams・azd環境で[構築手順](../setup/08-provision-core.md)を進める。撤去済みの識別子のまま、新規構築を開始しない。

APIMと用途別WAFポリシーが、新しい共通の識別子を使います。環境名、公開ホスト名、その他の資源名は維持します。

### CM内部アカウントの解放を確認できない場合

CMの正規の撤去経路は、接続権限→接続→provider→APIM本体→削除履歴のpurgeです。この経路は撤去hookで実行します。[provider削除](https://learn.microsoft.com/rest/api/apimanagement/authorization-provider/delete?view=rest-apimanagement-2024-05-01)と[APIMのpurge](https://learn.microsoft.com/rest/api/apimanagement/deleted-services/purge?view=rest-apimanagement-2024-05-01)は公開APIですが、CMが背後に作る[credential store](https://learn.microsoft.com/azure/api-management/credentials-process-flow#credential-provider)の内部accountを直接削除・解放確認する公開操作は確認できていません。

providerのDELETE成功・一覧不在・親APIMの404・削除履歴の404だけで、内部accountの解放や同名CMの再構築成功を判定しません。CM採用構成の`postdown`は、`status: resources-absent-with-unverified-credential-manager-release`を返します。公開資源・削除履歴の結果は`resourceCleanupStatus`に、内部accountの未確認は`credentialManager.internalAccountReleaseVerified: false`と`sameNameCredentialRecreationVerified: false`に分けます。終了コード0も、この内部解放の証明にはなりません。

同名APIMの再構築で「同じAPIMサービス名を別アカウントが使用中」というCM作成エラーが出た場合は、未解決として記録します。Azure公式OSSにも[同一症状の記録](https://github.com/Azure/azure-service-operator/blob/b9dcbe48feaf1173f48ab28dd5edc48b514d3f4d/v2/internal/testcommon/samples_tester.go#L120)がありますが、そこに解放手順・原因確定・解放期限は示されていません。soft-deleteの保持期間や一般的なpurge後待機を、このエラーの解決保証に読み替えません。

削除・配備の結果、エラーコード、発生時刻、相関IDをローカルの証跡から整理し、Microsoftサポートへサービス側の調査・解放方法を確認します。秘密値・トークンは共有せず、資材からの問い合わせ送信も行いません。未公開account IDを推測したDELETEや未定義の`deleteAll`指定は追加しません。同名再構築の実測は別の有料試験として扱い、`azd down`から自動的に環境を作り直しません。

### 削除待ち・再実行時に確認すること

- APIMやアプリが削除中なら要求を重ねず、同じIDの消失を待つ。待機期限を超えたら同じ入力・stateを保持して原因と現在状態を確認する。
- APIM本体の削除が`ServiceLocked`で拒否された場合は、所有情報を再確認し、約30分の待機枠内で再試行する。CMの削除処理は繰り返さず、APIMの削除受付後はGETで完了を確認する。他の409や412は自動再試行せず停止する。
- CMの個別削除と親APIMの削除を分ける。一覧消失を用いる限定条件でもprovider自身のGET404とは記録せず、`deletionVerified`・`listAbsenceVerified`・`parentDeletionVerified`を区別する。
- ACA管理RGだけが残る場合はAzureの削除完了を確認し、同じparamsで`python3 scripts/deployment/lifecycle.py postdown --config "$DEPLOY_PARAMS_FILE"`を再実行する。
- Key Vaultの完全削除が非同期で受け付けられた場合は、Azureが返す待機間隔に従って操作状態を読み、最後に対象の削除履歴の404も確認する。既定の待機枠は15分で、要求をその場で再送しない。上限・失敗で止まったら同じ入力と記録を保持し、操作と削除履歴の現在状態を確認してから再開する。
- Log Analyticsのforce完全削除は通常処理に含めない。
- モデル消失と利用枠・費用の反映は別に確認する。利用枠は次の`capacity`で再確認し、他環境の割当まで0にしない。

### 一部の所有RGが残る、またはResourceGroupNotFoundで止まる場合

部分構築の撤去でazd本体が空のRGを削除対象に含めない場合や、途中まで削除済みのRGがある場合は、`postdown`が`resource-groups-remain`で停止することがあります。先行回収の記録を確認し、以下の手順で残る所有RGだけを回収します。

1. `.local/<environment>/state/cleanup-predown.json`の対象が一致し、`result.status`が`ready-for-azd-down`であることを確認する。モデル・APIM・アプリ／ジョブ・CAE・サブネット・管理VMの先行回収と保持確認が済んだ記録を使う。
2. 同じSubscriptionで5RGの現存状態を読み取り、残るRGの所有と資源を照合する。削除中なら完了を待つ。
3. 残る所有RGだけを1件ずつ削除し、正常終了を確認する。削除済みRGを再作成せず、stateも書き換えない。

   ```sh
   az group delete --subscription '<SUBSCRIPTION_ID>' --name '<REMAINING_RESOURCE_GROUP>'
   ```

4. 5RGの不存在を確認してから、同じparamsで`postdown`を実行する。

   ```sh
   python3 scripts/deployment/lifecycle.py postdown --config "$DEPLOY_PARAMS_FILE"
   ```

5. 終了コードと新しい撤去記録を確認し、ACA管理RG・削除履歴・保護による保持・費用を記録する。

### 初回構築の途中で失敗し、撤去用stateがない場合

監視配備の成功前に停止すると、資源の一部が存在しても`.local/<environment>/state/latest.json`がない場合があります。通常の撤去hookは正確なstateなしでは続行しません。

1. 同じSubscriptionのAzure Portalで対象の配備・時刻・環境名・製品名・所有タグ・操作履歴を確認し、実際に作成された資源IDを特定する。処理中の操作を重複実行しない。
2. 成功済み配備の完全なoutputsやバックアップがある場合は、管理担当者が実資源・保護設定と照合して正確な記録を回復する。アプリ用`--deployment-name`を撤去用stateの代用にしない。
3. 成功済みoutputsがない場合はstateを捏造せず、管理担当者が確認できた資源だけをAzure Portalで限定回収する。存在するモデル・APIM・アプリ・ネットワークの依存順と監査保持を守る。
4. 実IDごとの残存・保持・費用を記録する。削除保護・WORM・purge protectionを回避しない。

生成された秘密入力だけを回収する場合は`python3 scripts/deployment/lifecycle.py clear-inputs --config "$DEPLOY_PARAMS_FILE"`を使います。対象は`.local/<environment>/generated/main.parameters.json`であり、元の保護入力・SSH鍵・配備記録を削除せず、Azure資源も撤去しません。azdが入力を使っている構築・撤去の実行中には、この回収を行いません。
