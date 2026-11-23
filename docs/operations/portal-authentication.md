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

署名用UAMIには対象鍵のmetadata読取り・署名だけを付与します。管理VMは発行・照合と旧鍵版の無効化を行うため、より強い運用権限を持ちます。管理VMの既存HTTPS証明書操作権限は継続します。鍵は非exportableでも署名権限の悪用を防げるわけではないため、VM/UAMIとRBACを保護します。

## 実行前の照合

同じcheckoutの`DEPLOY_PARAMS_FILE`、azd環境、Coreの`state/latest.json`、`state/portal-auth-certificate.json`を選択してください。別環境のstate・鍵を黙って探索・コピーしません。`.local/`のルートをリンクにせず、ディレクトリは所有者専用、ファイルは所有者専用にします。運用入口はparams・Core所有記録・現物VM/KVを照合します。

`scripts/operations/portal_auth_certificate.py`は既定で計画を表示します。Azure変更には`--execute`が必要です。値や公開証明書本文は画面へ出しません。管理VMの配置済みworkerのハッシュが一致することも確認します。

```sh
python3 -B scripts/operations/portal_auth_certificate.py prepare --config "$DEPLOY_PARAMS_FILE"
```

## 初期構築と既存環境への限定追加

新規構築は標準の`azd up`を使います。Coreの`infra/main.bicep`が署名専用ID、鍵運用権限、初期発行Run Commandの3部品を呼び出します。TLS工程による固定版PowerShellの準備、KVのPE/DNS・通信規則・RBAC、監査設定の完了後に、管理VMで`Initialize`を実行します。`postprovision`が公開`.cer`と初期証明書stateを収集します。専用Portal EntraアプリのClient IDはこの段階で不要です。初期発行・取得が失敗した場合は構築完了処理を停止し、Coreの作成記録を保持して生成した秘密入り入力を回収します。

構築済みの環境へ証明書準備を追加する場合は、同じ3部品だけを使う`initialize`を選びます。対象は署名ID、カスタムロール定義、対象鍵scopeのロール割当、管理VM Run Commandの計4資源です。既存Portal・管理VM・KV・所有RG、管理VMのCertificates Officer権限、TLS Run Commandの正常終了とPowerShell準備を読み取りで照合します。KVへの実通信と実runtimeの版は発行workerも検査します。

```sh
# 既存環境の初期発行計画。3部品のwhat-ifと保護checkpointを作成
python3 -B scripts/operations/portal_auth_certificate.py initialize --config "$DEPLOY_PARAMS_FILE"
# 最終差分を確認してから実行
python3 -B scripts/operations/portal_auth_certificate.py initialize --config "$DEPLOY_PARAMS_FILE" --execute
```

別のsource checkoutから既存の保護runtimeを使う場合は、`--runtime-root "$PROTECTED_RUNTIME_ROOT"`を両コマンドへ明示します。paramsはそのroot直下の`.local/<environment>/params.local.json`、Core記録は同環境の`state/latest.json`を選びます。入力・stateの探索、コピー、書換えは行いません。新しいcheckpointと証跡・公開証明書は明示したruntimeへ所有者専用権限で保存します。`--runtime-root`は初期発行だけで使用できます。

全3部品のwhat-ifで4資源のCreate/NoChangeだけを照合し、Modify/Delete、他資源への書込み、未展開・重複・未解決診断、違う権限やworkerを拒否します。各初回PUTの直前にもwhat-ifを確認します。Run Command内部の証明書生成はARMの4資源には含まれず、変更予測の成功はKV発行成功を意味しません。Entra登録・Portal認証切替は後続の手動登録と個別配備で行います。

初期発行のcheckpointは`state/portal-auth-initialization.json`です。要求・source・VM principalを固定し、PUT前に配備IDを保存します。応答不明や収集・保存の途中失敗では、同じコマンドを再実行して保存済み配備をGETで確認し、PUTを重ねません。Failed/Canceledは停止してcheckpointを保持します。初期発行では`--resume-failed-worker`と更新用の受入フラグを使えません。失敗配備の再実行は、KV・VMのcheckpointを調査し、回復方法を判断してから行います。保存済み入力やsourceを変更して強制再開しません。

## 更新の段階

| 操作 | 結果と次の担当 |
| --- | --- |
| `prepare` | 管理VMからKVへ新しい鍵・自己署名証明書の生成を要求。現行固定参照を維持し、`generated/portal-auth-renewal-public.cer`へ公開証明書だけを出力 |
| 手動Entra登録 | Entra担当が新しい公開証明書を追加し、thumbprint・期限を照合。旧証明書は受入まで保持 |
| `confirm-registration` | 担当者が手動登録の完了を明示し、対象アプリの設定とcandidateを束ねる |
| `switch` | 現行の固定イメージを使い、本体とGraphジョブの鍵版・thumbprintを一緒に切替。設定・イメージを読戻し、VMのcurrentを同期 |
| `verify` | 実際のログイン・Graph管理・定期ジョブを人が確認した後、現物設定も照合して受入記録を更新 |
| 手動Entra旧版削除 | 受入後、Entra担当が旧公開証明書を削除 |
| `retire` | 現行設定を再確認して、旧KV鍵のexact versionだけを無効化 |
| `rollback` | 旧公開証明書と鍵が有効な間だけ、保存した旧固定参照と同じイメージへ復旧 |

```sh
python3 -B scripts/operations/portal_auth_certificate.py prepare --config "$DEPLOY_PARAMS_FILE" --execute
# Entra担当が新しい公開 .cer を追加し、thumbprint・期限・対象アプリを確認
python3 -B scripts/operations/portal_auth_certificate.py confirm-registration --config "$DEPLOY_PARAMS_FILE" --entra-registration-confirmed --execute
python3 -B scripts/operations/portal_auth_certificate.py switch --config "$DEPLOY_PARAMS_FILE" --execute
# ログイン、Graph管理画面、定期ジョブの実行結果を確認してから進める
python3 -B scripts/operations/portal_auth_certificate.py verify --config "$DEPLOY_PARAMS_FILE" --login-verified --graph-verified --job-verified --execute
# Entra担当が旧公開証明書を削除
python3 -B scripts/operations/portal_auth_certificate.py retire --config "$DEPLOY_PARAMS_FILE" --entra-old-certificate-removed --execute
```

受入のフラグは実施済みの確認を記録するためのものです。ヘルス応答だけで付けません。切替時のイメージは`state/portal-deployment.json`から取得し、同時に新機能をリリースしません。更新中はPortal通常配備との重複を避けてください。ローカルの運用lockは他checkoutや他端末の同時実行まで排他するものではありません。管理VMのworkerも同じ鍵の操作を排他します。

```sh
python3 -B scripts/operations/portal_auth_certificate.py rollback --config "$DEPLOY_PARAMS_FILE"
python3 -B scripts/operations/portal_auth_certificate.py rollback --config "$DEPLOY_PARAMS_FILE" --execute
```

旧Entra証明書の削除後や旧KV鍵無効化後はこの復旧経路を使えません。旧鍵名そのものを削除すると現行versionまで失うため、鍵全体の削除は更新手順に含めません。期限切れで現行署名が使えない場合も新証明書の準備はできますが、旧版への復旧はできません。

## 失敗・応答不明からの再開

workerはKV作成POSTの前に管理VMの保護checkpointへintentを保存します。POSTの応答不明では別の鍵を重ねて生成せず、pendingと新しい公開証明書のrequestタグを照合します。初回再構築も既存currentを検証して再利用し、毎回発行しません。現物とstateが異なる場合は停止します。

運用入口もVM Run Commandのexact IDをPOST/PUT前に保存します。同じ操作を再実行するとそのcommandを確認します。Pending状態を再送しません。失敗資源と保護checkpointを調査し、VM commandがFailed/Canceled/TimedOutで完了していることを確認できた場合だけ、同じ操作へ`--resume-failed-worker`を付けて再開します。新しいVM commandは既存KV intentを利用し、発行POSTを自動で重ねません。

```sh
python3 -B scripts/operations/portal_auth_certificate.py prepare --config "$DEPLOY_PARAMS_FILE" --resume-failed-worker --execute
```

KVの要求が送られたか不明でpendingも結果もない場合、checkpointを削除して再実行せず、運用担当がKVの現物と要求状況を確認してください。由来不明の既存証明書や他要求の証明書を自動採用しません。完了commandは段階結果を保存してから回収し、失敗commandは調査用に保持します。保存・回収の途中失敗でも、既存stateを手で削除せず同じ段階から再開します。

## 旧Portal構成の撤去

新Portalの実受入後、ACA本体・Graphジョブに`MICROSOFT_CLIENT_SECRET`と旧KV secret参照が残っていないことを確認します。旧実行IDに残るPortal用途の`core-credential-<profile>` secret読取割当も、scope・principal・利用先を照合して撤去します。Bicepの条件で割当を作らなくなっても、incremental配備では既存の割当が自動削除されるとは限りません。

Entra担当は旧アプリのPortal専用Redirect URIだけを利用先確認後に撤去します。CMが使うsecret、Redirect URI、権限は残します。旧secret値・CM共用資材を自動削除する操作はありません。旧イメージによる復旧を終えた後、ソースに残る移行用secret処理も別途差分確認して撤去します。

実環境の証跡は`.local/<environment>/evidence/`へ保存します。公開の検証記録には独立した合成値と一般化した結果だけを記載します。[構築手順](../setup/entra-portal.md)と[受入試験](../testing/portal-authentication-tests.md)を併せて確認してください。
