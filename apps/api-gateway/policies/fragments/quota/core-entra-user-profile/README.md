# 社員利用枠のprofile準備

共通認証が確認した社員の利用枠を、後段の回数枠・トークン枠へ渡す部品です。user JSON解析、limitsのJObject取得、Formatting.NoneでのJSON返却の3処理を担います。初回棚卸で正本をこのフォルダーの同名XML・C#へ移し、AzureのフラグメントIDは`core-entra-user-profile`を維持します。生成時にC#式をXMLの`gatewaySubscriptionProfile`へ組み込みます。

## 入出力

| 入力・出力 | 用途 |
| --- | --- |
| `gatewayUserAuthentication` | 共通認証が保持した社員利用枠limits |
| `gatewaySubscriptionProfile` | 確認したrequestRate・tokensPerMinute・tokenQuotaのJSON |

## MOD-62の変更

ローカル実装・関連検証済みです。先頭のallowedと`gatewayEntraAuthenticationValidated`の存在・trueの再確認だけを撤去し、先行共通認証・認証結果の引渡しによる停止を維持します。本人IDと利用枠の残る条件は今回変更しません。

## MOD-63の変更

ローカル実装・関連検証済みです。callerId・allocationIdの再照合と、そのためだけの`gatewayDecision`のJSON解析を撤去します。社員user JSONの読取りは利用枠に必要なため維持し、本人確認と引渡しは先行共通認証へ任せます。配置とAzure IDはMOD-62から変更しません。MOD-62のID一致維持と121件（36.106s）は変更前の別記録です。

## MOD-64の変更

ローカル実装・関連検証済みです。元の`gatewayEntraUserLimits`の存在確認・JSON再解析・`DeepEquals`だけを撤去し、先行共通認証の元設定検査と不備500停止を維持します。user JSONからのlimits取得・返却、limitsの非null・3項目、必須・型・範囲・期間・明示nullとSQL側は維持します。MOD-62／63の元設定一致確認と検証結果は変更前の別記録です。

## MOD-65の変更

ローカル実装・関連検証済みです。残る構造・3項目限定・必須・型・範囲・期間の検査と、そのためのlocalsを撤去します。先行共通認証が同じ設定を全検査し、不備500で停止する処理は維持します。必要なuser JSON解析→limitsのJObject取得→Formatting.Noneでの返却だけを残します。読取り不能・missing・null・nonObjectは補完やcatchを追加せず、既存on-errorへ渡します。

SQL側・明示null・counter・免除、Azure IDと配置は変更しません。関連125件が全件成功しました（29.737s、失敗・スキップなし）。人工的な設定12対照は単独profileの返却後に通常profileへ復元し、同じ不備を正式共通認証へ渡すと500・INVALID_GATEWAY_CONFIGURATIONで後段未実行となることを確認しました。取得失敗7対照の例外伝播・補完なし、先行認証停止・変数非アクセス・既存枠条件と実Bicepコンパイル・生成一致等も成功しています。MOD-62～64の検査維持と各検証結果は当時の別実行です。

## 維持する処理と残件

- user JSON解析・limitsのJObject取得・Formatting.Noneでの返却。
- 先行共通認証による設定全検査と不備500停止、内部読取り不備の既存on-error。
- SQL側・明示null・counter・免除と既存のID・配置。

この部品のローカルInbound棚卸はここで一区切りです。供給の3処理は必要な処理として維持し、未判断の独自拒否は残っていません。工程1全体とSQL側の棚卸は未完了、Azure未反映・実native動作／on-error応答分類は未確認です。

MOD-62～64は各変更時点の履歴です。MOD-62の関連121件が全件成功しました（36.106s、失敗・スキップなし）。実C#の孤立3対照と成功印非アクセス、全profile呼出しの非アクセス、正式401・引渡し500と残るID・元limits・型・範囲不備の停止を確認しました。実Bicepコンパイル・Python／Bicep生成一致・同名所有・Razor対象・説明保持、既存counter・override・診断も成功しています。MOD-63も別実行の関連122件が全件成功しました（28.680s、失敗・スキップなし）。decisionをprofile直前に変更して直後に復元する孤立4対照で非アクセスと同じprofile・rate・tokenKey・監査本人を確認し、全profile呼出しの非アクセス・先行停止・残る利用枠条件と実Bicepコンパイル・生成一致等も成功しています。MOD-64も別実行の関連123件が全件成功しました（25.556s、失敗・スキップなし）。元設定を直前に変更・直後に復元する成功4対照の非アクセスと同じcounter・tokenKey・監査本人、構造6対照と既存型・範囲不備の停止、共通認証・引渡しの先行停止を確認し、実Bicepコンパイル・生成一致等も成功しています。トークン部品のMOD-61の88件（31.368s）は別部品・別時点の結果です。
