# Portal認証証明書の運用

状態：工程3のローカル検証中。以下のAzure上の発行・切替・復旧・旧版廃止は実受入前です。旧Portalのsecret参照は切替受入までの復旧用であり、恒久互換にはしません。

## 管理対象

| 対象 | 用途と更新 |
| --- | --- |
| Portal認証のKV自己署名証明書 | 専用Entraアプリのclient assertion。RSA秘密鍵はKV内に保持。既定12か月、60日前に更新準備、新しい鍵も生成 |
| App GatewayのHTTPS証明書 | ACME/Let's Encrypt等の既存TLS処理。今回の認証証明書とは別 |
| CMのEntra client secret | CMの既存認証。Portal切替を理由に削除しない |
| Portal Cookie署名・保存暗号鍵 | `BETTER_AUTH_SECRET`等の既存資材。認証証明書更新で変更しない |

12か月・60日前は運用既定値で、Microsoft共通の必須更新頻度ではありません。`portal.certificate`の有効期間を変更する場合は、現行証明書との整合と手順を確認してください。KVの自動更新を有効にしてもEntra登録や固定参照の切替は自動完了しません。この資材はKVの自動更新actionを設定しません。

署名用UAMIには対象鍵のmetadata読取り・署名だけを付与します。発行・照合と旧鍵版の無効化は、証明書Jobに割り当てた専用の運用IDが行います。鍵をエクスポートできなくても、署名権限の悪用を防ぐにはUAMIとRBACの保護が必要です。

証明書Jobの鍵単位のDataActionは`Microsoft.KeyVault/vaults/keys/read`と`Microsoft.KeyVault/vaults/keys/update/action`です。`keys/write`は管理プレーンのActionであり、このKVデータ操作用ロールには指定しません。[Microsoftの操作一覧](https://learn.microsoft.com/en-us/azure/role-based-access-control/resource-provider-operations#microsoftkeyvault)を参照してください。

KVの最新証明書GETはpolicyを含みますが、版指定GETは証明書本体だけを返します。workerは最新の同じ版のpolicy・公開鍵・署名を検証し、証明書ID・鍵ID・thumbprintと結び付けたpolicyをJob用Storageの保護checkpointへ保存します。更新後の旧版はこの検証済み記録を使い、現在の別の版のpolicyで代用しません。記録のない過去版を自動採用しません。これは公開metadataの保存で、秘密鍵を取り出す処理ではありません。

## 実行前の照合

同じcheckoutの`DEPLOY_PARAMS_FILE`、azd環境、Coreの`state/latest.json`、`state/portal-auth-certificate.json`を選択してください。別環境のstate・鍵を黙って探索・コピーしません。`.local/`のルートをリンクにせず、ディレクトリは所有者専用、ファイルは所有者専用にします。運用入口はparams・Core所有記録・Jobまたは旧構成の実行先・対象KVを照合します。

`scripts/operations/portal_auth_certificate.py`は既定で計画を表示します。Azure変更には`--execute`が必要です。値や公開証明書本文は画面へ出しません。Job構成では固定イメージdigestと今回の実行を照合します。

```sh
python3 -B scripts/operations/portal_auth_certificate.py prepare --config "$DEPLOY_PARAMS_FILE"
```

## 初期証明書の発行と確認

新規構築は[Coreの初回構築](../setup/08-provision-core.md)で`azd up`を使います。`azd provision`でも同じCore工程を実行できます。Coreが署名用ID、証明書Job用の運用IDと権限、専用ACA環境とプライベート接続を準備します。非公開ACRへ登録した固定イメージでPortal署名Jobの`Initialize`を実行します。`postprovision`が公開`.cer`と初期証明書stateを収集します。専用Portal EntraアプリのClient IDはこの段階で不要です。初期発行・取得が失敗した場合は構築完了処理を停止し、Coreの作成記録を保持して生成した秘密入り入力を回収します。

構築後に保存済みの初期証明書stateを確認する場合は、`initialize`を実行します。CoreのACA Job構成と初期状態をローカルで確認するコマンドです。途中の発行を再開する場合は、同じ保護入力で`azd up`を実行してください。

```sh
python3 -B scripts/operations/portal_auth_certificate.py initialize --config "$DEPLOY_PARAMS_FILE"
```

別のsource checkoutから確認する場合は、`--runtime-root "$PROTECTED_RUNTIME_ROOT"`で既存の保護runtimeを明示します。指定先の`.local/<environment>/params.local.json`と、同じ環境の`state/latest.json`および`state/portal-auth-certificate.json`を照合します。

## 更新の段階

| 操作 | 結果と次の担当 |
| --- | --- |
| `prepare` | Portal署名JobからKVへ新しい鍵・自己署名証明書の生成を要求。現行固定参照を維持し、`generated/portal-auth-renewal-public.cer`へ公開証明書だけを出力 |
| 手動Entra登録 | Entra担当が新しい公開証明書を追加し、thumbprint・期限を照合。旧証明書は受入まで保持 |
| `confirm-registration` | 担当者が手動登録の完了を明示し、対象アプリの設定とcandidateを束ねる |
| `switch` | 現行の固定イメージを使い、本体とGraphジョブの鍵版・thumbprintを一緒に切替。設定・イメージを読み戻し、発行workerのcurrentを同期 |
| `verify` | 実際のログイン・Bearer試用・Graph OBOを人が確認した後、現物設定も照合して受入記録を更新 |
| 手動Entra旧版削除 | 受入後、Entra担当が旧公開証明書を削除 |
| `retire` | 現行設定を再確認して、旧KV鍵のexact versionだけを無効化 |
| `rollback` | 旧公開証明書と鍵が有効な間だけ、保存した旧固定参照と同じイメージへ復旧 |

```sh
python3 -B scripts/operations/portal_auth_certificate.py prepare --config "$DEPLOY_PARAMS_FILE" --execute
# Entra担当が新しい公開 .cer を追加し、thumbprint・期限・対象アプリを確認
python3 -B scripts/operations/portal_auth_certificate.py confirm-registration --config "$DEPLOY_PARAMS_FILE" --entra-registration-confirmed --execute
python3 -B scripts/operations/portal_auth_certificate.py switch --config "$DEPLOY_PARAMS_FILE" --execute
# ログイン、Bearer試用、Graph OBOの結果を確認してから進める
python3 -B scripts/operations/portal_auth_certificate.py verify --config "$DEPLOY_PARAMS_FILE" --login-verified --graph-verified --execute
# Entra担当が旧公開証明書を削除
python3 -B scripts/operations/portal_auth_certificate.py retire --config "$DEPLOY_PARAMS_FILE" --entra-old-certificate-removed --execute
```

受入のフラグは実施済みの確認を記録するためのものです。ヘルス応答だけで付けません。切替時のイメージは`state/portal-deployment.json`から取得し、同時に新機能をリリースしません。更新中はPortal通常配備との重複を避けてください。ローカルの運用lockは他checkoutや他端末の同時実行まで排他するものではありません。専用ACA JobはBlobリースで同じ鍵の操作を排他します。

```sh
python3 -B scripts/operations/portal_auth_certificate.py rollback --config "$DEPLOY_PARAMS_FILE"
python3 -B scripts/operations/portal_auth_certificate.py rollback --config "$DEPLOY_PARAMS_FILE" --execute
```

旧Entra証明書の削除後や旧KV鍵無効化後はこの復旧経路を使えません。旧鍵名そのものを削除すると現行versionまで失うため、鍵全体の削除は更新手順に含めません。期限切れで現行署名が使えない場合も新証明書の準備はできますが、旧版への復旧はできません。

## 失敗・応答不明からの再開

workerはKV作成POSTの前にJob用Storageの保護checkpointへ要求を保存します。POSTの応答不明では別の鍵を重ねて生成せず、pendingと新しい公開証明書のrequestタグを照合します。初回再構築も既存currentを検証して再利用し、毎回発行しません。現物とstateが異なる場合は停止します。

運用入口は開始前の識別子を保存します。同じ操作を再実行すると、その要求に対応するJob実行を照合します。状態が不明な場合は開始要求を重ねず、元の実行を調べます。失敗が確定した場合だけ、同じ操作へ`--resume-failed-worker`を付けて再開します。新しい実行は既存の要求記録を使い、KVへの発行POSTを自動で重ねません。

```sh
python3 -B scripts/operations/portal_auth_certificate.py prepare --config "$DEPLOY_PARAMS_FILE" --resume-failed-worker --execute
```

KVの要求が送られたか不明でpendingも結果もない場合、checkpointを削除して再実行せず、運用担当がKVの現物と要求状況を確認してください。由来不明の既存証明書や他要求の証明書を自動採用しません。段階結果と実行記録を調査できるように保持します。保存の途中で失敗した場合も、既存stateを残して同じ段階から再開します。

実環境の証跡は`.local/<environment>/evidence/`へ保存します。公開の検証記録には独立した合成値と一般化した結果だけを記載します。[構築手順](../setup/09-egakumi-portal.md)と[受入試験](../testing/portal-authentication-tests.md)を併せて確認してください。
