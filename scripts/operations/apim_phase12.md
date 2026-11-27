# 工程1・2のAPI反映資材

[apim_phase12.py](apim_phase12.py)は、工程1・2のまとめ反映について、実行内容の全文を確認できる入口である。保護領域に置くのは入力・状態・生成テンプレート・通信証跡であり、このソースには実環境の値を含めない。

この入口は既存の反映用runnerと同じ処理を使う。新しい認証方式、権限、API、モデルをここで追加するものではない。既存の通常API公開処理`execute_plan(..., reconcile_existing_source=False)`へ、検査済みの計画とテンプレートを渡す。

## 実行対象と保持する境界

- 現在のソースから生成したAPI公開計画、APIMのAPI・操作・接続先・schema・MCP tools・ポリシー・共通認証・Named Value・Product・宣言済み購読・公開source世代を対象とする。CMの既存ブラウザー入口も同じ公開範囲に含まれる。
- 既存のAPI公開処理に付随する、APIM下流宛先用FirewallのFQDN、利用枠接続設定の初期化、APIM診断・logger、公開説明、未リリースAPIのProduct所属整理、転用されていない既定サンプルの整理も対象に含まれる。Firewallは既存のAPIM用ルールを所有・ETag・読戻しで確認し、その他のルールを保全する。
- 既存の所有source・認証・policy graphを確認し、ETagと読戻し、閉じた準備段階を使う。所有不明や不一致を補完して進めない。
- Portalアプリの切替、Broker・SQL配備、OBO実装、Entraアプリ変更・RBAC付与・証明書やsecretの作成・APIキー再生成、WAF反映をこの入口では実行しない。WAFは別のGateway限定工程である。
- 明示した主チェックアウトの入力・Core state・source checkpointを使い、同じ環境のoperation lockを取得する。保存先の探索、入力のコピー、Core stateの上書きは行わない。
- 更新要求の意図・応答、閉じた段階のcheckpoint、最終読戻しを同じ保護領域へ保存する。途中checkpointだけで反映完了と扱わない。
- 完了時に全policy graph、正確な社員Product所属、browser source、入力・Core stateの不変を照合する。実接続の受入は別工程である。
- 失敗時は停止して証跡を確認する。変更要求を自動再送しない。

更新の実体は[通常API公開処理](../../apps/apim/deploy/api_publication.py)と[API公開Bicep](../../apps/apim/api-publication.bicep)で確認する。共通認証・Productの切替と復旧は[Entra公開処理](../../apps/apim/deploy/entra_publication.py)へ引き継ぐ。生成専用のARM配備は、最終API反映とは別の変更要求として記録する。

## 計画確認と反映の区切り

次の指定は、利用者が選んだチェックアウト・保護入力・公式Bicep実行ファイルを表す。実際の値は保護した環境設定で管理する。

| 環境変数 | 用途 |
| --- | --- |
| `GW_PHASE12_SOURCE_ROOT` | 実行するコードのチェックアウト |
| `GW_PHASE12_RUNTIME_ROOT` | 利用者が明示した主チェックアウト |
| `GW_PHASE12_CONFIG_FILE` | その環境の保護された構築入力 |
| `BICEP_CLI` | 公式Bicep実行ファイル |

```sh
python3 -I -B scripts/operations/apim_phase12.py
```

`--apply`を付けない確認は、保護入力・既存state・source checkpointの照合、現在のAPI計画、公式Bicepコンパイル、64 UDF上限の検査までで停止する。Azure通信・更新は行わず、計画と検査結果だけを保護領域へ保存する。

Azure反映には`--apply`の明示と利用者の再開指示が必要である。実行時の自動承認レビューは別途行われる。計画確認が通ったことを、将来の反映コマンドが承認済みであることや、実環境検証済みであることと同一視しない。

## 記録済みの閉鎖段階が失敗した場合

社員Productの所属を閉じている途中で管理APIの読取りに失敗した場合も、停止記録を確認してから再開する。例えば`ManagementApiRequestFailed`・422は管理エンドポイントへの接続失敗を含むため、ポリシー式の不備と決めつけず、同じ所有APIMの現在の管理読取りを確認する。

API配備へのPUT、staged checkpoint、完了記録がまだない場合は、失敗したAPI配備の復旧指定を使わない。入力・Core state・反映済みcheckpoint・停止時の反映ソースの不変を確認し、保存済み旧sourceに対する全policy graph・認証・browser・native Productと、既知の社員APIだけからなる途中所属をGETで厳密照合する。新しい読戻しから旧期待値を作らず、未知の所属や構成差分があれば停止する。

この読戻しが通った後は、通常の`--apply`で既知の途中所属から処理を進める。前回の要求をそのまま再送せず、既に閉じた所属を再び閉じる処理へ戻さない。停止記録と再開前の証跡を保護保存し、最終の全graph・正確な再公開所属・checkpointの読戻しまで完了扱いにしない。

API増分配備が失敗すると、一部の子資源だけが更新済みになる場合がある。通常処理は旧sourceとの不一致を拒否する。この拒否と、失敗配備の生成元をcurrent checkpointへ採用しない条件は維持する。

明示的な復旧には、選択した主チェックアウトの保護された`evidence/`内にある、停止した反映記録を指定する。

```sh
python3 -I -B scripts/operations/apim_phase12.py --apply --recover-failed-stage-evidence <PROTECTED_FAILED_RUN_EVIDENCE>
```

この指定は自動再送ではない。実行前に停止記録を確認し、入力・Core state・旧checkpointの不変と、修正内容を検証する。入口は同じoperation lock内で、旧要求前の全JSON snapshot、記録済みの完全テンプレート、APIへのPUT意図と対応する受付応答を読む。新しい記録には要求前の元ファイルのSHA256も保存し、後の復旧で元のバイト列を照合する。旧runnerの記録に元ファイルのハッシュがない場合は、保護された開始記録のハッシュによる別の照合も必要であり、整形済みJSONから元のバイト列を推定しない。

同じ停止記録には`native-product-before.json`が必要である。反映前に完全照合した既存graphの記録から、native Productの要求先・properties・policy・policyの既知の不存在を取り出し、その由来とハッシュを保護stateで確認する。現在の実読戻しからこの旧値を作らない。復旧の前後と最終読戻しで、Productの旧properties・policyが維持されていることを確認する。先頭の`base`だけを根拠に、記録にないpolicyを採用しない。

Azure上では、同じ所有者の終了済みFailed配備、全操作の既知の対象・終了状態、保存されたコード・全入力、資源を変更しない成功済み生成要求、APIM本人IDと世代IDの一致を独立して読む。旧Global、空の社員Product、native Product、API全件・操作・接続先・schema・tools・browserの範囲は保全する。途中資材の期待値は、旧sourceまたは記録済みの要求sourceの既知値からだけ選び、実読戻しのXMLを新しいsourceとして採用しない。旧状態で存在しなかった生成資源だけ、既知の未作成状態を認める。

全snapshot・不存在・ETagと失敗配備の状態を変更直前にも確認してから、修正済みの通常API増分配備を閉鎖状態で実行する。全て新しいsourceと一致する読戻し後にだけcheckpoint、Global、最後の社員Product所属へ進む。既存sourceの全体再調整やGateway全配備へ切り替えない。

既存のMCP toolが1件だけ欠落している場合も、通常の不存在・全件一覧の検査は拒否を維持する。別の明示指定`--restore-recorded-mcp-tool`を併用した場合だけ、旧・失敗要求・修正済みsourceの同じtoolと親APIの完全一致、記録済みの親API更新成功、tool操作の未実行、現在の完全な空一覧と対象toolへの直接404を確認する。その他のtool、schema、操作の欠落や未知の要素は許可しない。初回の閉鎖段階の実読取りだけがこの既知の欠落を記録し、成功した応答を仮定しない。修正済みの増分配備後は通常の全件一覧・tool本体の検査へ戻り、toolが実在して一致するまでcheckpointや再公開へ進まない。欠落した理由を、未確認のままAPI更新や依存関係の挙動へ断定しない。

`--apply`なしでは、復旧記録のローカル照合までで停止し、Azureの記録や実資源の一致を確認済みとは扱わない。[記録済み要求の読取り](../../apps/apim/deploy/api_publication.py)と[既知の途中資材の読取り](../../apps/apim/deploy/entra_publication.py)は、GET・exportだけで実行でき、Azure更新やcheckpoint保存を行わない。

## 過去ポリシーとPIIの500応答定義を移行する順序

過去のpolicy配置を移行するときは、既存APIの非policy構成との完全一致を維持する。PIIの500応答定義の追加が同時に必要な場合は、次の順で行う。

1. 選択した保護stateに、両段階を未完了として記録する。既存sourceと最終ソースの確認情報を保持する。
2. PIIの応答定義を従来の形に保ち、現在のポリシーへ移行する。全graphの読戻しとcurrent checkpointを確立する。
3. PIIの標準エラー一覧へ500を戻して、通常API公開処理で反映する。500以外の既存応答行を保全する。
4. AzureのPII操作に500の応答定義があり、current checkpointと一致することを読戻しで確認し、2段階目を完了として記録する。

**2段階目の反映・読戻しが完了するまで、工程1・2を完了と扱わない。** 再開時も、選択環境の`state/phase12-two-stage-migration.json`を読み、未完了の段階から確認する。1段階目の成功だけで旧形式の撤去や完了報告へ進まない。
