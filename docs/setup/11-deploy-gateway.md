# 11. Gatewayを個別配備する

前：[09. Core構築後のEntra登録](09-egakumi-portal.md) · [10. ハブ＆スポーク（任意）](10-hub-spoke.md) · [README](../../README.md) · 次：[12. Portalの個別配備](12-deploy-portal.md)

CoreのAPIM、共通SQL、非公開ACR、ACA環境を使い、BrokerとCredential Managerの本人接続基盤を配備する。その後、APIMのAPI定義、バックエンド、ポリシー、製品を登録する。購読キーの例外発行は基盤管理者がAzureポータルで行う。本文監査が有効な環境では、中継と保存ワーカーも配備する。 [購読キーの運用](../operations/access-and-keys.md) · [本文監査の構成と受入条件](../reference/audit/request-body-audit.md)

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

共通基盤とGatewayの設定は同じparams・保護stateを使う。credentials DB、CM、Broker、照合ジョブ、APIM APIはGateway工程が担当する。

SQLサーバーは`sql-<stem>`、Private Endpointは`pe-<stem>-sql`。`<stem>`には環境名とプロダクト名が入る。SQLへの公開アクセスとSQLパスワード認証は無効とし、後続のジョブとアプリはマネージドIDで接続する。

共有SQLの接続ポリシーはProxy固定、Private Endpointへの通信はTCP1433とする。[Proxyを採用する理由](../reference/architecture/sql-connectivity.md)に、ネットワークの制限、Portalが同じSQL接続を保持する理由、ライブラリの対応範囲と性能上のトレードオフを記載する。

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

Gatewayが失敗した場合は、Coreと配備記録を保ち、原因を修正して再試行する。同じ環境・同じソースで登録済みのイメージは再利用する。APIMに子資源が一部作成された場合や同じAPI範囲の更新時に停止した場合は、[途中失敗時の手順](../operations/deployment.md#アプリ配備の途中で失敗した場合)で再開条件を確認する。Gatewayの完了後に[Portalを個別配備する](12-deploy-portal.md)。

継続配備では`.github/workflow-templates/gateway.yml.example`が同じコマンドを使う。`--image-digest`は別途作成済みのイメージを指定する場合に使い、通常の初期構築では指定しない。

## 5. 本人接続を試験する

API登録は手順4に含まれるため、別の登録コマンドは不要。配備完了と、Microsoftサインイン・Graphの実通信は分けて確認する。

[本人接続の手順](../reference/identity/credential-manager-login.md)に沿って、購読キーを付けたGraph APIの呼び出しから始める。セッションがないときの409に含まれる認証URLを利用者へ案内し、アプリのバックエンドで完了確認・本人セッションの保存・元要求の再実行まで確認する。全体の疎通は[第13章](13-verify-deployment.md)で確認する。

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

本人セッションの照合・回収とSQLの復旧は[Credential Brokerの運用](../operations/credential-broker.md)を参照する。

公開版のActionsファイルは無効な `.yml.example` テンプレートです。自身のrunner・Environment・OIDCを設定してから必要なファイルを `.github/workflows/` へコピーします。[設定上の注意](../../.github/workflow-templates/README.md)を参照してください。
