# 標準Entraトークン検証の失敗処理

このフォルダは、Azureポータルのポリシーフラグメント `core-entra-authentication-error` の正本を管理する。同名XMLだけで処理を定義できるため、空のC#ファイルは作らない。

共通認証の `validate-azure-ad-token` が失敗すると、通常のinbound処理を中断して、その操作に適用される `on-error` へ移る。Globalでは共通の要求IDヘッダー設定の後から呼び出す。独自のon-errorを持つ対象操作では、個別エラー処理より先に同じ部品を呼ぶ。

```text
STEP4の validate-azure-ad-token が失敗
    ↓ 共通 on-error
apim-request-id を設定
    ↓ core-entra-authentication-error
LastError.PolicyId が core-entra-token-validation か確認
    ├─ 一致：認証拒否を監査へ記録し、401・Bearer案内・no-storeを返す
    └─ 不一致：この部品では応答を終了せず、後続のエラー処理へ進む
```

監査は、`fragments/audit/core-gateway-telemetry/` のXMLとC#を生成時に再利用して本文へ展開する。別のフラグメントを実行時に入れ子で呼び出す構成ではない。共有式の詳しい説明は `core-gateway-telemetry` にあり、ここでは正本への案内と既存コメントを保持する。

失敗したトークンから本人を推測せず、アクセストークンや例外の詳細を応答・監査へ載せない。会社設定の不備や内部処理による500の分類は、`core-common-authentication` 側が担当する。このフラグメントは標準Entraトークン検証の失敗だけを扱う。

生成・完全照合は `scripts/deployment/helpers/apim_common_authentication.py`、IaCへの登録は `apps/gateway/apim/api-publication.bicep` が担当する。読戻しでは、共通認証本文・呼出位置・標準検証とこの失敗処理の同時存在も確認する。

## MOD-79：個別操作の共通トークン検証失敗

承認済み・ローカル実装と関連検証を終えました。Document開始／結果取得、PII、CMのstart／status／logout／logout-all／disconnect／session-commitのon-error先頭へ、Entra認証が有効な場合だけ固定includeを生成します。Browserの4操作・API・Product・別フラグメントへ広げません。

LastError.PolicyIdがcore-entra-token-validationの場合だけ、既存のrejected／INVALID_AUTHENTICATION／401の監査を1回行い、要求ID・no-store・WWW-Authenticate: Bearer付きの本文なし応答で停止します。その他の個別エラー処理を全て継承するbaseは追加せず、購読・利用枠・タイムアウト・下流接続・CMセッションの既存分類を維持します。

PythonとBicepは同じ固定markerを有効時include／無効時空文字へ置換します。操作用の実式展開helperは、このerror部品の先頭1回だけを正本から展開し、Global用helperの固定順序検査を緩めません。公開と読戻しの検査も、信頼したHTTP API設定のpii／documentAnalysis／documentAnalysisStatusとCM保護6操作に限定し、Globalの順序・回数とvalidator／error部品の同時存在を維持します。Azure未反映・実APIM受入は未確認です。

## ローカル検証

| 実行 | 結果 |
|---|---|
| CM・Document・PIIの実式と契約 | 71件、8.757s、失敗なし。Document配備クラスはBicep未検出で1クラスskip（2メソッド） |
| Document配備クラスの補完 | 初回2件、2.629s、合成ARM評価のEntra既定値不足で2error。fixtureへ既定値を指定した後の再実行2件は全成功、2.616s |
| 配備・source・rate・容量 | 初回38件、23.418s、fixtureと期待供給の不足で1failure・4error。試験を揃えた最終38件は全成功、30.674s、skipなし |
| 追加scope・操作展開境界 | 初回16件は全成功、3.005s。CM親ポリシーの境界等を追加した最終18件も全成功、3.480s |
| 既存publication／historical | 31件全成功、0.658s。親側の別実行publication28件も全成功、0.518s |
| API設定・protocol読戻し・Razor | 56件、2.849s、失敗なし・Bicep未検出の生成試験1skip。その1件の補完は全成功、6.667s |

異なる実行を一回の成功へまとめません。スキップした配備生成3メソッドは、確認済みBicepで別途実行して成功しました。試験の合成ARM評価に不足していた既定値を補ったもので、配備ソースの条件は緩めていません。

実式は9操作について、共通PolicyId一致時の401・監査1回・個別処理の非到達と、同じSourceの別ID／null／空ID、購読・timeout・rate・接続・式エラーの既存応答を比較しました。PII固有の別validatorは従来の分類を維持します。生成は有効／無効、先頭includeの1回／0回、Python・実Bicep・保護source一致、Browser・API集合の不変、条件付き依存と429順序を確認しました。

scopeは対象外のAPI・Product・Browser・inbound・別fragment、重複・先頭以外・属性変造・部品不足・Global変造、操作制約の型と操作名、CMの偽親と型違いを拒否しました。以前のoperation includeがないsourceは引き続き受け付け、Globalの固定順序・回数と標準validator／error部品の同時存在は維持します。

合成CM＋Entra構成の展開サイズは配備2,084,967bytes、source1,991,466bytesで2,800,000bytesの予算内です。このerrorフラグメントは13,813bytesで32,768bytes未満。CM本人セッション部品の余裕170bytesはMOD-78から変わりません。これはローカル生成検査で、Azureの受入ではありません。

独立レビューに指摘はありません。9操作の挿入以外のXML、正確なPolicyId判定、無効時の非参照、配備／sourceの同一rendererと依存、Browser／API／別fragmentの非追加、Global用展開条件の維持を確認しました。対象25ファイルの公開情報検査と差分書式も成功しました。工程1／2全体は未完了、Azure未反映です。
