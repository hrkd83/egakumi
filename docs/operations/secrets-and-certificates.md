# EntraのsecretとTLS証明書を更新する

[共通手順](common.md) · [Entra準備](../setup/02-entra.md) · [運用テスト](../testing/operations-tests.md)

構築済み環境の通常更新に使う。共通手順で同じparams・state・資源IDを照合する。
以下のスクリプトは既定では計画だけを保護記録へ保存する。内容を確認してから
同じコマンドに`--execute`を付けて反映する。実環境への反映と受入を行うまでは、
資材・ローカル検証と実測を区別する。`azd up`・`azd provision`は使わない。

## Entra client secretを期限前に切り替える

入口は[entra_secret.py](../../scripts/operations/entra_secret.py)。同じアプリの旧値を保持し、
新値の作成、全利用先への切替、確認後の旧値廃止を別stageに分ける。
既存CLIのテナントは対象プロファイルのEntraテナントと一致している必要がある。
Graphでアプリの資格情報を管理できない場合は停止し、新しい権限・同意は自動付与しない。

1. 対象の論理プロファイル、Client ID、全物理CM provider、共有KV、Broker・Portal・
   Portal同期ジョブの参照を確認する。旧credentialのkey ID・有効期限を非秘密metadataで
   特定する。Valueとkey IDを取り違えず、新旧を併存できる期間内に作業する。
2. 同じ環境の新規保存先を選ぶ。receiptは`state/operations/`、新Valueは`secrets/`内とし、
   既存の`clientSecretFile`へ新規作成結果を直接上書きしない。

```sh
export OPS_SECRET_RECEIPT="$DEPLOY_LOCAL_DIRECTORY/state/operations/entra-rotation.json"
export OPS_NEXT_SECRET="$DEPLOY_LOCAL_DIRECTORY/secrets/entra-next.txt"
python3 -B scripts/operations/entra_secret.py --config "$DEPLOY_PARAMS_FILE" \
  --profile '<CREDENTIAL_PROFILE_NAME>' --receipt "$OPS_SECRET_RECEIPT" \
  --stage create --old-key-id '<OLD_CREDENTIAL_KEY_ID>' --new-secret-file "$OPS_NEXT_SECRET"
```

3. 計画後、同じコマンドに`--execute`を付ける。既存credentialをresetせず、
   `addPassword`で一つ追加し、Valueを新しい600ファイルへ保存する。旧値は有効なまま残る。
   作成要求や保存結果が不明なら再作成せず、receiptの段階・operation IDとEntraの
   credential metadataを照合する。Valueは作成後に再取得できない。
4. 次を計画・反映する。現在の保護入力は別の600ファイルへ退避し、同じClient IDの
   対象入力を新値へ揃える。既存の共有secretモジュールだけをサービスRGへ
   `Incremental`配備し、同名のKV新versionを保存した後、既存Gを実行する。

```sh
python3 -B scripts/operations/entra_secret.py --config "$DEPLOY_PARAMS_FILE" \
  --profile '<CREDENTIAL_PROFILE_NAME>' --receipt "$OPS_SECRET_RECEIPT" --stage switch
```

GはCM・Broker・SQL・API・WAF等も扱うため、予定外差分があれば開始しない。
共有KVへの書込みは管理プレーンの既存モジュールで行い、公開範囲やデータ用権限を
追加しない。対象RGへの配備・`Microsoft.KeyVault/vaults/secrets/write`権限は必要。
秘密入りの生成ARM入力は600で扱い、CLI引数・標準出力へ値を渡さず使用後に回収する。

Entra画面等で新Valueを既に作成した場合は、新しいreceiptを選び、`switch`に
`--new-secret-file`・`--old-key-id`・`--new-key-id`を指定できる。新値は現在の入力と
別の600ファイルへ保管し、作成済みValueをもう一度作り直さない。

5. 切替receiptが`switched-auth-unverified`であることを読む。KVの存在を読むだけのPを
   再実行しても、共有KVの値を書き込む操作にはならない。版を固定しないContainer Appsは
   通常30分以内に新版を取得し、該当環境変数を使うactive revisionが自動再起動する。
   取得待ちは他の読取・記録作業と並行する。再起動だけを新版取得の証拠にしない。
   [Microsoftの参照仕様](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets#key-vault-secret-uri-and-secret-rotation)
6. 新しいブラウザセッションでPortalへ正規サインインし、グループ照会・APIキー画面を
   確認する。対象profileで新しい本人接続を認証して実APIを取得する。全CM providerの
   設定、Broker・Portalと新版取得後の同期ジョブも確認する。キャッシュ済みトークンの
   一回の成功だけを切替完了にしない。
7. 上記を自分で確認した担当者だけが、次を計画・反映する。2フラグは担当者の確認申告であり、
   スクリプトが本人認証を自動実証した証拠ではない。切替receiptと現在の保護入力を照合し、
   指定した旧key IDだけを廃止する。新旧が曖昧、切替失敗、入力変更ありの場合は停止する。

```sh
python3 -B scripts/operations/entra_secret.py --config "$DEPLOY_PARAMS_FILE" \
  --profile '<CREDENTIAL_PROFILE_NAME>' --receipt "$OPS_SECRET_RECEIPT" --stage retire \
  --verified-new-sign-in --verified-new-connection
```

廃止後にもう一度新規サインイン・本人接続・代表APIを確認する。新しい期限と次回担当を
記録する。旧値を退避した保護ファイルは組織の秘密管理方針で回収し、Gitへ含めない。

**途中失敗と切戻し：** receiptの`*-requested`で止まった場合は、同じoperation IDの
ARM配備、Gの配備記録とジョブを読み、実行中・完了・失敗を先に判別する。進行中は
別の更新を重ねない。要求が不明な`create`・`retire`は盲目的に再送しない。
切替を試みた後で旧credentialがまだ有効なら、同じreceiptの`--stage rollback`を
計画・反映し、退避した旧入力→KV→CMへ戻す。切戻し後も認証確認は別途必要。
旧値が期限切れ・失効済みの場合は戻さず、新しい有効値への更新漏れを修正する。
本人接続の一括削除、Portalの署名・保存データ暗号化鍵の一括再生成は行わない。

## TLS証明書を更新し、公開HTTPSまで確認する

入口は[certificate.py](../../scripts/operations/certificate.py)。構築済み管理VMの保存済み
worker・configを使用し、新しい定期jobやACME環境を作らない。対象VM・AppGW・
versionless KV参照・公開ホストを照合し、workerのソース一致も確認する。
管理VMの既存MIを使って公開certificate metadataだけを読み、PFXや秘密鍵を取得しない。

1. まず次を計画・反映する。実行時は一回限りのVM Run Commandで更新timerと公開metadataを
   読み、Gateway／Portalと採用済みAPIM直通経路のSNI・信頼を検証する。
   `public-tls-verified`は、KVの現在leafと実TLSが一致した場合だけ記録する。
   正常完了した今回のRun Commandだけを回収し、404を確認する。実行中や応答不明のものは保持する。

```sh
python3 -B scripts/operations/certificate.py --config "$DEPLOY_PARAMS_FILE" --stage check
```

2. 期限に従う通常更新は次を計画・反映する。標準workerは12時間ごとに確認し、残り30日以内に
   更新する。十分な有効期間がある場合は既存leafを再利用し、不要な新規発行を行わない。

```sh
python3 -B scripts/operations/certificate.py --config "$DEPLOY_PARAMS_FILE" --stage renew
```

3. 期限前に計画更新する場合は上記へ`--rotate`を付ける。スクリプトが確認した現在leafを
   既存workerの`RotateFromThumbprint`へ渡し、新しい正常leafになったことを確認する。
   指定済みの公開thumbprintと照合したい場合は、代わりに`--thumbprint`を使う。
4. 更新後はAPIMの公式`refreshHostnames`を一度要求する。現在使用するAPI版は
   **2025-09-01-preview**で、計画にも表示する。これはNamed ValueのSecret refreshと別操作。
   AppGWは既存タグを保持したETag付きの作業用タグ更新でKVを再確認させ、作業用タグを戻す。
   参照変更や全体再配備で同期しない。[APIM同期API](https://learn.microsoft.com/en-us/rest/api/apimanagement/api-management-service/refresh-hostnames?view=rest-apimanagement-2025-09-01-preview)・[AppGWの取得仕様](https://learn.microsoft.com/en-us/azure/application-gateway/key-vault-certs#supported-certificates)
5. APIMは一度受理された同じLROを、Retry-Afterに従って最大60分待つ。AppGW更新も有限時間で読み、公開Gateway・Portal・採用した直通経路すべての
   実TLS leaf・信頼・ホスト名が現在KVと一致することを確認する。KVの版が増えたことだけで
   完了にしない。通常APIとPortalログインも一度確認する。
6. workerは成功したが同期で止まった場合は、元のVM Run Command・同期結果・参照を読む。
   正常な証明書を再発行せず、必要なら`--stage sync`を計画・反映して同期とTLS観測だけを
   行う。`check`で既に新版が配信されていると確認できた場合は追加更新をしない。
   `sync`はversionless参照と現在KVの公開metadataを照合してから、APIM直通経路の
   信頼・ホスト名付きTLSを確認する。現在leafと一致すればAPIMへのPOSTを省略し、
   AppGW同期と全公開経路の確認へ進む。保護記録`certificate-apim-refresh-skipped`は
   公開TLSによる一致の証拠であり、前のLROの成功を示すものではない。
   信頼付きTLSで旧leafを確認した場合は通常の同期要求へ進み、TLSを確認できなければ停止する。

APIMの同期要求のHTTP状態とエラー識別子は、保護された
`certificate-apim-refresh-response`記録で確認できる。`409 / ServiceLocked`で
拒否された場合は、新しいKV版を保持し、Azure側の保守・更新状況を確認して時間を置き、
同期だけを再試行する。資源の`Succeeded`表示だけでロック解除を判断しない。
応答が不明な場合は、元の要求の結果を確認してから再開する。

**異常時：** worker・DNS・KV・サービス同期のどこで止まったかを保護記録で特定する。
実行中や応答不明のVM要求・同期POSTを繰り返さず、旧証明書の有効な版を保持する。
DNS権限・KV権限／経路・timer・参照の対象だけを修正してから再試行する。
公開側が旧版なら、再発行する前に同期を確認する。作業用タグ復元が未完了なら、現在の
タグとETagを読み、同時変更を消さず該当タグだけを戻す。ログや秘密の一括取得は不要。
