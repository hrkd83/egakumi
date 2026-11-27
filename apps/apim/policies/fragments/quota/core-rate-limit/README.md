# 利用回数枠の計数

認証済みの購読または社員の利用回数枠を選び、入口で要求を数える部品です。AzureのフラグメントIDは`core-rate-limit`を維持し、同名XML・C#をこのフォルダーで管理します。生成時にC#をXMLへ組み込み、`gatewayRateDecision`を準備して有効な枠へ`rate-limit-by-key`を適用します。

## 入出力

| 入力・出力 | 用途 |
| --- | --- |
| `gatewayDecision` | 認証済みの呼出者・認証方式と確認済み社員ID |
| `gatewaySubscriptionProfile` | 先行処理が確認した利用枠設定とrequestRateの読取り |
| `gatewayRateLimitExempt` | 既存の取消・切断等の計数免除 |
| `context.Subscription.Id`・生成済み回数設定 | 共通認証済み購読の利用枠選択 |
| `gatewayRateDecision` | scope・enabled・calls・renewalPeriodSeconds・counterKey |
| `gatewayRateRetryAfter` | 超過時の既存応答処理へ渡す待機時間 |

## MOD-49の変更

社員情報JSONの再読取りと本人IDの再照合を省き、`gatewayDecision.verifiedTenantId`と`verifiedPrincipalId`から従来と同じ`tid:oid`を作ります。計数キーは`ai-gateway-rate-v3:user:`に`Base64(UTF8(tid:oid))`を連結する既存形式です。社員の利用枠とカウンターの帰属先を維持します。

## MOD-50の変更

計数部品内の社員認証成功印の再確認1行だけを撤去します。成功印は共通認証・認証結果の引渡し・計数直前の社員profileで確認済みです。不正Bearerの先行拒否と、以下の残る計数条件を維持します。成功印を直接読む入力はこの部品から削除しました。

## MOD-51の変更

計数部品内のallowed・callerId非空の再確認1行を撤去します。共通の本人確認、HTTP／Protocolの引渡し失敗500停止、直前の社員profileを維持します。キー側のmethod・購読ID・caller一致の確認は残します。

## MOD-52の変更

社員profile全体の`DeepEquals`と、requestRateの必須・型・範囲の再検査を撤去し、直前のUserProfileへ集約します。先行する共通認証・UserProfileの同じ不備拒否を維持し、追加API・計数免除でもUserProfileを省略しません。計数に必要なprofile／requestRateの読取りと値適用は維持します。

## MOD-53の変更

ローカル実装・関連検証済みです。キー側の認証方式・購読ID非空・caller一致の再確認3条件を撤去し、共通認証済みの`context.Subscription.Id`を直接使います。キー用設定のBase64復元・JObject解析を社員側のreturn後へ移し、社員要求では復元しません。共通認証・引渡しによる不正キー拒否と失敗時の停止は維持します。MOD-49～52の維持条件と検証結果は各変更時点の記録です。

## MOD-54の変更

ローカル実装・関連検証済みです。キー側のprofile存在確認、有効時の欠落throw、空JObjectへのfallback、requestRateの存在再確認を撤去し、準備済みprofileを直接読みます。先行subscription-profileが全入口で回数枠を含む初期値を用意し、SQL不備は503で停止します。社員側もUserProfileの不備停止を維持します。MOD-53の66件は今回と別の検証記録です。

## MOD-55の変更

ローカル実装・関連検証済みです。SQL有効かつrequestRateがObjectのキー要求は`values = profileRate`として、埋込みBase64設定の復元とoverride探索を省きます。それ以外は従来の設定選択を維持し、制限なし時を含む内部calls／periodも同値を保ちます。選択結果はC#内で持ち、新しい公開設定やAPIM変数フラグは追加しません。profile切替・優先順位・明示null無制限・counter・免除・先行不備停止を維持します。MOD-54の73件は今回と別の検証記録です。

## 維持する処理と残件

- 確認済みprofile／requestRateの読取りと、回数・期間の値適用。
- nullによる制限なしと、既存の計数免除。
- キー側のprofileEnabled切替とoverride／SQL profileの優先順位、計数キー・回数・期間と超過応答の扱い。

回数枠のInbound棚卸・ローカル検証はここで一区切りです。読取りレビューでも、この部品に未判断の独自拒否・設定再検査は残っていません。残る枠選択・Object／null分岐・免除・APIM標準ポリシーへの引渡しは必要な処理として維持します。現在は[トークン枠](../core-token-limits/README.md)もMOD-61で、[社員利用枠の取得](../core-entra-user-profile/README.md)もMOD-65でローカルInbound棚卸を一区切りにしています。社員枠の設定検査は先行共通認証へ集約済みです。SQL利用枠・CM等と工程1全体は未完了で、APIM標準429・on-error・実分散カウンターの動作とAzure反映は未確認です。

MOD-49の59件（19.435s）、MOD-50の65件（15.836s）、MOD-51の65件（21.645s）は各変更時点の別実行で、全件成功・失敗・スキップなしです。MOD-52も別実行の関連65件が全件成功しました（18.122s、失敗・スキップなし）。MOD-52までは実Bicepコンパイル・生成一致を含むローカル実装・関連検証済みで、計数式の設定再読取り0とUserProfileの免除時を含む不備拒否を確認しました。MOD-53も別実行の関連66件が全件成功しました（15.960s、失敗・スキップなし）。社員要求のキー設定非復元、共通認証の不正キー・空／空白購読IDの401と後段未実行、counter・override／SQL profile・明示null・免除の維持を確認し、実Bicepコンパイル・生成一致も成功しています。MOD-54も別実行の関連73件が全件成功しました（17.597s、失敗・スキップなし）。SQL契約不備503とrate未実行、正規null無制限・既存counter、全入口のprofile準備と生成一致を確認し、実Bicepコンパイルも成功しています。MOD-55も別実行の関連74件が全件成功しました（20.304s、失敗・スキップなし）。SQL Object時の埋込み設定非復元と免除時のenabledだけの変更、SQL null・設定無効・通常免除の内部calls／periodとcounter保持、先行拒否を確認し、実Bicepコンパイル・生成一致も成功しています。Azure未反映・実カウンター未確認です。
