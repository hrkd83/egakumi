# 利用資格・APIキー・本人接続プールを保守する

[共通確認](common.md) · [Portal設定](../setup/portal.md) · [CMプール設計](../reference/credential-pool-design.md)

構築済み環境で行う設定変更、キーの更新、購読の同期、CMプール増設と定期点検を扱う。変更前の値と対象、利用先、戻し方を[共通確認](common.md)で記録してから実施する。実情報・秘密・実測結果はチェックアウト直下の`.local/<environment>/`内で保護し、公開文書や通常ログへ転記しない。

| 作業 | 操作する担当と権限 |
| --- | --- |
| Entraのグループ所属変更 | グループ所有者・Entra管理者等、対象グループの所属を管理できる担当 |
| Portalの対象条件・管理グループ設定 | Portal管理担当が変更内容を決め、対象環境の配備権限を持つ担当がPで反映 |
| APIキーの主副更新 | 対象APIMの購読更新権限を持つAzure運用担当と、利用アプリの設定担当 |
| キーの停止・再開 | 現在の所有グループのMemberである一般利用者。Portal管理者という理由だけで代行しない |
| 購読の同期 | `portalApiKeys.platformGroupId`の現在のMemberであるPortal管理者 |
| CM増設・ジョブ点検 | Gの配備権限を持つAzure運用担当。点検だけなら対象ジョブ・ログの読取権限 |

Portalの管理グループ所属、Azure RBAC、Entraの管理権限は別である。P／Gは既存の個別配備入口であり、設定変更でもDB・権限・API・WAF等を照合する。基盤の`azd up`・`azd provision`を通常保守の入口にしない。

## 1. 所属・Portalの対象条件・管理グループを変更する

### 変更する設定を分ける

| 変更したいこと | 正本と反映方法 |
| --- | --- |
| 同じ所有グループ・管理グループの担当者交代 | EntraのMember所属を変更する。グループIDが同じなら、そのためのPは不要 |
| 通常のキー所有候補になるグループ名の条件 | 同じ環境のparamsの`portalApiKeys.groupNamePattern`を変更しPで反映。Securityグループ・現在のMember所属は引き続き必須 |
| Portalの管理グループ自体を変更 | `portalApiKeys.platformGroupId`と、そのIDを含む`portalApiKeys.groups`の承認設定を揃えPで反映 |
| 明示承認した検証用グループの製品・発行枠 | `portalApiKeys.groups[].products[]`の`productId`・`maxSubscriptions`を変更しPのSQL初期化で反映。製品はAPIMに準備済みのものを使う |

1. 新旧の担当者・グループのObject ID、Security属性、Member所属、影響するキーを確認する。担当者交代だけなら既存グループを維持し、新担当の操作確認後に旧担当の所属を回収する。
2. 対象条件を変える場合は、現在と変更後の正規表現、それぞれで一致するグループを確認する。RE2互換・最大512文字であり、空欄や不正構文を全許可として使わない。
3. 管理グループIDを変える場合は、新グループを承認配列へ用意し、旧グループのキーと検証用購読を先に点検する。管理グループIDの変更は既存キーの所有者移管ではない。既存の検証用購読IDを新グループへ自動移管する処理もないため、その所有を保持する承認設定を不用意に取り除かない。
4. 同じ保護入力でPの計画を読み、目的外の変更がなければ実行する。

```sh
python3 -B scripts/operations/release.py --config "$DEPLOY_PARAMS_FILE" --component portal
python3 -B scripts/operations/release.py --config "$DEPLOY_PARAMS_FILE" --component portal --execute
```

5. 今回の終了コード・`state/portal-deployment.json`を確認し、新担当のPortal管理操作、所有候補、対象グループのキー操作を確認する。旧担当の所属を外した場合は、更新した画面から権限喪失も確認する。コピー済みAPIキーは所属変更だけでは失効しないため、必要な値の更新は次節で行う。
6. 戻す場合は今回変更した所属・paramsだけを旧値へ戻す。params変更はPで再反映する。途中で発行・移管・削除したキーまで設定復帰で戻ったとは扱わない。

**設定範囲：** `groups`は検証用購読の明示承認であり、通常利用者のグループ全件を列挙する欄ではない。通常発行は現行資材で`released-apis`に固定され、通常カタログの発行枠は`approved_product_catalog()`が5件を生成する。通常製品や全グループの枠を自由変更するPortal画面・params項目はない。カタログ値の変更だけで、既存グループの登録済み枠が一括更新されるわけでもない。発行枠は主副2値を持つ購読1件として数え、作成中・停止中・削除中も削除確定まで含む。呼出回数・トークン制限とは区別する。

## 2. 主副キーを段階更新し、必要なら停止・再開する

Portalには主副キーの再生成操作はない。Azure運用担当が`api_key.py`からAPIMの[主キー再生成](https://learn.microsoft.com/en-us/rest/api/apimanagement/subscription/regenerate-primary-key?view=rest-apimanagement-2024-05-01)・[副キー再生成](https://learn.microsoft.com/en-us/rest/api/apimanagement/subscription/regenerate-secondary-key?view=rest-apimanagement-2024-05-01)を使う。対象は構築記録と一致するAPIM内の、`portal-<UUID>`形式・`released-apis`製品スコープ・activeまたはsuspendedの通常購読に限定する。`master`・全APIスコープ・検証用製品の購読は受け付けない。SQLの所有者・状態・権限は変更せず、Portal利用者の代行APIとしても使わない。

主キーの例は次のとおり。指定するのは購読ID・片側の名前・出力先であり、秘密値を引数へ渡さない。計画はAzure通信をせず、入力と出力先を確認する。実行時に現物のAPIM所有範囲と購読の製品・状態を読み直す。

```sh
python3 -B scripts/operations/api_key.py --config "$DEPLOY_PARAMS_FILE" \
  --subscription-id '<PORTAL_SUBSCRIPTION_ID>' --key primary \
  --secret-output "$DEPLOY_LOCAL_DIRECTORY/secrets/<NEW_PRIMARY_KEY_FILE>"
python3 -B scripts/operations/api_key.py --config "$DEPLOY_PARAMS_FILE" \
  --subscription-id '<PORTAL_SUBSCRIPTION_ID>' --key primary \
  --secret-output "$DEPLOY_LOCAL_DIRECTORY/secrets/<NEW_PRIMARY_KEY_FILE>" --execute
```

出力は同じ環境の`secrets/`内の新規ファイルで、親700・ファイル600、本文は選んだ側のキー値だけとなる。既存ファイルは上書きしない。副キーは`--key secondary`と別の新規保存先を使う。計画・結果の端末出力にキー値は含めず、作業段階を`state/operations/`へ記録する。

1. 対象購読を使うアプリと秘密の保管・読込方法、現在利用中の主／副を確認する。キー値は保護した入力先だけで扱い、コマンド引数・作業票・画像へ貼らない。
2. 主キーを使っている場合は、まず利用アプリを現在の副キーへ切り替え、通常の認証付き要求が成功することを確認する。副キーを使う利用先がある場合も含め、各利用先の切替状況を把握する。
3. 上記スクリプトで主キーだけを再生成する。保存された新しい主キーを正規の秘密管理先へ反映し、利用アプリを切り替えて正常要求を確認する。旧主キーの要求は拒否され、副キーは引き続き使えることを確認する。
4. 全利用先が新しい主キーへ移った後、副キーも更新する必要があれば`--key secondary`で再生成して別の新規ファイルへ保護保存する。主副を同時に失効させず、利用できる片方を残して進める。
5. 購読ID・製品・状態・利用制限が意図せず変わっていないことを確認する。再生成済みの旧値は復元できない。切替に問題があれば、まだ有効なもう片方へ戻して調査し、再生成を機械的に繰り返さない。

再生成POSTと、その後の秘密読取POSTはそれぞれ1回だけである。変更前に購読・片側へ束縛したintentを`state/operations/`へ保存し、未解決なら、出力先を変えてCLIを起動し直しても通常の再生成を拒否する。`regenerated-not-saved`は再生成成功後の読取・保存未確認、`saved-receipt-unconfirmed`は秘密ファイル保存後の記録未確認を表す。まず保護記録と出力ファイルの有無を確認する。

未解決で秘密ファイルがない場合は、同じ対象の**現在値だけ**を別の新規ファイルへ回収する。次の`--recover-current`は同じ所有範囲・購読の検査後、秘密読取POSTを1回行い、再生成POSTは送らない。通常と同じく、既定は計画、`--execute`で取得・保存する。

```sh
python3 -B scripts/operations/api_key.py --config "$DEPLOY_PARAMS_FILE" \
  --subscription-id '<PORTAL_SUBSCRIPTION_ID>' --key primary --recover-current \
  --secret-output "$DEPLOY_LOCAL_DIRECTORY/secrets/<RECOVERED_PRIMARY_KEY_FILE>"
python3 -B scripts/operations/api_key.py --config "$DEPLOY_PARAMS_FILE" \
  --subscription-id '<PORTAL_SUBSCRIPTION_ID>' --key primary --recover-current \
  --secret-output "$DEPLOY_LOCAL_DIRECTORY/secrets/<RECOVERED_PRIMARY_KEY_FILE>" --execute
```

保存成功時は`current-key-recovered`を記録する。これは現在値の回収であり、元の再生成が成功した証拠にはしない。回収値を正規の利用先へ反映し、実際の認証付き要求で利用を確認する。回収後の次回更新は、別の明示した通常操作として行う。intentを削除して不明な再生成をやり直す手順にはしない。

### Portalでの停止・再開

所有グループの現在のMemberとしてキー一覧から対象詳細を開く。「利用を停止」後、同じ購読の主副キーによる新しい要求が拒否されることを確認する。再開は「利用を再開」で行い、許可範囲の通常要求を確認する。キー値の更新や購読の削除は不要である。

停止は進行中処理の取消保証ではない。APIMだけで状態を変えるとSQLとの不一致になり、後の購読同期で再有効化され得るため、通常の停止はPortalで台帳とAPIMを揃える。「棚卸済みにする」は用途・所有者・利用状態の確認日時の記録であり、停止・再生成ではない。

## 3. SQL台帳を正本として購読を同期する

1. Portal管理者として**システム管理者 → 購読の同期**（`/<言語>/subscription-sync`）を開く。接続先が意図したAzure SQLとAPIMであることを確認し、「一覧を更新」する。ローカルSQLの画面は反映不可である。
2. 各行のSQL状態・APIM状態と処理内容を読む。既存購読の用途名・製品・状態をSQLへ合わせる更新、欠損購読の再作成、SQL削除済み・SQL未登録購読の削除を区別する。`master`と処理途中の行は対象外である。
3. 今回直す対象を最大10件選び、「同期内容を確認」へ進む。特にactiveへの再有効化、削除、再作成時の新しいキーと利用アプリへの配布を確認する。確認内容は5分で期限切れとなり、並行変更時も再照合が必要になる。
4. 確認欄を選び「同期を実行」する。各行の「完了」「削除完了待ち」「失敗」を区別し、一覧を再取得して差分と通常利用を確認する。応答不明時は一覧から再照合し、成功済みの操作を推測して繰り返さない。
5. 再作成したキーは所有者の正規操作で取得・配布する。同期画面はキー値を取得・表示しない。既存キーのメタデータ更新と、新キーになる再作成を同じ復旧として扱わない。

同期はEntra所属・ライセンス・API定義・CM接続の同期ではない。APIM側の手動変更を正本としてSQLへ取り込む処理でもない。不要な削除や再作成を実行すると以前のキー値へは戻せないため、限定したプレビューの確認を省略しない。

## 4. CMプールを増設し、定期ジョブを点検する

### 既存プロファイルのプール増設

1. 同じ環境のparamsの対象`credentialProviders[]`と`pool.count`・`pool.capacity`、既存の物理provider名、SQLプールの容量・割当数を確認する。現在の入力範囲は個数1～26・各容量1～8,000である。保管枠と同時処理性能は別である。
2. 必要数に合わせて`pool.count`だけを増やす。論理プロファイル名、既存の接尾辞付き物理名、容量、Client ID・secret参照、APIM識別子は維持する。通常の増設でEntraアプリや戻り先を作り直さない。
3. Gの計画で追加先と既存資源の保持を確認し、同じ入力で実行する。Gateway一式の照合を伴い、CMだけの専用配備ではない。

```sh
python3 -B scripts/operations/release.py --config "$DEPLOY_PARAMS_FILE" --component gateway
python3 -B scripts/operations/release.py --config "$DEPLOY_PARAMS_FILE" --component gateway --execute
```

4. 今回の終了コード・`state/gateway-deployment.json`とSQL初期化ジョブの成功を確認する。APIMの追加provider・必要なアクセス権、SQLの追加プール行、Brokerの許可先が揃い、既存本人の接続ID・割当数と代表的な本人API利用が保持されていることを確認する。
5. 新規割当先は各プールの負荷率等で選ばれるため、増設先を選ばせる目的で既存プールを埋めない。追加先に接続・予約ができた後は、`count`を元へ減らすだけの切戻しをしない。追加先を保持して調査し、縮小や既存接続の修復をこの増設作業へ混ぜない。

### 既存の毎時照合ジョブを点検する

Azure Portalの**Container Apps Jobs**で対象環境の`caj-<stem>-recon`を選び、配備記録と完全な資源IDを照合する。現在のスケジュールは`17 * * * *`、[Container AppsのcronはUTC基準](https://learn.microsoft.com/en-us/azure/container-apps/jobs#scheduled-jobs)である。Brokerと同じ採用イメージ・実行MI・許可pool設定と、直近の実行結果を確認する。スキーマ初期化の手動ジョブと取り違えない。

通常は既存の定期実行を観測する。実行中に重ねて開始せず、成功／失敗と、対象実行の集約件数・残件を確認する。同じジョブは60日未使用・解除待ち接続の回収も行い、ログインや有効セッションがあるだけでは未使用期間を延長しない。CM不存在とSQLの同一世代確認前に枠を手動で減らさない。

ログは対象実行と狭い時間範囲で読む。到達待ち・取得不能を処理失敗と即断せず、ジョブ成功だけでも対象接続の削除完了と判断しない。件数増加や確認不能が続く場合は対象と時刻を保護記録へ残して調査へ引き継ぐ。この通常点検のために利用日時変更・削除故障・競合を注入する必要はない。
