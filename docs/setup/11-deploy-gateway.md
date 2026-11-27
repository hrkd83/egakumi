# 11. Gatewayを個別配備する

前：[09. Core構築後のEntra登録](09-egakumi-portal.md) · [10. ハブ＆スポーク（任意）](10-hub-spoke.md) · [README](../../README.md) · 次：[12. Portalの個別配備](12-deploy-portal.md)

CoreのAPIM、共通SQL、非公開ACR、ACA環境を使い、BrokerとCredential Managerの本人接続基盤を配備する。その後、APIMのAPI定義、バックエンド、ポリシー、製品を登録する。購読キーの例外発行は基盤管理者がAzureポータルで行う。本文監査が有効な環境では、中継と保存ワーカーも配備する。 [購読キーの運用](../operations/access-and-keys.md) · [本文監査の構成と受入条件](../reference/request-body-audit.md)

| `apps` 配下の資材 | Gateway個別配備での役割 |
| --- | --- |
| [api-gateway](../../apps/api-gateway/README.md) | 統合配備のBicep、Credential Manager設定、API定義、APIMポリシー、Gateway入口のWAF設定 |
| [credential-broker](../../apps/credential-broker/README.md) | Broker本体、credentials DB、SQL初期化、定期照合ジョブ |
| [audit-relay](../../apps/audit-relay/) | 本文監査が有効なときに起動する中継アプリと保存ワーカー。標準のテスト・本番設定では有効 |

対応表をAzure SQLで管理し、同じ本人には同じCM接続を再利用する。クライアントは論理名`graph-delegated`を指定し、基盤が設定されたプールから割当先を選ぶ。共通configの初期`pool.count=12`・`pool.capacity=8000`を使い、内部の物理名`graph-delegated-a`〜`graph-delegated-l`を保持する。UPNは表示と初期命名に使い、本人確認にはEntraのテナントID・オブジェクトIDを使う。

本書は[第07章](07-params.md)で作成した`DEPLOY_PARAMS_FILE`と、第1章で決めた`DEPLOY_ENVIRONMENT`を使う。別のターミナルでは、実際に構築した環境の値を設定し直す。

## 1. Entraの戻り先を確認する

[Tools](03-entra-tools.md)と[Credential](04-entra-credential.md)の登録内容と、それぞれのWeb戻り先を確認する。

| アプリ | 戻り先 | 用途 |
| --- | --- | --- |
| egakumi-tools | `https://authorization-manager.consent.azure-apim.net/redirect/apim/<APIM_NAME>` | 購読キー利用者のGraph委任接続とCMの更新 |
| egakumi-credential | `https://<GATEWAY_HOST>/auth/connections/browser/identity-callback` | 接続窓口の本人確認 |

両者のclient secretの保護ファイル参照を確認する。GraphのBearer呼び出しでは、そのトークンの宛先のegakumi-apiに登録した証明書をBrokerで使う。

## 2. 本人接続の設定を確認する

公開paramsの`credentialProviders.graphDelegated`にはClient IDと`clientSecretFile`を設定する。共通configの同slotには表示名・scope・pool、`config.gateway.authentication.credentialLogin`にはprovider選択とセッション設定を置く。標準の論理名`graph-delegated`とtenantは固定資材から導出する。追加providerは双方の`additional[]`で定義と登録を論理名照合する。最終構成の本人確認には`gateway.authentication.credentialLogin.identity`を必須とする。

```bash
python3 scripts/deployment/lifecycle.py validate --config "$DEPLOY_PARAMS_FILE"
```

SQLの容量・バックアップ・ゾーン冗長化は`config/test.json`または`config/production.json`の`credentialBroker`で管理する。

## 3. Coreと証明書の準備完了を確認する

[Core構築](08-provision-core.md)が完了し、同じ環境の`.local/<environment>/state/latest.json`に今回の出力があることを確認する。[Core後のEntra登録](09-egakumi-portal.md)で、egakumi-apiへのOBO用公開証明書の登録とGraphの委任`User.Read`への管理者同意を確認する。企業ハブを使う場合は[第10章](10-hub-spoke.md)の通信確認を終えてから配備する。

共通基盤とGatewayの設定は同じparams・保護stateを使う。credentials DB、CM、Broker、照合ジョブ、APIM APIはGateway工程が担当する。既存アプリから移行する場合の事前確認は[工程8](../reference/entra-app-rollout-plan.md#工程8旧共用アプリと移行設定を撤去する)を参照する。

SQLサーバーは`sql-<stem>`、Private Endpointは`pe-<stem>-sql`。`<stem>`には環境名とプロダクト名が入る。SQLへの公開アクセスとSQLパスワード認証は無効とし、後続のジョブとアプリはマネージドIDで接続する。

共有SQLの接続ポリシーはProxy固定、Private Endpointへの通信はTCP1433とする。[Proxyを採用する理由](../reference/sql-proxy-decision.md)に、ネットワークの制限、Portalが同じSQL接続を保持する理由、ライブラリの対応範囲と性能上のトレードオフを記載する。

## 4. Gatewayを配備する

初回配備・更新・再試行には同じコマンドを使う。リポジトリルートで、同じparamsとCore stateを使う。GitHub Actions、PC上のDocker、PCからのSQL／ACRへの直接接続は不要。

```bash
python3 scripts/deployment/deploy_gateway.py \
  --config "$DEPLOY_PARAMS_FILE"

python3 scripts/deployment/deploy_gateway.py \
  --config "$DEPLOY_PARAMS_FILE" \
  --execute
```

最初のコマンドはローカル計画`.local/<environment>/state/gateway-plan.json`を作る。通常は保存済みCore stateから生成し、Azureへのアクセス・変更やparamsの編集は行わない。CIなどで`--deployment-name`を指定した場合は、Coreの配備結果をAzureから読み取る。`--execute`は次を順に実行する。

1. 共通基盤の実IDを確認し、認証窓口の公開ソースだけを既存管理VMへ転送する。params・secrets・個人セッションはイメージへ含めない。
2. 管理VMへACRスコープの組み込み`AcrPush`を一時付与し、イメージを作成して非公開ACRへ登録する。実際のdigestを取得し、そのビルドで作成した一時Run Commandと一時権限を回収する。
3. 本文監査を有効にした環境では、Coreの監査基盤を使って中継・保存ワーカーを配備し、準備を確認する。
4. credentials DB、認証窓口の実行用ID、CMプロバイダー・必要な組み込みロール、専用鍵とAPIMの名前付きの値・バックエンドを配備する。
5. 同じdigestのSQL初期化ジョブを実行し、テーブル・プール台帳・実行用IDのSQL権限を準備する。初回はスキーマ版4まで適用する。同じ資材で再試行する場合は適用済みのスキーマ、本人対応、取引・セッション、CMプールを引き継ぐ。
6. SQL初期化に成功してからContainer Appと定期照合ジョブを配備し、認証窓口の正常起動を確認する。Startup・Readiness・Liveness probeは`/health/gateway-caller-v1`でSQL版4とユーザー主体用の列を検査する。初期化に失敗した場合はアプリ更新とAPI登録を停止する。
7. API・バックエンド・Product・ポリシーを登録する。購読キーは[例外発行の運用](../operations/access-and-keys.md)に沿って管理者が発行する。必要な診断ログと初期サンプルの整理も行う。

初回配備とステージ開始前の再試行では、APIMが作成した既定のEcho APIを読み取りで確認する。対象APIMの初期リビジョンだけを許容し、業務APIの配備が確認できてから既存のサンプル整理処理を実行する。[Microsoft Learnの既定Echo APIの例](https://learn.microsoft.com/en-us/azure/api-management/how-to-deploy-self-hosted-gateway-kubernetes-opentelemetry#generate-and-consume-the-opentelemetry-metrics)

標準19 API正本を常用で解決する。計画と反映は個別コマンドの`--execute`で区別し、公開paramsの切替フラグを使わない。

本人接続のDB名は`sqldb-<stem>-credentials`、SQL初期化ジョブ名は`caj-<stem>-schema`、定期照合ジョブ名は`caj-<stem>-recon`。SQLの容量・バックアップ・ゾーン冗長化は`credentialBroker`設定を使う。保存データの暗号化はAzure SQL Databaseのサービス管理TDEを使う。[MicrosoftのTDE仕様](https://learn.microsoft.com/azure/azure-sql/database/transparent-data-encryption-tde-overview)

ACR・SQLの公開アクセスは無効のまま。追加の常設VMやカスタムロールは作らない。実行者には管理VMのRun Command操作、対象資源の配備、必要なスコープでのロール割当・解除権限が必要。`Owner`、または`Contributor`と`Role Based Access Control Administrator`等の組み込みロールの組合せを使う。通常実行用IDへ`AcrPush`やSQL管理者権限は付けない。

コマンドが終了コード`0`で完了し、今回更新された`.local/<environment>/state/gateway-deployment.json`が`status: deployed`になることを確認する。イメージ作成と一時資源の回収状況は、同じディレクトリの`credential-broker-build.json`に記録する。Coreの`latest.json`は後続のPortal配備でも使う。

Gatewayが失敗した場合は、Coreと配備記録を保ち、原因を修正して再試行する。同じ環境・同じソースで登録済みのイメージは再利用する。APIMに子資源が一部作成された場合や同じAPI範囲の更新時に停止した場合は、[途中失敗時の手順](../operations.md#アプリ配備の途中で失敗した場合)で再開条件を確認する。Gatewayの完了後に[Portalを個別配備する](12-deploy-portal.md)。

継続配備では`.github/workflow-templates/gateway.yml.example`が同じコマンドを使う。`--image-digest`は別途作成済みのイメージを指定する場合に使い、通常の初期構築では指定しない。

## 5. 本人接続を試験する

API登録は手順4に含まれるため、別の登録コマンドは不要。配備完了と、Microsoftサインイン・Graphの実通信は分けて確認する。

[本人接続の手順](../reference/credential-manager-login.md)に沿って、購読キーを付けたGraph APIの呼び出しから始める。セッションがないときの409に含まれる認証URLを利用者へ案内し、アプリのバックエンドで完了確認・本人セッションの保存・元要求の再実行まで確認する。全体の疎通は[第13章](13-verify-deployment.md)で確認する。

共通ポリシーの反映先は、APIMの「API」→「すべてのAPI」のポリシー。定義は`apps/api-gateway/policies/service.xml`、適用処理は`apps/api-gateway/deploy/api_publication.py`、認証関連の生成処理は`scripts/deployment/helpers/apim_authentication.py`にある。

- 有効な本人セッションでは認証画面を挟まずGraphを実行できること。
- セッション未指定・期限切れ・CM再認証時には、同じGraph APIの応答から認証を開始できること。
- 同じ人で2回サインインし、CM接続数が増えないこと。
- 別の人では別の接続になること。
- 別購読のセッションを使用すると拒否されること。
- 更新前後で既存のキー認証と保存済み`cs2`セッションが利用できること。
- GatewayのBearer認証でGraph OBOを使い、同じAPIを呼べること。キー経路では、CMでGatewayと異なる本人にサインインした場合や、別利用者のセッションを流用した場合に拒否されること。
- CMの切断・削除時に他の資格情報へ切り替わらず、再認証が必要になること。
- DBへの到達不能時に、本人接続APIが利用を許可しないこと。
- キー不正・権限不足・SQLや外部通信の障害を認証のやり直しに置き換えないこと。
- 初回同時サインインと空き枠の境界を、実SQLで確認すること。

## 運用と復旧

SQLの主要テーブルは`broker.Pools`、`broker.Connections`、`broker.Transactions`、`broker.Sessions`。資格情報のトークンはCM内に保持する。通常実行用IDにはDDLやユーザー管理の権限を付けない。スキーマ変更は初期化用IDのジョブで行う。

本人セッションv2は`broker.SubjectSecurity`と`broker.SessionRotationCandidates`も使用する。`Sessions`には認証完了時刻、最終業務利用時刻、無操作・最長期限、現行/旧値の検証ハッシュ、世代と失効状態を保持する。`SubjectSecurity`は本人単位の全失効、`Connections`の失効番号は接続解除と関連セッション失効に使う。`rowversion`はSQLの比較更新用であり、通常の時刻更新でも変わるため失効番号とは区別する。仮値・現行値・候補の平文やCMトークンを台帳へ保存しない。

`Transactions.ConnectionEpochAtVerification`はCM接続を準備・照合した時点の接続失効番号であり、認証完了時にも一致を確認する。認証途中の接続解除と、古いコールバックの完了が競合しても接続を復活させない。

期限はconfigの`gateway.authentication.credentialLogin`の`idleTimeoutSeconds`（既定7日）、`absoluteLifetimeSeconds`（30日）、`rotationIntervalSeconds`（24時間）等で指定する。SQLの時刻で失効・期限を判定し、検証済み業務APIでだけ最終利用を更新する。既存セッションの最長期限は設定変更や値の更新で伸ばさない。[本人セッションの契約](../reference/credential-manager-login.md)を参照する。

初めて`002_session_lifecycle.sql`を適用する既存DBでは、セッション関連定義の変更と旧取引・旧セッションの無効化を伴う。保守時間を確保してSQL→Broker→APIMまで同じ資材で更新し、本人接続を再認証する。適用済みmigrationの通常再配備では、この初期化を繰り返さない。

### ユーザー主体対応への切り替え

`004_verified_user_caller.sql`はデータを保持する追加移行であり、002で行う旧セッションの無効化は繰り返さない。ただし、旧Brokerはスキーマ版を正確に3と照合するため、SQLを版4へ更新した時点で旧Brokerのhealthは失敗する。対応版Brokerも正確に版4と必要列を照合する。この切り替えには保守時間を確保し、同じGatewayソース・paramsで**SQL→対応版Broker imageと定期照合ジョブ→APIMポリシー**の順に更新する。進行中の旧照合ジョブも終了を確認する。

APIだけを更新する処理は、対応するReadiness probeと最新Ready revisionの実health・imageをAzureから読み合わせる。旧runtimeや未完了のSQL移行では、APIMやFirewallを書き換える前に停止する。SQL移行をAPIだけの配備で代用しない。

SQL版4への移行後、旧imageだけへ戻しても旧Brokerのhealthは回復しない。対応版のSQL・image・定期ジョブを揃えて同じparamsから再試行し、前進して復旧する。DBを戻す判断が必要な場合は、本書の時点復元手順に従う。SQLの復元はCM側の状態を巻き戻さない。

既存キー用の`cs2`は保持する。分離済みGraph APIのBearerは検証済み本人をOBOで引き継ぐ。CMを採用した他のBearer対応APIでは検証済み本人に対応する`cs2`を使う。同じテナント・本人のCM接続は再利用するが、キー用とユーザー用のセッションは相互に流用できない。SQL版3から4への移行は実SQLでは未試験であり、配備前に専用SQLで移行・データ保持・既存キーの継続利用を確認する。

SQLの実行用ユーザーはマネージドIDのクライアントIDをSIDとして登録する。Azure RBACの割当で使うオブジェクトIDとは用途が異なる。[Microsoft公式のCREATE USERの例](https://learn.microsoft.com/en-us/sql/t-sql/statements/create-user-transact-sql?view=azuresqldb-current#k-create-a-contained-database-user-from-a-microsoft-entra-principal-without-validation)に従う。

管理VMからの直接保守は、担当者本人のEntra IDに、DBAが承認済みの構築DBとオブジェクトのSQL権限を手動付与し、終了時に回収する。初期構築ではスキーマジョブと通常アプリのIDに必要なSQL権限を用意する。本人への付与と回収、初回構築直後のDBA権限、回収後の接続確認は[管理VMからのSQL保守手順](../operations/management-sql-access.md)に従う。

既存の照合ジョブは毎時17分にCMとSQLを突き合わせ、消失・切断した接続を利用未確認に戻す。同じジョブで60日未使用の接続も回収し、Entra同期や追加ジョブは作らない。CM側だけの接続、pool上限、照合不能は件数として調査し、観測だけで容量を解放しない。

接続の未使用期間は`Connections.LastUsedAt`からSQL時刻で判定する。新規予約時を起点とし、その後は本人照合に成功した許可済み業務利用だけで更新する。ログイン・認証完了・照合・値の更新確定は延長しない。60×86400秒以上を回収対象とし、有効セッションがあるだけでは除外しない。有効なleaseや未完了の認証は保護し、直前の利用や再認証との競合は所有者・`rowversion`・leaseをSQLで再確認する。

1回の実行は最大100件、開始から300秒まで新しい回収を受け付ける。各接続を300秒のleaseで確保し、SQLで先に`deleting`と古い証明の無効化を確定する。CMの個別GETで対象のID・型・名前を確認し、SQLの確保状態を再検証して`If-Match`付きDELETEを送る。有効なETagはその値を使い、ヘッダー自体がない場合だけ、明示されたSQL検証が成功すれば`If-Match: *`を使う。空・不正・複数・ワイルドカード値のETagをこの代替へ切り替えない。新規割当には別の世代名を使い、既存対応の接続名は維持する。方式切替時は、回収を再開する前に接続名を再利用する旧runtimeと旧照合実行の終了を確認する。CMの個別GET 404と同じ回収lease・接続をSQLで確認した後だけ、対応表の削除と容量減算を一つのトランザクションで確定する。CMの接続状態値は未使用期間の判定元にしない。SQL検証不成立・不正ETag・412・通信失敗・削除結果不明・削除保留では枠を保持し、`LastCheckedAt`の試行順で次回に再試行する。利用者が解除を要求済みの`deleting`接続は、60日未満でも同じ確認手順で再試行する。

`reconcile`の計画モードは読取・件数集計だけで、定期ジョブは`reconcile --execute`で回収まで実行する。単体テストの偽SQL/HTTPで確認した処理順と、実SQLのロック・実CMの条件付き削除・正式ジョブの実行履歴を区別して記録する。結果の`cmCleanupAttempted`は削除確認callbackの論理的な試行であり、HTTP DELETEの実送信回数とは区別する。

CM側だけに残る接続を確認したpoolは`broker.Pools.AllocationBlocked=1`として、新規割当を止める。CMの実在接続・本人対応表・`Allocated`件数を運用者が照合・修復してから解除する。通常の照合や`migrate`では解除しない。既存の正常な接続はGraph本人照合を継続する。

`loginCallsPerMinute`は検証済み購読またはユーザー単位の開始回数制限である。同時サインイン数、SQLの待ち時間、CM管理APIの制限を測定して設定する。`pool.count × pool.capacity`は保管枠であり、同時処理性能の保証ではない。

SQLの時点復元はCMを巻き戻さない。次の順で復旧する。

1. 本人接続APIとbrokerを停止し、新規セッション発行を止める。
2. バックアップを別名のSQL DBへ復元し、台帳・スキーマを確認する。
3. 復元DBの`broker.Sessions`と`broker.Transactions`を全て失効させる。過去のセッションを再使用させない。
4. CMと対応表を照合する。紐付けが不明な接続は利用を止め、担当者が確認する。
5. 保守手順で検証済みDBを稼働先へ切り替え、SQL初期化ジョブで権限とスキーマを検査する。
6. 再サインイン・Graph本人照合を確認して本人接続APIを再開する。

検証環境で復元訓練を実施し、復元可能時点と復旧所要時間を記録する。ゾーン障害、DBの論理破損、CMとの不一致を別々の試験として扱う。

公開版のActionsファイルは無効な `.yml.example` テンプレートです。自身のrunner・Environment・OIDCを設定してから必要なファイルを `.github/workflows/` へコピーします。[設定上の注意](../../.github/workflow-templates/README.md)を参照してください。
