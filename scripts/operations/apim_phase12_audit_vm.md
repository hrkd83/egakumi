# 工程1・2の監査ログを管理VMから取得する

[apim_phase12_audit_vm.py](apim_phase12_audit_vm.py)は、構築済みLinux管理VMの正式clientを使って、限定したWorkspaceの監査データを保護保存する内部APIです。単独で実行するCLIではありません。合成試験の確認範囲は末尾に示し、実際のVM・MI・Workspaceからの取得・認可・通信の確認状況は、対象環境の保護記録で管理します。取得できたことと、監査の照合成功・工程1～2の完了を区別します。

## 呼出し前の準備

実行ソース、利用者が明示した主チェックアウト、既存の保護入力を使い、`OperationContext.load(config_path, runtime_root=primary_checkout)`でcontextを用意します。入力・state・途中結果・証跡は、その主チェックアウト直下の`.local/<environment>/`へまとめます。別の保存先の探索・複製、Windowsの認証cache・Bearer・APIキーのVMへのコピーは行いません。

呼出し側は、対象の受入結果・要求ID・資源ID・絶対時刻範囲を保護記録から確認し、KQLをその範囲へ限定します。inventoryは、現在のCore出力に対して`management_client_inventory(config, state)`で検証した値を使います。clientの期待SHA256は、使用する公開正本を`management-maintenance-client.bicep`と同じCRLF→LFで正規化したUTF-8 bytesから計算し、実際にインストールするsourceと照合します。

`expected_snapshot`には、呼出し側が確認した保護入力・Core state・`state/gateway-api-source.json`の内容SHA256を、`input`・`coreState`・`checkpoint`の3項目で渡します。実値・派生hashを公開記録へ転載しません。helperは同じ環境のoperation lockを取得し、contextとsnapshotを読み直してから最初のARM操作へ進みます。次のchunk要求前と結果確定前にも再確認します。

```python
result = run(
    context,
    verified_inventory,
    bounded_queries,
    expected_client_sha256=public_client_source_hash,
    expected_snapshot=verified_protected_snapshot,
    operation_id=new_independent_hex_nonce,
)
```

`bounded_queries`は、一意な`name`とKQLの`query`を持つ1～4件の配列です。KQLは1件1MiB以内、protected payload全体は256KiB以内です。名前は英小文字から始まる英小文字・数字・ハイフンで32文字以内です。KQLの内容を実行コードやshellへ挿入しません。呼出し用Pythonも`-I -B`等でbytecode保存を抑止します。

## VM内の正式clientと保護境界

既設の`/opt/core/maintenance/venv/bin/python -I -B`と`management_client.py workspace-query`を使います。VM自身のIMDS/SAMIと所有inventoryでWorkspaceを照合し、利用者の資格情報を渡しません。clientのインストール・更新、権限・NSG・構成の変更も行いません。

guestは既存のprepare lockを取得してから、clientの正本hash・所有者・権限・リンクを検査します。検証したsource bytesを独立したnamespaceで読み、cacheや`.pyc`から別のmoduleを読みません。VM UUID・inventoryの固定項目も照合します。

managed Run Commandの結果読戻し直後にVMが`Updating`の場合、呼出し側は、helperの単一のVM検査でUUID・MI・tenant・世代等の照合を通過した状態だけを、最大60秒の読取り待機対象にできます。各読取りでも同じ検査とsource／保護snapshotの再確認を行い、`Succeeded`へ戻った場合だけ後続へ進みます。未知の状態、`Failed`・`Canceled`、本人や世代の不一致、期限超過は停止します。この有限待機は呼出し側の処理であり、`run`単体の自動待機ではありません。guest script・要求・receipt・構成を変更して通過させません。

要求KQL・出力・owner receipt・manifestは、管理VMの専用証跡フォルダーへ新規作成します。ディレクトリ700、ファイル600、非リンク、既存ファイルを上書きしない条件を維持します。元のclient・inventory・他の証跡は変更しません。正式clientのHTTP待機は30秒、応答上限は16MiBです。partial errorや不完全なtable・column・rowは取得成功として採用しません。

## 有限の取得と再送禁止

照会は、独立した新規ID・GETの404・共有lock・保護された実行意図・1回だけのPUT・正確な読戻しで行います。PUT・DELETEを自動再送しません。同じoperation IDの記録があれば、再実行せず記録済みcommandを確認します。照会のPUT応答不明・実行中・失敗は、未実行と推測しません。

PUT応答は判定より前に、応答オブジェクト・HTTP状態・非同期処理ヘッダー・transportの応答連鎖を保護記録へ保存します。応答schemaの不備や保存失敗は結果不明として扱い、未実行へ読み替えません。公開出力へ応答を転記せず、同じ変更要求の再送で補いません。

query workerの待機上限は900秒、chunk workerは180秒、pollは最大5秒間隔です。guestの照会結果をJSONとして最大4MiB、gzipとして最大48KiBへ制限し、1KiB未満のmanifestだけを最初に返します。各chunkはgzipの1536バイトをBase64で返し、上限32個です。固定の単一marker、index・長さ・chunk SHA256・全体hash・上限付き展開を照合します。最終chunkでは、組み立てたgzip全体も回収前に確認します。

結果は選択済み主チェックアウトの`evidence/phase12-audit-<operation-id>/`へ、`context.save_private`で新規保存します。公開stdoutへ取得本文・KQL・例外詳細・実ID・実パスを出しません。`progress`へ渡す値は固定の段階名だけです。返却された証跡パスや回収待ちIDも保護記録として扱います。

## 正確な回収と回収待ち

commandのID・種類・タグ・source・実行設定・VM/SAMI、実行終端・exit・単一成功記録とその内容を、保存と回収の前に確認します。回収前には、expanded GETで最新の終端・結果とETagを読み直します。実行中へ戻ったもの、再作成・結果・所有の変化を検出したものは削除しません。

Computeの対象APIに対する条件付きDELETEの契約は、ローカルの準備だけでは確認できていません。契約未確認、または厳密なETag欠落なら、その正確なcommandを回収待ちとして保護記録へ残し、有限の結果取得は続けます。`temporaryCommandsRemoved`はfalseで、`cleanupPendingCommands`に対象を残します。未確認のCASを保証せず、prefix単位の削除やqueryの再実行で回避しません。

条件付きDELETEを確認できた場合だけ、変化のない厳密なETagを`If-Match`へ指定して1回削除し、最大60秒の読戻しで404を確認します。応答不明・回収未確認は未完了として残します。guestの700/600証跡は保持し、取得の都合で一括削除しません。

helperの既定動作は、条件付きDELETEの契約を確認できないcommandを保持します。実行枠の解放等で別に明示された通常DELETEによる退役は、この既定の自動処理と分けます。同じ環境の共有operation lockで、再利用しないnonce、元のreceiptにあるsource・payload、現在の終端`Succeeded`とexit 0、保存した完全結果のhashを照合し、その作業が所有する必要最小限の完了済みcommandだけを対象にします。

この別退役は、文書化されたCompute Run Commands DELETEのAPI版`2024-07-01`を使う1回の要求と、その生応答の保護保存、同じcommand IDのGETで404を確認した場合だけ削除済みと扱います。結果不明なら再DELETEせず回収待ちを残します。通常DELETEの確認は`If-Match`やCASの保証を証明するものではなく、helper単体に自動退役が実装済みとは扱いません。入力・Core state・checkpoint、VM識別情報、guestと主チェックアウトの監査証跡は保持します。

## 確認状況

正規の配置先を使った30件の合成試験が全件成功し、失敗・エラー・スキップはありません。所有・設定・VM世代の変更、応答不明と再送拒否、内部の保護snapshot変更、partial result、manifest・chunk・gzipの不備、回収前の終端・ETag・結果変更、未確認回収を伴う取得継続、guestのlock・source・ファイル境界を検証しました。実VMのagent、既設clientの一致、MI認可・通信、Workspaceの実table・ログ保持・取得待機、Computeの条件付き回収は別の実環境確認です。

`auditVerified`は常にfalseです。ログ0件・遅延・欠測は監査保留として扱い、backend未到達・利用量0・照合成功へ読み替えません。保存結果を受入ケースと照合し、回収待ちも確認した後に、工程1・2の完了を別途判断します。
